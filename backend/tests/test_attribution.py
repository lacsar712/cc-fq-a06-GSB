"""Unit tests for failure-attribution aggregation (no DB required)."""

from datetime import datetime, timezone

from app.attribution import FailureRow, build_attribution, message_prefix


def test_prefix_cuts_at_chinese_colon():
    assert message_prefix("FASTQ 记录不完整：从第 1 行起不足 4 行（畸形输入）") == "FASTQ 记录不完整"


def test_prefix_normalizes_digits_and_quotes():
    assert (
        message_prefix("第 3 行分隔符必须以 + 开头，实际为: 'NOTPLUS'")
        == "第 # 行分隔符必须以 + 开头"
    )
    assert message_prefix("序列含非法碱基 'X'（读段 @A）") == "序列含非法碱基 '?'"
    assert message_prefix("序列含非法碱基 'Y'（读段 @B）") == "序列含非法碱基 '?'"


def test_prefix_no_delimiter_and_empty():
    assert message_prefix("FASTQ 内容为空") == "FASTQ 内容为空"
    assert message_prefix("") == "（无消息）"
    assert message_prefix(None) == "（无消息）"


def _row(job_id, actor, message, ts, broken=True):
    return FailureRow(
        job_id=job_id,
        sample_name=f"sample-{job_id}",
        is_broken=broken,
        created_by="bioops",
        job_created_at=datetime(2026, 9, 20, ts, 0, 0, tzinfo=timezone.utc),
        actor_name=actor,
        message=message,
        stage_finished_at=None,
    )


def test_build_attribution_groups_and_clusters():
    rows = [
        _row(1, "ParseActor", "FASTQ 记录不完整：从第 1 行起不足 4 行（畸形输入）", 1),
        _row(2, "ParseActor", "FASTQ 记录不完整：从第 5 行起不足 4 行（畸形输入）", 2),
        _row(3, "ParseActor", "第 3 行分隔符必须以 + 开头，实际为: 'XX'", 3),
        _row(4, "QualityHistActor", "质量统计失败: boom", 4, broken=False),
    ]
    out = build_attribution(rows)
    assert out["total_failures"] == 4
    assert [a["actor_name"] for a in out["actors"]] == ["ParseActor", "QualityHistActor"]

    parse = out["actors"][0]
    assert parse["failure_count"] == 3
    assert len(parse["clusters"]) == 2
    top = parse["clusters"][0]
    assert top["prefix"] == "FASTQ 记录不完整"
    assert top["size"] == 2
    # most recent job first
    assert [j["job_id"] for j in top["recent_jobs"]] == [2, 1]
    assert top["recent_jobs"][0]["is_broken"] is True

    qual = out["actors"][1]
    assert qual["failure_count"] == 1
    assert qual["clusters"][0]["prefix"] == "质量统计失败"


def test_build_attribution_recent_limit():
    rows = [_row(i, "ParseActor", "FASTQ 内容为空", i % 24) for i in range(1, 9)]
    out = build_attribution(rows, recent_limit=5)
    cluster = out["actors"][0]["clusters"][0]
    assert cluster["size"] == 8
    assert len(cluster["recent_jobs"]) == 5
    assert cluster["recent_jobs"][0]["job_id"] == 8


def test_build_attribution_empty():
    assert build_attribution([]) == {"total_failures": 0, "actors": []}
