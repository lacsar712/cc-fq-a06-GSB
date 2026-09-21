import csv
import io
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from app import attribution
from app.auth import authenticate_user, create_access_token, get_current_user, require_bioops
from app.database import SessionLocal, get_db
from app.models import Job, JobStage, Sample
from app.pipeline.runner import create_job_stages, run_pipeline_sync
from app.schemas import (
    ActorFailureOut,
    AttributionOut,
    FailureJobRef,
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


# ---- 失败归因与话术聚类（bioops / auditor 均只读）----


def _validate_range(start_date: date | None, end_date: date | None) -> None:
    if start_date and end_date and end_date < start_date:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")


@router.get("/attribution/failures", response_model=AttributionOut)
def get_failure_attribution(
    start_date: date | None = Query(default=None, description="起始日期（含当天）"),
    end_date: date | None = Query(default=None, description="结束日期（含当天）"),
    is_broken: bool | None = Query(default=None, description="样例是否损坏：true/false，不传为全部"),
    _user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按失败 Actor 计数，Actor 内再按失败消息前缀聚类。跳过（skipped）阶段不计入。"""
    _validate_range(start_date, end_date)
    stages = attribution.query_failed_stages(db, start_date, end_date, is_broken)
    actors = attribution.build_attribution(stages)
    return AttributionOut(
        start_date=start_date,
        end_date=end_date,
        is_broken=is_broken,
        total_failures=len(stages),
        actors=[ActorFailureOut(**a) for a in actors],
    )


@router.get(
    "/attribution/failures/{actor_name}/jobs",
    response_model=list[FailureJobRef],
)
def get_cluster_jobs(
    actor_name: str,
    cluster_prefix: str = Query(..., description="消息簇归一化前缀"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    is_broken: bool | None = Query(default=None),
    _user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """某失败 Actor 下单个消息前缀簇的全部失败作业（点簇下钻）。"""
    _validate_range(start_date, end_date)
    stages = attribution.query_failed_stages(
        db, start_date, end_date, is_broken, actor_name=actor_name
    )
    jobs = attribution.build_cluster_jobs(stages, cluster_prefix)
    return [FailureJobRef(**j) for j in jobs]


@router.get("/attribution/export")
def export_failure_attribution(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    is_broken: bool | None = Query(default=None),
    _user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载当前筛选结果的后端摘录（CSV，一个失败阶段一行）。"""
    _validate_range(start_date, end_date)
    stages = attribution.query_failed_stages(db, start_date, end_date, is_broken)
    rows = attribution.export_rows(stages)

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=attribution.EXPORT_FIELDS, lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = buffer.getvalue().encode("utf-8-sig")

    filename = "failure_attribution.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}',
            "X-Total-Failures": str(len(rows)),
        },
    )
