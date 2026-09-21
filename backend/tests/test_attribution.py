"""失败归因：聚合逻辑与 API 测试（SQLite 内存库，不依赖 Postgres）。"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import attribution
from app.database import Base, get_db
from app.main import app
from app.models import Job, JobStage, Sample
from app.pipeline.runner import STAGE_NAMES


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = testing_session_local()

    good = Sample(name="good-sample", is_broken=False, fastq_content="g")
    broken = Sample(name="broken-sample", is_broken=True, fastq_content="b")
    db.add_all([good, broken])
    db.commit()

    def make_job(*, sample, at, stage_states, created_by="bioops", job_status="failed"):
        job = Job(
            sample_id=sample.id if sample else None,
            sample_name=sample.name if sample else "自定义输入",
            status=job_status,
            created_by=created_by,
            fastq_snapshot="x",
            created_at=at,
        )
        db.add(job)
        db.flush()
        for order, (state, message) in enumerate(stage_states):
            db.add(
                JobStage(
                    job_id=job.id,
                    actor_name=STAGE_NAMES[order],
                    stage_order=order,
                    status=state,
                    message=message,
                    started_at=at,
                    finished_at=at,
                )
            )
        db.commit()
        return job

    ts = datetime  # 朴素 UTC，SQLite 下与过滤边界同为字符串比较
    line_msg_5 = "第 5 行表头必须以 @ 开头，实际为: '@bad'"
    line_msg_9 = "第 9 行表头必须以 @ 开头，实际为: '@oops'"
    skip_msg = "因 ParseActor 失败而跳过"

    # J1/J2：同话术（行号不同）的 ParseActor 失败
    make_job(sample=broken, at=ts(2026, 9, 10, 9, 0), stage_states=[
        ("failed", line_msg_5), ("skipped", skip_msg),
        ("skipped", skip_msg), ("skipped", skip_msg),
    ])
    make_job(sample=broken, at=ts(2026, 9, 11, 9, 0), stage_states=[
        ("failed", line_msg_9), ("skipped", skip_msg),
        ("skipped", skip_msg), ("skipped", skip_msg),
    ])
    # J3：ParseActor 另一种话术
    make_job(sample=broken, at=ts(2026, 9, 11, 14, 0), stage_states=[
        ("failed", "FASTQ 内容为空"), ("skipped", skip_msg),
        ("skipped", skip_msg), ("skipped", skip_msg),
    ])
    # J4：自定义输入（无样例）ParseActor 失败
    make_job(sample=None, at=ts(2026, 9, 12, 9, 0), stage_states=[
        ("failed", "序列与质量串长度不一致：seq=3 qual=4（读段 @z）"),
        ("skipped", skip_msg), ("skipped", skip_msg), ("skipped", skip_msg),
    ])
    # J5：合格样例，ParseActor 成功、QualityHistActor 失败、后续跳过
    make_job(sample=good, at=ts(2026, 9, 12, 15, 0), stage_states=[
        ("success", "完成"), ("failed", "质量统计失败: 非法 Phred 字符 '!'"),
        ("skipped", "因 QualityHistActor 失败而跳过"),
        ("skipped", "因 QualityHistActor 失败而跳过"),
    ])
    # J6：完全成功的作业
    make_job(sample=good, at=ts(2026, 9, 13, 9, 0), job_status="success", stage_states=[
        ("success", "完成"), ("success", "完成"),
        ("success", "完成"), ("success", "完成"),
    ])

    yield db
    db.close()


@pytest.fixture()
def client(db_session):
    def _get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _get_db
    # 不用 with：避免触发 lifespan 去连接 Postgres（建表在 fixture 内完成）
    yield TestClient(app)
    app.dependency_overrides.clear()


def _token(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(client, username, password):
    return {"Authorization": f"Bearer {_token(client, username, password)}"}


# ---------- 纯聚合逻辑 ----------


def test_normalize_message_collapses_digits_and_ws():
    p1 = attribution.normalize_message("第 5 行表头必须以 @ 开头，实际为: '@bad'")
    p2 = attribution.normalize_message("第 9 行表头必须以 @ 开头，实际为: '@oops'")
    assert p1 == p2
    assert "#" in p1
    assert attribution.normalize_message("FASTQ 内容为空") != p1
    assert attribution.normalize_message(None) == ""


def test_counts_only_failed_stages(db_session):
    stages = attribution.query_failed_stages(db_session)
    actors = attribution.build_attribution(stages)
    counts = {a["actor_name"]: a["failure_count"] for a in actors}
    # 4 个 ParseActor 失败 + 1 个 QualityHistActor 失败；skipped/success 均不计
    assert counts == {"ParseActor": 4, "QualityHistActor": 1}
    assert sum(counts.values()) == len(stages) == 5


def test_cluster_size_and_latest_job(db_session):
    stages = attribution.query_failed_stages(db_session, actor_name="ParseActor")
    actors = attribution.build_attribution(stages)
    parse = actors[0]
    clusters = {c["cluster_prefix"]: c for c in parse["clusters"]}

    line_prefix = attribution.normalize_message("第 5 行表头必须以 @ 开头，实际为: '@x'")
    line_cluster = clusters[line_prefix]
    assert line_cluster["cluster_size"] == 2
    # 最近作业 = 9-11 的 J2（id=2）
    assert line_cluster["latest_job_id"] == 2
    assert [j["job_id"] for j in line_cluster["recent_jobs"]] == [2, 1]
    # 其余两簇各 1 个
    assert sum(c["cluster_size"] for c in parse["clusters"]) == 4


def test_cluster_drilldown(db_session):
    line_prefix = attribution.normalize_message("第 1 行表头必须以 @ 开头，实际为: 'x'")
    stages = attribution.query_failed_stages(db_session, actor_name="ParseActor")
    jobs = attribution.build_cluster_jobs(stages, line_prefix)
    assert [j["job_id"] for j in jobs] == [2, 1]
    assert jobs[0]["is_broken"] is True


def test_date_filter_is_server_side_inclusive(db_session):
    one_day = attribution.query_failed_stages(
        db_session,
        start_date=datetime(2026, 9, 11).date(),
        end_date=datetime(2026, 9, 11).date(),
    )
    assert len(one_day) == 2  # J2 + J3

    from_12 = attribution.query_failed_stages(
        db_session, start_date=datetime(2026, 9, 12).date()
    )
    assert {s.job_id for s in from_12} == {4, 5}


def test_is_broken_filter_excludes_custom_input(db_session):
    broken = attribution.query_failed_stages(db_session, is_broken=True)
    ok = attribution.query_failed_stages(db_session, is_broken=False)
    assert {s.job_id for s in broken} == {1, 2, 3}
    assert {s.job_id for s in ok} == {5}
    # 自定义输入（J4）在损坏/合格两档中都不出现
    all_stages = attribution.query_failed_stages(db_session)
    assert 4 in {s.job_id for s in all_stages}


def test_export_rows_fields(db_session):
    rows = attribution.export_rows(attribution.query_failed_stages(db_session))
    assert len(rows) == 5
    for row in rows:
        for field in attribution.EXPORT_FIELDS:
            assert field in row
    custom = next(r for r in rows if r["job_id"] == 4)
    broken_row = next(r for r in rows if r["job_id"] == 1)
    good_row = next(r for r in rows if r["job_id"] == 5)
    assert custom["is_broken"] == ""
    assert broken_row["is_broken"] == "true"
    assert good_row["is_broken"] == "false"
    assert all(r["stage_status"] == "failed" for r in rows)
    assert custom["cluster_prefix"]


# ---------- API ----------


def test_api_requires_auth(client):
    assert client.get("/api/attribution/failures").status_code == 401
    assert client.get("/api/attribution/export").status_code == 401


@pytest.mark.parametrize("user,pw", [("bioops", "fastq123456"), ("auditor", "audit123456")])
def test_api_attribution_readable_by_both_roles(client, user, pw):
    headers = _auth(client, user, pw)
    r = client.get("/api/attribution/failures", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total_failures"] == 5
    top = data["actors"][0]
    assert top["actor_name"] == "ParseActor"
    assert top["failure_count"] == 4

    # 下钻
    prefix = top["clusters"][0]["cluster_prefix"]
    r2 = client.get(
        f"/api/attribution/failures/ParseActor/jobs",
        params={"cluster_prefix": prefix},
        headers=headers,
    )
    assert r2.status_code == 200
    assert {j["job_id"] for j in r2.json()} == {1, 2}


def test_api_attribution_filter_params(client):
    headers = _auth(client, "auditor", "audit123456")
    r = client.get(
        "/api/attribution/failures",
        params={"is_broken": "true", "start_date": "2026-09-10", "end_date": "2026-09-11"},
        headers=headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total_failures"] == 3  # J1(9-10) + J2/J3(9-11)
    assert data["start_date"] == "2026-09-10"
    assert data["is_broken"] is True


def test_api_rejects_invalid_range(client):
    headers = _auth(client, "auditor", "audit123456")
    r = client.get(
        "/api/attribution/failures",
        params={"start_date": "2026-09-20", "end_date": "2026-09-01"},
        headers=headers,
    )
    assert r.status_code == 400


def test_api_export_csv_complete_fields(client):
    headers = _auth(client, "auditor", "audit123456")
    r = client.get("/api/attribution/export", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["x-total-failures"] == "5"

    text = r.content.decode("utf-8-sig")
    lines = text.strip().split("\n")
    header = lines[0].split(",")
    for field in attribution.EXPORT_FIELDS:
        assert field in header
    # 表头 + 5 行数据
    assert len(lines) == 6
    # 仅失败阶段
    body_lines = "\n".join(lines[1:])
    assert "skipped" not in body_lines
    assert "ParseActor" in body_lines


def test_api_export_respects_current_filter(client):
    headers = _auth(client, "bioops", "fastq123456")
    r = client.get("/api/attribution/export", params={"is_broken": "false"}, headers=headers)
    assert r.status_code == 200
    lines = r.content.decode("utf-8-sig").strip().split("\n")
    assert len(lines) == 2  # 表头 + J5
    assert "QualityHistActor" in lines[1]


def test_auditor_cannot_submit_jobs(client):
    headers = _auth(client, "auditor", "audit123456")
    r = client.post("/api/jobs", json={"fastqText": "@a\nACGT\n+\nIIII"}, headers=headers)
    assert r.status_code == 403
