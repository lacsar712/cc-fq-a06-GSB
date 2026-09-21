"""Failure attribution: group failed stages by actor, cluster messages by prefix.

Only stages with status ``failed`` are attribution input — ``skipped`` stages
(downstream of a failure) never reach these functions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# Prefix = message cut at the first of these delimiters (variable tail dropped).
_DELIMITERS = ("：", ":", "（", "(", "，", ",")
_MAX_PREFIX_LEN = 40
DEFAULT_RECENT_LIMIT = 5

_DIGITS_RE = re.compile(r"\d+")
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")

NO_MESSAGE_PREFIX = "（无消息）"


def message_prefix(message: str | None) -> str:
    """Normalize a failure message into a stable cluster prefix.

    - cut at the first delimiter so variable tails (行号/读段名等) drop off
    - digits -> ``#``, quoted literals -> ``'?'`` so one template = one cluster
    """
    if not message or not message.strip():
        return NO_MESSAGE_PREFIX
    text = message.strip()
    cut = len(text)
    for delim in _DELIMITERS:
        idx = text.find(delim)
        if idx != -1:
            cut = min(cut, idx)
    prefix = text[:cut] if cut > 0 else text
    prefix = _QUOTED_RE.sub("'?'", prefix)
    prefix = _DIGITS_RE.sub("#", prefix)
    prefix = prefix[:_MAX_PREFIX_LEN].strip()
    return prefix or NO_MESSAGE_PREFIX


@dataclass
class FailureRow:
    """One failed stage joined with its job (attribution input row)."""

    job_id: int
    sample_name: str
    is_broken: bool
    created_by: str
    job_created_at: datetime | None
    actor_name: str
    message: str | None
    stage_finished_at: datetime | None


def _recent_job_ref(row: FailureRow) -> dict:
    return {
        "job_id": row.job_id,
        "sample_name": row.sample_name,
        "is_broken": row.is_broken,
        "created_by": row.created_by,
        "created_at": row.job_created_at,
        "message": row.message,
    }


def _recency_key(row: FailureRow) -> tuple:
    # Flag first so naive sentinel never compares against real datetimes.
    if row.job_created_at is None:
        return (0, datetime.min, row.job_id)
    return (1, row.job_created_at, row.job_id)


def build_attribution(
    rows: list[FailureRow], recent_limit: int = DEFAULT_RECENT_LIMIT
) -> dict:
    """Aggregate failed-stage rows into actor counts + message-prefix clusters."""
    actors: dict[str, dict] = {}
    for row in rows:
        actor = actors.setdefault(
            row.actor_name,
            {"actor_name": row.actor_name, "failure_count": 0, "_clusters": {}},
        )
        actor["failure_count"] += 1
        prefix = message_prefix(row.message)
        cluster = actor["_clusters"].setdefault(
            prefix, {"prefix": prefix, "size": 0, "_jobs": []}
        )
        cluster["size"] += 1
        cluster["_jobs"].append(row)

    actor_list = []
    for actor in actors.values():
        clusters = []
        for cluster in actor["_clusters"].values():
            recent = sorted(cluster["_jobs"], key=_recency_key, reverse=True)[
                :recent_limit
            ]
            clusters.append(
                {
                    "prefix": cluster["prefix"],
                    "size": cluster["size"],
                    "recent_jobs": [_recent_job_ref(r) for r in recent],
                }
            )
        clusters.sort(key=lambda c: (-c["size"], c["prefix"]))
        actor_list.append(
            {
                "actor_name": actor["actor_name"],
                "failure_count": actor["failure_count"],
                "clusters": clusters,
            }
        )
    actor_list.sort(key=lambda a: (-a["failure_count"], a["actor_name"]))
    return {"total_failures": len(rows), "actors": actor_list}
