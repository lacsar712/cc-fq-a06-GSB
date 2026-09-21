"""失败归因聚合：只统计 failed 阶段（skipped 不计入），按 Actor 计数并按消息前缀聚类。

过滤（起止日期、样例是否损坏）一律在服务端完成，调用方只传筛选条件。
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session, joinedload

from app.models import Job, JobStage, Sample


# 归一化后取此前缀长度作为"话术簇"键：行号/计数等数字统一为 #，
# 引号内字面量（实际输入值）与全角括号补充说明剥离，使同一话术落入同一簇。
MESSAGE_PREFIX_LEN = 48
RECENT_JOB_LIMIT = 5

_DIGIT_RUN = re.compile(r"\d+")
_WS_RUN = re.compile(r"\s+")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_FULLWIDTH_PAREN = re.compile(r"（[^）]*）")


def normalize_message(message: str | None) -> str:
    """把失败消息归一化为聚类前缀：去可变尾部（引号字面量/全角括号）、折叠空白、数字替换为 #。"""
    if not message:
        return ""
    text = _FULLWIDTH_PAREN.sub("", message)
    text = _QUOTED.sub("", text)
    text = _DIGIT_RUN.sub("#", text)
    text = _WS_RUN.sub(" ", text).strip()
    return text[:MESSAGE_PREFIX_LEN]


def _date_bounds(
    start_date: date | None, end_date: date | None
) -> tuple[datetime | None, datetime | None]:
    """end_date 按含当天处理（到 23:59:59.999999）。边界显式带 UTC，与 timestamptz 列比较无歧义。"""
    start = (
        datetime.combine(start_date, time.min, tzinfo=timezone.utc) if start_date else None
    )
    end = (
        datetime.combine(end_date, time.max, tzinfo=timezone.utc) if end_date else None
    )
    return start, end


def query_failed_stages(
    db: Session,
    start_date: date | None = None,
    end_date: date | None = None,
    is_broken: bool | None = None,
    actor_name: str | None = None,
) -> list[JobStage]:
    """按服务端过滤条件取出失败阶段（status='failed'），按时间倒序。

    - skipped / success 阶段不计入失败归因；
    - is_broken 过滤经 samples 表关联，自定义输入（无样例）在指定损坏/合格时被排除；
    - 日期过滤落在作业创建时间 Job.created_at 上。
    """
    start, end = _date_bounds(start_date, end_date)

    q = (
        db.query(JobStage)
        .options(joinedload(JobStage.job).joinedload(Job.sample))
        .join(Job, JobStage.job_id == Job.id)
        .outerjoin(Sample, Job.sample_id == Sample.id)
        .filter(JobStage.status == "failed")
    )
    if actor_name:
        q = q.filter(JobStage.actor_name == actor_name)
    if start is not None:
        q = q.filter(Job.created_at >= start)
    if end is not None:
        q = q.filter(Job.created_at <= end)
    if is_broken is not None:
        # 自定义输入没有样例行，is_broken 过滤时不纳入任一桶
        q = q.filter(Sample.is_broken == is_broken)

    return q.order_by(Job.created_at.desc(), JobStage.id.desc()).all()


def _job_item(stage: JobStage) -> dict[str, Any]:
    job = stage.job
    sample = job.sample if job and job.sample else None
    return {
        "job_id": job.id,
        "sample_id": job.sample_id,
        "sample_name": job.sample_name,
        "is_broken": sample.is_broken if sample else None,
        "status": job.status,
        "created_by": job.created_by,
        "created_at": job.created_at,
        "message": stage.message,
    }


def _cluster_records(stages: Iterable[JobStage]) -> list[dict[str, Any]]:
    buckets: dict[str, list[JobStage]] = {}
    for st in stages:
        prefix = normalize_message(st.message)
        buckets.setdefault(prefix, []).append(st)

    clusters: list[dict[str, Any]] = []
    for prefix, members in buckets.items():
        members.sort(
            key=lambda s: (s.job.created_at or datetime.min, s.id), reverse=True
        )
        latest = members[0]
        clusters.append(
            {
                "cluster_prefix": prefix,
                "cluster_size": len(members),
                "latest_job_id": latest.job.id,
                "latest_job_created_at": latest.job.created_at,
                "sample_name": latest.job.sample_name,
                "latest_message": latest.message,
                "recent_jobs": [_job_item(s) for s in members[:RECENT_JOB_LIMIT]],
            }
        )
    clusters.sort(key=lambda c: (-c["cluster_size"], c["cluster_prefix"]))
    return clusters


def build_attribution(stages: list[JobStage]) -> list[dict[str, Any]]:
    """按 Actor 聚合计数，Actor 内再按消息前缀聚类。"""
    by_actor: dict[str, list[JobStage]] = {}
    for st in stages:
        by_actor.setdefault(st.actor_name, []).append(st)

    result: list[dict[str, Any]] = []
    for actor_name, actor_stages in by_actor.items():
        actor_stages.sort(
            key=lambda s: (s.job.created_at or datetime.min, s.id), reverse=True
        )
        result.append(
            {
                "actor_name": actor_name,
                "failure_count": len(actor_stages),
                "latest_failure_at": actor_stages[0].job.created_at,
                "clusters": _cluster_records(actor_stages),
            }
        )
    result.sort(key=lambda a: (-a["failure_count"], a["actor_name"]))
    return result


def build_cluster_jobs(
    stages: list[JobStage], cluster_prefix: str
) -> list[dict[str, Any]]:
    """下钻：取某 Actor 下指定消息前缀簇的全部失败作业。"""
    jobs = [
        _job_item(s)
        for s in stages
        if normalize_message(s.message) == cluster_prefix
    ]
    jobs.sort(key=lambda j: j["created_at"] or datetime.min, reverse=True)
    return jobs


def export_rows(stages: list[JobStage]) -> list[dict[str, Any]]:
    """当前筛选结果的扁平摘录行（一个失败阶段一行），供 CSV 下载。"""
    rows: list[dict[str, Any]] = []
    for st in stages:
        job = st.job
        sample = job.sample if job and job.sample else None
        rows.append(
            {
                "job_id": job.id,
                "actor_name": st.actor_name,
                "stage_order": st.stage_order,
                "stage_status": st.status,
                "cluster_prefix": normalize_message(st.message),
                "message": st.message or "",
                "sample_id": job.sample_id if job.sample_id is not None else "",
                "sample_name": job.sample_name,
                "is_broken": "" if sample is None else ("true" if sample.is_broken else "false"),
                "created_by": job.created_by,
                "job_created_at": job.created_at.isoformat(sep=" ") if job.created_at else "",
                "stage_started_at": st.started_at.isoformat(sep=" ") if st.started_at else "",
                "stage_finished_at": st.finished_at.isoformat(sep=" ") if st.finished_at else "",
            }
        )
    rows.sort(
        key=lambda r: (r["job_created_at"], r["job_id"], r["stage_order"]),
        reverse=True,
    )
    return rows


EXPORT_FIELDS = [
    "job_id",
    "actor_name",
    "stage_order",
    "stage_status",
    "cluster_prefix",
    "message",
    "sample_id",
    "sample_name",
    "is_broken",
    "created_by",
    "job_created_at",
    "stage_started_at",
    "stage_finished_at",
]
