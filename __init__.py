"""Crosmos Memory plugin — registration and auto-hooks."""

import json
import logging
import httpx
import os

from . import schemas, tools

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get("CROSMOS_BASE_URL", "https://api.crosmos.dev/v1")
_API_KEY = os.environ.get("CROSMOS_API_KEY", "")
_DEFAULT_SPACE_ID = os.environ.get("CROSMOS_SPACE_ID", "")

_turn_ingested = False


def _recall_for_turn(
    session_id: str, user_message: str, is_first_turn: bool, **kwargs
) -> dict | None:
    """pre_llm_call hook: auto-recall relevant context before each LLM turn."""
    global _turn_ingested
    _turn_ingested = False

    if not _DEFAULT_SPACE_ID or not _API_KEY:
        return None

    if not user_message or len(user_message.strip()) < 5:
        return None

    recall_prefixes = (
        "recall ",
        "remember ",
        "search ",
        "what do you know",
        "what do i",
    )
    if user_message.strip().lower().startswith(recall_prefixes):
        return None

    try:
        resp = httpx.post(
            f"{_BASE_URL}/search",
            headers={
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "query": user_message,
                "space_id": _DEFAULT_SPACE_ID,
                "limit": 5,
                "include_source": True,
            },
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])

        if not candidates:
            return None

        lines = ["Relevant context from memory:"]
        for c in candidates[:5]:
            line = f"- {c['content']}"
            if c.get("source"):
                src = c["source"]
                line += (
                    f" (source: {src[:80]}...)"
                    if len(src) > 80
                    else f" (source: {src})"
                )
            lines.append(line)

        return {"context": "\n".join(lines)}
    except Exception as e:
        logger.warning("crosmos auto-recall failed: %s", e)
        return None


def _ingest_after_turn(
    session_id: str, user_message: str, assistant_response: str, **kwargs
) -> None:
    """post_llm_call hook: auto-ingest conversations after each completed turn."""
    global _turn_ingested

    if not _DEFAULT_SPACE_ID or not _API_KEY:
        return

    if _turn_ingested:
        return

    if not user_message or not assistant_response:
        return

    if len(user_message.strip()) < 10 and len(assistant_response.strip()) < 10:
        return

    if (
        user_message.strip()
        .lower()
        .startswith(("recall", "remember", "search", "forget"))
    ):
        return

    try:
        resp = httpx.post(
            f"{_BASE_URL}/conversations",
            headers={
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "space_id": _DEFAULT_SPACE_ID,
                "messages": [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_response},
                ],
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        _turn_ingested = True
        logger.debug("crosmos auto-ingest: job %s", resp.json().get("job_id"))
    except Exception as e:
        logger.warning("crosmos auto-ingest failed: %s", e)


def register(ctx):
    """Wire schemas to handlers and register hooks."""
    ctx.register_tool(
        name="crosmos_remember",
        toolset="crosmos",
        schema=schemas.CROSMOS_REMEMBER,
        handler=tools.crosmos_remember,
    )
    ctx.register_tool(
        name="crosmos_recall",
        toolset="crosmos",
        schema=schemas.CROSMOS_RECALL,
        handler=tools.crosmos_recall,
    )
    ctx.register_tool(
        name="crosmos_forget",
        toolset="crosmos",
        schema=schemas.CROSMOS_FORGET,
        handler=tools.crosmos_forget,
    )
    ctx.register_tool(
        name="crosmos_graph_stats",
        toolset="crosmos",
        schema=schemas.CROSMOS_GRAPH_STATS,
        handler=tools.crosmos_graph_stats,
    )

    ctx.register_hook("pre_llm_call", _recall_for_turn)
    ctx.register_hook("post_llm_call", _ingest_after_turn)
