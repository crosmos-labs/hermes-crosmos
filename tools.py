"""Stateless HTTP helpers for Crosmos tool calls."""

from __future__ import annotations

import json
from typing import Any

import httpx


def _http_error(prefix: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return json.dumps(
            {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        )
    return json.dumps({"error": f"{prefix}: {type(exc).__name__}: {exc}"})


def remember(
    client: httpx.Client, space_id: str, args: dict, session_id: str = ""
) -> str:
    """Ingest a single fact via /conversations as a one-message payload.

    Using /conversations (not /sources) keeps explicit memories on the same
    extraction pipeline as auto-captured turns and links them to the active
    session for later lookback.
    """
    content = (args.get("content") or "").strip()
    if not content:
        return json.dumps({"error": "No content provided"})
    payload: dict[str, Any] = {
        "space_id": space_id,
        "messages": [{"role": "user", "content": content}],
    }
    if session_id:
        payload["session_id"] = session_id
    try:
        resp = client.post("/conversations", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return json.dumps(
            {
                "status": "accepted",
                "job_id": data.get("job_id"),
                "message": f"Remembered. Job {data.get('job_id', 'unknown')} is processing.",
            }
        )
    except Exception as exc:
        return _http_error("Ingestion failed", exc)


def ingest_turn(
    client: httpx.Client,
    space_id: str,
    user_content: str,
    assistant_content: str,
    session_id: str = "",
) -> dict:
    """Used by sync_turn — raises on error so caller can log."""
    payload: dict[str, Any] = {
        "space_id": space_id,
        "messages": [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ],
    }
    if session_id:
        payload["session_id"] = session_id
    resp = client.post("/conversations", json=payload)
    resp.raise_for_status()
    return resp.json()


def recall(client: httpx.Client, space_id: str, args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "No query provided"})
    limit = min(max(int(args.get("limit", 10) or 10), 1), 50)
    include_source = bool(args.get("include_source", True))
    try:
        candidates = search(client, space_id, query, limit, include_source)
        results: list[dict[str, Any]] = []
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
        return json.dumps({"results": results, "total": len(results)})
    except Exception as exc:
        return _http_error("Search failed", exc)


def forget(client: httpx.Client, args: dict) -> str:
    memory_id = (args.get("memory_id") or "").strip()
    if not memory_id:
        return json.dumps({"error": "No memory_id provided"})
    try:
        resp = client.delete(f"/memories/{memory_id}")
        if resp.status_code == 404:
            return json.dumps({"error": f"Memory {memory_id} not found"})
        resp.raise_for_status()
        return json.dumps({"status": "forgotten", "memory_id": memory_id})
    except Exception as exc:
        return _http_error("Forget failed", exc)


def search(
    client: httpx.Client,
    space_id: str,
    query: str,
    limit: int = 5,
    include_source: bool = True,
) -> list[dict[str, Any]]:
    """Bare /search call. Raises on HTTP error."""
    resp = client.post(
        "/search",
        json={
            "query": query,
            "space_id": space_id,
            "limit": limit,
            "include_source": include_source,
        },
    )
    resp.raise_for_status()
    return resp.json().get("candidates", []) or []
