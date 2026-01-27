from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Mapping

import httpx

from . import IntegrationEvent

GITHUB_API_URL = "https://api.github.com"


def _normalize_limit(limit: int, min_value: int = 1, max_value: int = 5) -> int:
    return max(min_value, min(int(limit), max_value))


def _event_summary(event: Mapping[str, object]) -> tuple[str, Mapping[str, object]]:
    event_type = str(event.get("type") or "Event")
    repo = (event.get("repo") or {}).get("name") if isinstance(event.get("repo"), Mapping) else None
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    base_payload: dict[str, object] = {
        "type": event_type,
        "repo": repo,
        "created_at": event.get("created_at"),
    }

    if event_type == "PushEvent":
        commits = payload.get("commits") if isinstance(payload.get("commits"), list) else []
        commit_count = len(commits)
        commit_messages = [
            str(commit.get("message"))
            for commit in commits
            if isinstance(commit, Mapping) and commit.get("message")
        ][:3]
        base_payload.update(
            {
                "ref": payload.get("ref"),
                "commit_count": commit_count,
                "commit_messages": commit_messages,
            }
        )
        summary = f"GitHub: Pushed {commit_count} commit(s) to {repo or 'a repo'}"
        return summary, base_payload

    if event_type == "PullRequestEvent":
        action = payload.get("action")
        pr = payload.get("pull_request") if isinstance(payload.get("pull_request"), Mapping) else {}
        title = pr.get("title")
        base_payload.update(
            {
                "action": action,
                "title": title,
                "url": pr.get("html_url"),
            }
        )
        summary = f"GitHub: PR {action or 'update'} — {title or 'Untitled'}"
        return summary, base_payload

    if event_type == "IssuesEvent":
        action = payload.get("action")
        issue = payload.get("issue") if isinstance(payload.get("issue"), Mapping) else {}
        title = issue.get("title")
        base_payload.update(
            {
                "action": action,
                "title": title,
                "url": issue.get("html_url"),
            }
        )
        summary = f"GitHub: Issue {action or 'update'} — {title or 'Untitled'}"
        return summary, base_payload

    if event_type == "ReleaseEvent":
        action = payload.get("action")
        release = payload.get("release") if isinstance(payload.get("release"), Mapping) else {}
        tag = release.get("tag_name")
        base_payload.update(
            {
                "action": action,
                "tag": tag,
                "url": release.get("html_url"),
            }
        )
        summary = f"GitHub: Release {tag or ''} {action or ''}".strip()
        return summary, base_payload

    if event_type == "CreateEvent":
        ref_type = payload.get("ref_type")
        ref = payload.get("ref")
        base_payload.update({"ref_type": ref_type, "ref": ref})
        summary = f"GitHub: Created {ref_type or 'item'} {ref or ''}".strip()
        return summary, base_payload

    summary = f"GitHub: {event_type} on {repo or 'a repo'}"
    return summary, base_payload


def fetch_github_events(
    username: str,
    *,
    token: str | None = None,
    limit: int = 5,
) -> List[IntegrationEvent]:
    cleaned = (username or "").strip()
    if not cleaned:
        return []
    normalized_limit = _normalize_limit(limit)
    url = f"{GITHUB_API_URL}/users/{cleaned}/events/public"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(url, params={"per_page": normalized_limit}, headers=headers, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    events = []
    payload = response.json()
    if not isinstance(payload, list):
        return []
    for raw_event in payload[:normalized_limit]:
        if not isinstance(raw_event, Mapping):
            continue
        external_id = str(raw_event.get("id") or "")
        if not external_id:
            continue
        topic, extra_payload = _event_summary(raw_event)
        events.append(
            IntegrationEvent(
                kind="github",
                topic=topic,
                payload={
                    **extra_payload,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                },
                external_id=f"github:{external_id}",
            )
        )
    return events
