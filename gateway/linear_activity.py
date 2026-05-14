"""Linear-backed activity helpers for gateway slash commands.

Small, stdlib-only helpers used by /linear and /activity.  API keys stay in
environment variables; persisted state stores only issue/session metadata.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

LINEAR_ENDPOINT = "https://api.linear.app/graphql"
LINKS_FILENAME = "linear_task_links.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _links_path() -> Path:
    return get_hermes_home() / LINKS_FILENAME


def _load_links() -> Dict[str, Any]:
    path = _links_path()
    if not path.exists():
        return {"version": 1, "session_links": {}}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"version": 1, "session_links": {}}
    if not isinstance(data, dict):
        return {"version": 1, "session_links": {}}
    data.setdefault("version", 1)
    data.setdefault("session_links", {})
    if not isinstance(data["session_links"], dict):
        data["session_links"] = {}
    return data


def _save_links(data: Dict[str, Any]) -> None:
    path = _links_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def get_session_link(session_key: str) -> Optional[Dict[str, Any]]:
    links = _load_links().get("session_links", {})
    link = links.get(session_key)
    return link if isinstance(link, dict) else None


def attach_session_issue(session_key: str, issue: Dict[str, Any], source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = _load_links()
    record = {
        "issue_id": issue.get("id"),
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "url": issue.get("url"),
        "team_key": (issue.get("team") or {}).get("key"),
        "state": (issue.get("state") or {}).get("name"),
        "state_type": (issue.get("state") or {}).get("type"),
        "source": source or {},
        "updated_at": _now_iso(),
    }
    existing = data["session_links"].get(session_key) or {}
    if isinstance(existing, dict) and existing.get("created_at"):
        record["created_at"] = existing["created_at"]
    else:
        record["created_at"] = record["updated_at"]
    data["session_links"][session_key] = record
    _save_links(data)
    return record


def detach_session_issue(session_key: str) -> Optional[Dict[str, Any]]:
    data = _load_links()
    old = data.get("session_links", {}).pop(session_key, None)
    _save_links(data)
    return old if isinstance(old, dict) else None


def _graphql(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    api_key = os.getenv("LINEAR_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("LINEAR_API_KEY is not set")
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        LINEAR_ENDPOINT,
        data=payload,
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:400]
        raise RuntimeError(f"Linear HTTP {exc.code}: {detail}") from exc
    if data.get("errors"):
        messages = "; ".join(str(e.get("message", e)) for e in data["errors"][:3])
        raise RuntimeError(f"Linear GraphQL error: {messages}")
    return data.get("data") or {}


def viewer_and_teams() -> Dict[str, Any]:
    q = """
    query {
      viewer { id name }
      teams(first: 50) { nodes { id name key } }
    }
    """
    return _graphql(q)


def get_issue(issue_ref: str) -> Dict[str, Any]:
    q = """
    query($id: String!) {
      issue(id: $id) {
        id identifier title url
        team { key name }
        state { id name type }
        assignee { name }
      }
    }
    """
    data = _graphql(q, {"id": issue_ref})
    issue = data.get("issue")
    if not issue:
        raise RuntimeError(f"Linear issue not found: {issue_ref}")
    return issue


def comment_issue(issue_id: str, body: str) -> Dict[str, Any]:
    q = """
    mutation($input: CommentCreateInput!) {
      commentCreate(input: $input) {
        success
        comment { id url body createdAt }
      }
    }
    """
    data = _graphql(q, {"input": {"issueId": issue_id, "body": body}})
    result = data.get("commentCreate") or {}
    if not result.get("success"):
        raise RuntimeError("Linear commentCreate returned success=false")
    return result.get("comment") or {}


def list_started_issues(first: int = 12) -> List[Dict[str, Any]]:
    q = """
    query($first: Int!) {
      issues(first: $first, filter: { state: { type: { eq: "started" } } }, orderBy: updatedAt) {
        nodes {
          id identifier title url priority updatedAt
          state { name type }
          team { key }
          assignee { name }
        }
      }
    }
    """
    data = _graphql(q, {"first": max(1, min(int(first), 50))})
    return (((data.get("issues") or {}).get("nodes")) or [])


def format_issue_line(issue: Dict[str, Any]) -> str:
    ident = issue.get("identifier") or issue.get("id") or "?"
    title = str(issue.get("title") or "").strip()
    state = (issue.get("state") or {}).get("name") or "?"
    assignee = (issue.get("assignee") or {}).get("name") or "unassigned"
    return f"- `{ident}` — {title} — {state} — {assignee}"


def safe_error(exc: Exception) -> str:
    text = str(exc)
    # Do not echo tokens if a low-level library ever includes headers.
    api_key = os.getenv("LINEAR_API_KEY", "").strip()
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    return text[:500]
