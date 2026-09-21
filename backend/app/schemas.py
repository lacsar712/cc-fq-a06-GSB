from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class SampleOut(BaseModel):
    id: int
    name: str
    description: str
    is_broken: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class JobCreate(BaseModel):
    sampleId: int | None = None
    fastqText: str | None = Field(default=None, alias="fastqText")

    model_config = {"populate_by_name": True}


class StageOut(BaseModel):
    id: int
    actor_name: str
    stage_order: int
    status: str
    message: str | None
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: int
    sample_id: int | None
    sample_name: str
    status: str
    created_by: str
    metrics: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
    stages: list[StageOut] = []

    model_config = {"from_attributes": True}


class JobListItem(BaseModel):
    id: int
    sample_id: int | None
    sample_name: str
    status: str
    created_by: str
    metrics: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class HealthOut(BaseModel):
    status: str
    service: str


# ---- 失败归因与话术聚类 ----


class FailureJobRef(BaseModel):
    job_id: int
    sample_id: int | None
    sample_name: str
    is_broken: bool | None
    status: str
    created_by: str
    created_at: datetime | None
    message: str | None


class MessageClusterOut(BaseModel):
    cluster_prefix: str
    cluster_size: int
    latest_job_id: int
    latest_job_created_at: datetime | None
    sample_name: str
    latest_message: str | None
    recent_jobs: list[FailureJobRef]


class ActorFailureOut(BaseModel):
    actor_name: str
    failure_count: int
    latest_failure_at: datetime | None
    clusters: list[MessageClusterOut]


class AttributionOut(BaseModel):
    start_date: date | None
    end_date: date | None
    is_broken: bool | None
    total_failures: int
    actors: list[ActorFailureOut]
