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
HANK_TEAM_KEY = os.getenv("HERMES_LINEAR_TEAM_KEY", "HANK")
HANK_LABEL_NAME = os.getenv("HERMES_LINEAR_LABEL", "hank")


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
    if isinstance(existing, dict):
        for key in ("auto_created", "created_at"):
            if key in existing:
                record[key] = existing[key]
    if not record.get("created_at"):
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


def _hank_team_context() -> Dict[str, Any]:
    """Return the Hank Linear team, label, viewer, and best started state."""
    q = """
    query($teamKey: String!, $labelName: String!) {
      viewer { id name }
      teams(filter: { key: { eq: $teamKey } }, first: 1) {
        nodes {
          id name key
          labels(filter: { name: { eq: $labelName } }, first: 1) { nodes { id name } }
          states(first: 50) { nodes { id name type position } }
        }
      }
    }
    """
    data = _graphql(q, {"teamKey": HANK_TEAM_KEY, "labelName": HANK_LABEL_NAME})
    team = (((data.get("teams") or {}).get("nodes")) or [None])[0]
    if not team:
        raise RuntimeError(f"Linear team not found: {HANK_TEAM_KEY}")
    states = ((team.get("states") or {}).get("nodes")) or []
    started = [s for s in states if s.get("type") == "started"]
    state = None
    for preferred in ("In Progress", "Started"):
        state = next((s for s in started if s.get("name") == preferred), None)
        if state:
            break
    if not state and started:
        state = sorted(started, key=lambda s: s.get("position") or 0)[0]
    labels = ((team.get("labels") or {}).get("nodes")) or []
    return {"viewer": data.get("viewer") or {}, "team": team, "label": labels[0] if labels else None, "state": state}


def _summarise_title(text: str, max_len: int = 80) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    cleaned = cleaned.replace("`", "")
    if not cleaned:
        return "Hank task"
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def should_auto_create_issue(message: str) -> bool:
    """Cheap local heuristic: only create Linear issues for real long-running work."""
    text = " ".join(str(message or "").strip().split())
    if len(text) < 25:
        return False
    if text.startswith("/"):
        return False
    lowered = text.lower()
    trivial_prefixes = (
        "what is", "what's", "where are", "whats", "thanks", "thank you",
        "ok", "okay", "yes", "no", "cool", "nice",
    )
    if any(lowered == p or lowered.startswith(p + " ") for p in trivial_prefixes):
        return False
    action_terms = (
        "do ", "create", "add", "implement", "fix", "debug", "inspect", "audit",
        "sync", "update", "commit", "push", "deploy", "write", "generate",
        "process", "triage", "backfill", "phase",
    )
    return any(term in lowered for term in action_terms)


def auto_create_issue_for_session(
    session_key: str,
    message: str,
    source: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Create/link a Hank Linear issue for a long-running gateway session.

    Returns the persisted session link, or None when heuristics say not to create.
    """
    if not session_key or get_session_link(session_key):
        return get_session_link(session_key)
    if not should_auto_create_issue(message):
        return None

    ctx = _hank_team_context()
    team = ctx["team"]
    label = ctx.get("label")
    state = ctx.get("state")
    viewer = ctx.get("viewer") or {}
    title = _summarise_title(message)
    description = (
        "Auto-created by Hank because this Telegram/Discord task ran long enough "
        "to need durable tracking.\n\n"
        "Details stay in the originating chat/wiki unless explicitly added here."
    )
    issue_input: Dict[str, Any] = {
        "teamId": team["id"],
        "title": title,
        "description": description,
        "priority": 0,
    }
    if label:
        issue_input["labelIds"] = [label["id"]]
    if state:
        issue_input["stateId"] = state["id"]
    if viewer.get("id"):
        issue_input["assigneeId"] = viewer["id"]
        issue_input["subscriberIds"] = [viewer["id"]]

    q = """
    mutation($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue {
          id identifier title url
          team { key name }
          state { id name type }
          assignee { name }
        }
      }
    }
    """
    data = _graphql(q, {"input": issue_input})
    result = data.get("issueCreate") or {}
    if not result.get("success"):
        raise RuntimeError("Linear issueCreate returned success=false")
    issue = result.get("issue") or {}
    link = attach_session_issue(session_key, issue, source=source)
    link["auto_created"] = True
    # Re-save with the auto_created marker.
    links = _load_links()
    links["session_links"][session_key] = link
    _save_links(links)
    try:
        comment_issue(
            str(issue.get("id") or ""),
            "Started: auto-linked from a long-running Hank chat task.",
        )
    except Exception:
        pass
    return link


def refresh_session_issue(session_key: str) -> Optional[Dict[str, Any]]:
    """Refresh cached Linear issue metadata for an attached session."""
    link = get_session_link(session_key)
    if not link or not link.get("identifier"):
        return link
    issue = get_issue(str(link["identifier"]))
    return attach_session_issue(session_key, issue, source=link.get("source") or {})


def _team_state_by_type(team_key: str, desired_type: str, preferred_names: tuple[str, ...]) -> Optional[Dict[str, Any]]:
    q = """
    query($teamKey: String!) {
      teams(filter: { key: { eq: $teamKey } }, first: 1) {
        nodes { states(first: 50) { nodes { id name type position } } }
      }
    }
    """
    data = _graphql(q, {"teamKey": team_key})
    team = (((data.get("teams") or {}).get("nodes")) or [None])[0]
    states = (((team or {}).get("states") or {}).get("nodes")) or []
    candidates = [s for s in states if s.get("type") == desired_type]
    for name in preferred_names:
        found = next((s for s in candidates if s.get("name") == name), None)
        if found:
            return found
    if candidates:
        return sorted(candidates, key=lambda s: s.get("position") or 0)[0]
    return None


def update_issue_state(issue_id: str, state_id: str) -> Dict[str, Any]:
    q = """
    mutation($id: String!, $input: IssueUpdateInput!) {
      issueUpdate(id: $id, input: $input) {
        success
        issue { id identifier title url team { key name } state { id name type } assignee { name } }
      }
    }
    """
    data = _graphql(q, {"id": issue_id, "input": {"stateId": state_id}})
    result = data.get("issueUpdate") or {}
    if not result.get("success"):
        raise RuntimeError("Linear issueUpdate returned success=false")
    return result.get("issue") or {}


def finish_session_issue(session_key: str, *, failed: bool = False, api_calls: int = 0) -> Optional[Dict[str, Any]]:
    """Post a terminal milestone and move an auto-linked session issue out of Started.

    Returns the refreshed link.  Safe to call repeatedly; terminal states are left terminal.
    """
    link = get_session_link(session_key)
    if not link or not link.get("issue_id"):
        return link
    issue = get_issue(str(link.get("identifier") or link.get("issue_id")))
    state_type = (issue.get("state") or {}).get("type")
    status = "failed" if failed else "completed"
    if state_type not in ("completed", "canceled"):
        try:
            comment_issue(str(link["issue_id"]), f"Gateway turn {status}. API calls: {api_calls}.")
        except Exception:
            pass
        team_key = (issue.get("team") or {}).get("key") or link.get("team_key") or HANK_TEAM_KEY
        desired_type = "canceled" if failed else "completed"
        preferred = ("Canceled", "Cancelled") if failed else ("Done", "Completed")
        terminal_state = _team_state_by_type(str(team_key), desired_type, preferred)
        if terminal_state:
            issue = update_issue_state(str(link["issue_id"]), str(terminal_state["id"]))
    return attach_session_issue(session_key, issue, source=link.get("source") or {})


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
