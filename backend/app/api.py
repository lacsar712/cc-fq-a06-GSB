import csv
import io
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.attribution import FailureRow, build_attribution, message_prefix
from app.auth import authenticate_user, create_access_token, get_current_user, require_bioops
from app.database import SessionLocal, get_db
from app.models import Job, JobStage, Sample
from app.pipeline.runner import create_job_stages, run_pipeline_sync
from app.schemas import (
    AttributionOut,
    HealthOut,
    JobCreate,
    JobListItem,
    JobOut,
    LoginRequest,
    SampleOut,
    StageOut,
    TokenResponse,
)


router = APIRouter(prefix="/api")


def _run_job_background(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            run_pipeline_sync(db, job)
    finally:
        db.close()


@router.get("/health", response_model=HealthOut)
def health():
    return HealthOut(status="ok", service="fastq-qc-pipeline")


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest):
    user = authenticate_user(body.username.strip(), body.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token(user["username"], user["role"])
    return TokenResponse(
        access_token=token,
        username=user["username"],
        role=user["role"],
    )


@router.get("/samples", response_model=list[SampleOut])
def list_samples(_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Sample).order_by(Sample.id).all()


@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    body: JobCreate,
    background: BackgroundTasks,
    user: dict = Depends(require_bioops),
    db: Session = Depends(get_db),
):
    sample_id = body.sampleId
    fastq_text = (body.fastqText or "").strip() if body.fastqText else ""
    sample_name = "自定义输入"
    sample = None

    if sample_id is not None:
        sample = db.query(Sample).filter(Sample.id == sample_id).first()
        if not sample:
            raise HTTPException(status_code=404, detail="样例不存在")
        fastq_text = sample.fastq_content
        sample_name = sample.name
    elif not fastq_text:
        raise HTTPException(status_code=400, detail="请提供 sampleId 或 fastqText")

    job = Job(
        sample_id=sample.id if sample else None,
        sample_name=sample_name,
        status="pending",
        created_by=user["username"],
        fastq_snapshot=fastq_text,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    create_job_stages(db, job.id)
    background.add_task(_run_job_background, job.id)

    job = (
        db.query(Job)
        .options(joinedload(Job.stages))
        .filter(Job.id == job.id)
        .first()
    )
    return job


@router.get("/jobs", response_model=list[JobListItem])
def list_jobs(_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Job).order_by(Job.id.desc()).all()


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, _user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    job = (
        db.query(Job)
        .options(joinedload(Job.stages))
        .filter(Job.id == job_id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="作业不存在")
    return job


@router.get("/jobs/{job_id}/stages", response_model=list[StageOut])
def get_job_stages(
    job_id: int, _user: dict = Depends(get_current_user), db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="作业不存在")
    return (
        db.query(JobStage)
        .filter(JobStage.job_id == job_id)
        .order_by(JobStage.stage_order)
        .all()
    )


def _failure_rows(
    db: Session,
    start: datetime | None,
    end: datetime | None,
    broken: str,
) -> list[FailureRow]:
    """Failed stages joined with jobs; skipped stages are never attribution input."""
    q = (
        db.query(JobStage, Job, Sample.is_broken)
        .join(Job, JobStage.job_id == Job.id)
        .outerjoin(Sample, Job.sample_id == Sample.id)
        .filter(JobStage.status == "failed")
    )
    if start is not None:
        q = q.filter(Job.created_at >= start)
    if end is not None:
        q = q.filter(Job.created_at <= end)
    if broken == "true":
        q = q.filter(func.coalesce(Sample.is_broken, False).is_(True))
    elif broken == "false":
        q = q.filter(func.coalesce(Sample.is_broken, False).is_(False))

    rows = []
    for stage, job, is_broken in q.order_by(Job.id.desc()).all():
        rows.append(
            FailureRow(
                job_id=job.id,
                sample_name=job.sample_name,
                is_broken=bool(is_broken),
                created_by=job.created_by,
                job_created_at=job.created_at,
                actor_name=stage.actor_name,
                message=stage.message,
                stage_finished_at=stage.finished_at,
            )
        )
    return rows


@router.get("/failures/attribution", response_model=AttributionOut)
def failure_attribution(
    _user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    start: datetime | None = Query(default=None, description="起始时间（含），ISO8601"),
    end: datetime | None = Query(default=None, description="截止时间（含），ISO8601"),
    broken: Literal["all", "true", "false"] = Query(
        default="all", description="按样例是否损坏过滤"
    ),
):
    """按失败 Actor 计数 + 消息前缀聚类；过滤全部在服务端完成。"""
    return build_attribution(_failure_rows(db, start, end, broken))


@router.get("/failures/attribution/export")
def failure_attribution_export(
    _user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    broken: Literal["all", "true", "false"] = Query(default="all"),
):
    """当前筛选结果的后端摘录下载（CSV，UTF-8 BOM）。"""
    rows = _failure_rows(db, start, end, broken)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "作业ID",
            "样例",
            "是否损坏",
            "失败Actor",
            "消息前缀簇",
            "失败消息",
            "提交人",
            "作业创建时间",
            "阶段完成时间",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.job_id,
                r.sample_name,
                "损坏" if r.is_broken else "合格",
                r.actor_name,
                message_prefix(r.message),
                r.message or "",
                r.created_by,
                r.job_created_at.isoformat() if r.job_created_at else "",
                r.stage_finished_at.isoformat() if r.stage_finished_at else "",
            ]
        )
    content = buf.getvalue().encode("utf-8-sig")
    filename = f"failure-attribution-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
