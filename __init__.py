"""Crosmos memory provider plugin for Hermes Agent."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from agent.memory_provider import MemoryProvider


_pkg = __name__.rpartition(".")[0]
if _pkg and _pkg not in sys.modules:
    sys.modules[_pkg] = types.ModuleType(_pkg)

from . import schemas, tools  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.crosmos.dev/api/v1"
_API_TIMEOUT = 30.0
_MIN_USER_LEN = 10
_MIN_ASSISTANT_LEN = 10

_CONTEXT_STRIP_RE = re.compile(
    r"<crosmos-context>[\s\S]*?</crosmos-context>\s*", re.IGNORECASE
)

_INJECTION_PATTERNS = re.compile(
    r"(?i)\b(ignore\s+(previous|above|prior|earlier|all)\s+(instructions?|prompts?|rules?|directions?))|"
    r"\b(forget\s+(everything|all|previous|above|prior))|"
    r"\b(you\s+are\s+now\b)|"
    r"\b(new\s+instructions?\s*[:=])|"
    r"\b(system\s*[:=]\s)",
)
_INJECTION_REPLACEMENT = "[instruction removed]"

_SKIP_RECALL_PREFIXES = (
    "recall ", "remember ", "search ", "search memory", "search your memory",
    "do you remember", "do you know about", "what do you know", "what do i",
    "can you recall", "can you remember", "can you search", "can you look up",
    "look up ", "check memory", "check your memory",
)
_SKIP_INGEST_PREFIXES = (
    "recall", "remember", "search", "forget", "look up",
    "check memory", "check your memory",
)


def _sanitize(text: str) -> str:
    return _INJECTION_PATTERNS.sub(_INJECTION_REPLACEMENT, text)


def _strip_own_context(text: str) -> str:
    """Remove <crosmos-context> blocks before ingest to prevent recall feedback loops."""
    return _CONTEXT_STRIP_RE.sub("", text or "").strip()


def _load_crosmos_config(hermes_home: str) -> dict:
    """Load $HERMES_HOME/crosmos.json (written by install.sh / save_config)."""
    config_path = Path(hermes_home) / "crosmos.json"
    if not config_path.exists():
        return {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        logger.debug("Failed to parse %s", config_path, exc_info=True)
        return {}


class CrosmosMemoryProvider(MemoryProvider):
    def __init__(self) -> None:
        self._api_key = ""
        self._base_url = _DEFAULT_BASE_URL
        self._space_name = ""
        self._space_id: str = ""
        self._client: Optional[httpx.Client] = None
        self._session_id = ""
        self._hermes_home = ""
        self._active = False
        self._write_enabled = True
        self._sync_thread: Optional[threading.Thread] = None
        self._write_thread: Optional[threading.Thread] = None
        self._space_id_cache: Dict[str, str] = {}

    @property
    def name(self) -> str:
        return "crosmos"

    def is_available(self) -> bool:
        return bool(os.environ.get("CROSMOS_API_KEY", ""))

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "api_key",
                "description": "Crosmos API key",
                "secret": True,
                "required": True,
                "env_var": "CROSMOS_API_KEY",
                "url": "https://console.crosmos.dev",
            },
            {
                "key": "space_name",
                "description": "Default memory space name",
                "default": "hermes-agent",
            },
            {
                "key": "base_url",
                "description": "Crosmos API base URL",
                "default": _DEFAULT_BASE_URL,
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "crosmos.json"
        existing: dict[str, Any] = {}
        if config_path.exists():
            try:
                raw = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    existing = raw
            except Exception:
                existing = {}

        sanitized = {k: v for k, v in (values or {}).items() if k != "api_key"}
        existing.update(sanitized)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    def initialize(self, session_id: str, **kwargs) -> None:
        from hermes_constants import get_hermes_home

        self._hermes_home = kwargs.get("hermes_home") or str(get_hermes_home())
        self._session_id = session_id

        config = _load_crosmos_config(self._hermes_home)
        self._api_key = os.environ.get("CROSMOS_API_KEY", "")
        self._base_url = (
            os.environ.get("CROSMOS_BASE_URL")
            or config.get("base_url")
            or _DEFAULT_BASE_URL
        )
        self._space_name = (
            os.environ.get("CROSMOS_SPACE_NAME")
            or config.get("space_name")
            or ""
        ).strip()

        agent_context = kwargs.get("agent_context", "")
        self._write_enabled = agent_context not in ("cron", "flush", "subagent")

        self._active = bool(self._api_key)
        if not self._active:
            return

        try:
            self._client = httpx.Client(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=_API_TIMEOUT,
            )
        except Exception:
            logger.warning("Crosmos client init failed", exc_info=True)
            self._active = False
            self._client = None
            return

        # Pre-resolve default space so prefetch/sync don't pay the lookup cost.
        if self._space_name:
            sid, err = self._resolve_space_id(self._space_name)
            if sid:
                self._space_id = sid
            else:
                logger.debug("Crosmos default space resolution failed: %s", err)

    def shutdown(self) -> None:
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        self._sync_thread = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None

    def _resolve_space_id(self, name: str) -> tuple[str | None, str | None]:
        name = (name or "").strip()
        if not name:
            return None, "No space configured. Pass space_name or set CROSMOS_SPACE_NAME."
        if name in self._space_id_cache:
            return self._space_id_cache[name], None
        if not self._client:
            return None, "Crosmos client not initialized."
        try:
            resp = self._client.get("/spaces", params={"name": name})
            resp.raise_for_status()
            spaces = resp.json().get("spaces", [])
        except httpx.HTTPStatusError as e:
            return None, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        except Exception as e:
            return None, f"Space lookup failed: {type(e).__name__}: {e}"
        if not spaces:
            return None, f"No space named '{name}' found."
        sid = spaces[0]["id"]
        self._space_id_cache[name] = sid
        return sid, None

    def _space_id_for(self, args: dict) -> tuple[str | None, str | None]:
        requested = (args.get("space_name") or "").strip()
        if requested:
            return self._resolve_space_id(requested)
        if self._space_id:
            return self._space_id, None
        if self._space_name:
            return self._resolve_space_id(self._space_name)
        return None, "No space configured. Pass space_name or set CROSMOS_SPACE_NAME."

    def system_prompt_block(self) -> str:
        if not self._active:
            return ""
        space = self._space_name or "(unset)"
        return (
            "# Crosmos Memory\n"
            f"Active. Default space: {space}.\n"
            "Use crosmos_remember to store explicit facts, crosmos_recall to search, "
            "and crosmos_forget to soft-delete. Conversations are also auto-ingested "
            "and relevant memories are auto-recalled each turn."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._active or not self._client:
            return ""
        q = (query or "").strip()
        if len(q) < 5:
            return ""
        if q.lower().startswith(_SKIP_RECALL_PREFIXES):

            return ""

        space_id, err = self._space_id_for({})
        if err or not space_id:
            logger.debug("Crosmos prefetch skipped: %s", err)
            return ""

        try:
            candidates = tools.search(self._client, space_id, q, limit=5)
        except Exception as exc:
            logger.debug("Crosmos prefetch failed: %s", exc)
            return ""

        if not candidates:
            return ""

        lines: list[str] = []
        for c in candidates[:5]:
            line = f"- {_sanitize(c.get('content', ''))}"
            src = c.get("source")
            if src:
                line += f" (source: {src[:80]}...)" if len(src) > 80 else f" (source: {src})"
            lines.append(line)
        body = "\n".join(lines)
        return f"<crosmos-context>\n{body}\n</crosmos-context>"

    def sync_turn(
        self, user_content: str, assistant_content: str, *, session_id: str = ""
    ) -> None:
        if not self._active or not self._write_enabled or not self._client:
            return
        u = _strip_own_context(user_content)
        a = _strip_own_context(assistant_content)
        if not u or not a:
            return
        if len(u) < _MIN_USER_LEN and len(a) < _MIN_ASSISTANT_LEN:
            return
        if u.lower().startswith(_SKIP_INGEST_PREFIXES):
            return

        space_id, err = self._space_id_for({})
        if err or not space_id:
            logger.debug("Crosmos sync_turn skipped: %s", err)
            return

        sid = session_id or self._session_id
        client = self._client

        def _run() -> None:
            try:
                tools.ingest_turn(client, space_id, u, a, session_id=sid)
            except Exception as exc:
                logger.debug("Crosmos sync_turn failed: %s", exc)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=2.0)
        self._sync_thread = threading.Thread(target=_run, daemon=True, name="crosmos-sync")
        self._sync_thread.start()

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:

        if not self._active or not self._write_enabled or not self._client:
            return
        cleaned: list[dict[str, str]] = []
        for msg in messages or []:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = _strip_own_context(str(msg.get("content", "")))
            if content:
                cleaned.append({"role": role, "content": content})
        if len(cleaned) < 2:
            return

        space_id, err = self._space_id_for({})
        if err or not space_id:
            logger.debug("Crosmos on_session_end skipped: %s", err)
            return

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        try:
            payload = {
                "space_id": space_id,
                "messages": cleaned,
                "session_id": self._session_id,
            }
            resp = self._client.post("/conversations", json=payload)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Crosmos session-end ingest failed: %s", exc)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [schemas.CROSMOS_REMEMBER, schemas.CROSMOS_RECALL, schemas.CROSMOS_FORGET]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if not self._active or not self._client:
            return json.dumps({"error": "Crosmos is not configured"})

        if tool_name == "crosmos_forget":
            return tools.forget(self._client, args)

        space_id, err = self._space_id_for(args)
        if err or not space_id:
            return json.dumps({"error": err})

        if tool_name == "crosmos_remember":
            return tools.remember(self._client, space_id, args, session_id=self._session_id)
        if tool_name == "crosmos_recall":
            return tools.recall(self._client, space_id, args)
        return json.dumps({"error": f"Unknown tool: {tool_name}"})


def register(ctx) -> None:
    ctx.register_memory_provider(CrosmosMemoryProvider())
