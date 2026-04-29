"""Tool handlers"""

import json
import os
from pathlib import Path

import httpx


def _load_hermes_env() -> None:
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    env_file = Path(hermes_home) / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_hermes_env()

_BASE_URL = os.environ.get("CROSMOS_BASE_URL", "https://api.crosmos.dev/api/v1")
_API_KEY = os.environ.get("CROSMOS_API_KEY", "")
_DEFAULT_SPACE_NAME = os.environ.get("CROSMOS_SPACE_NAME", "")

_client = httpx.Client(
    base_url=_BASE_URL,
    headers={
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
    },
    timeout=30.0,
)

# Process-local cache: space name → UUID. Avoids re-resolving on every call.
_space_id_cache: dict[str, str] = {}


def _resolve_space_id(args: dict) -> tuple[str | None, str | None]:
    """Resolve a space name to its UUID.

    Priority: ``space_name`` arg → ``CROSMOS_SPACE_NAME`` env default.
    Returns ``(space_id, error)``. Exactly one is non-None.
    """
    name = (args.get("space_name") or _DEFAULT_SPACE_NAME or "").strip()
    if not name:
        return None, (
            "No space configured. Pass space_name or set CROSMOS_SPACE_NAME."
        )

    if name in _space_id_cache:
        return _space_id_cache[name], None

    try:
        resp = _client.get("/spaces", params={"name": name})
        resp.raise_for_status()
        spaces = resp.json().get("spaces", [])
    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return None, f"Space lookup failed: {type(e).__name__}: {e}"

    if not spaces:
        return None, f"No space named '{name}' found."

    space_id = spaces[0]["id"]
    _space_id_cache[name] = space_id
    return space_id, None


def crosmos_remember(args: dict, **kwargs) -> str:
    """Ingest content into the knowledge graph."""
    content = args.get("content", "").strip()
    if not content:
        return json.dumps({"error": "No content provided"})

    space_id, err = _resolve_space_id(args)
    if err:
        return json.dumps({"error": err})

    try:
        resp = _client.post(
            "/sources",
            json={
                "space_id": space_id,
                "sources": [{"content": content, "content_type": "text"}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return json.dumps(
            {
                "status": "accepted",
                "job_id": data.get("job_id"),
                "source_ids": data.get("source_ids", []),
                "message": f"Content ingested. Job {data.get('job_id', 'unknown')} is processing.",
            }
        )
    except httpx.HTTPStatusError as e:
        return json.dumps(
            {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        )
    except Exception as e:
        return json.dumps({"error": f"Ingestion failed: {type(e).__name__}: {e}"})


def crosmos_recall(args: dict, **kwargs) -> str:
    """Search the knowledge graph for relevant memories."""
    query = args.get("query", "").strip()
    limit = min(max(args.get("limit", 10), 1), 50)
    include_source = args.get("include_source", True)

    if not query:
        return json.dumps({"error": "No query provided"})

    space_id, err = _resolve_space_id(args)
    if err:
        return json.dumps({"error": err})

    try:
        resp = _client.post(
            "/search",
            json={
                "query": query,
                "space_id": space_id,
                "limit": limit,
                "include_source": include_source,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        candidates = data.get("candidates", [])
        results = []
        for c in candidates:
            entry = {
                "memory_id": c.get("memory_id"),
                "content": c.get("content"),
                "score": round(c.get("score", 0), 4),
                "type": c.get("memory_type"),
            }
            if include_source and c.get("source"):
                entry["source"] = c["source"]
            results.append(entry)

        return json.dumps(
            {
                "query": data.get("query"),
                "results": results,
                "total": data.get("total", len(results)),
                "took_ms": round(data.get("took_ms", 0)),
            }
        )
    except httpx.HTTPStatusError as e:
        return json.dumps(
            {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        )
    except Exception as e:
        return json.dumps({"error": f"Search failed: {type(e).__name__}: {e}"})


def crosmos_forget(args: dict, **kwargs) -> str:
    """Soft-delete a memory."""
    memory_id = args.get("memory_id", "").strip()
    if not memory_id:
        return json.dumps({"error": "No memory_id provided"})

    try:
        resp = _client.delete(f"/memories/{memory_id}")
        if resp.status_code == 404:
            return json.dumps({"error": f"Memory {memory_id} not found"})
        resp.raise_for_status()
        return json.dumps({"status": "forgotten", "memory_id": memory_id})
    except httpx.HTTPStatusError as e:
        return json.dumps(
            {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        )
    except Exception as e:
        return json.dumps({"error": f"Forget failed: {type(e).__name__}: {e}"})


def crosmos_graph_stats(args: dict, **kwargs) -> str:
    """Get knowledge graph statistics."""
    space_id, err = _resolve_space_id(args)
    if err:
        return json.dumps({"error": err})

    try:
        resp = _client.get("/graph/stats", params={"space_id": space_id})
        resp.raise_for_status()
        return json.dumps(resp.json())
    except httpx.HTTPStatusError as e:
        return json.dumps(
            {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        )
    except Exception as e:
        return json.dumps({"error": f"Stats failed: {type(e).__name__}: {e}"})
