from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ai_agent_radar.models import NewsRecord, SourceStatus
from ai_agent_radar.reporting import ReportBundle, render_daily, render_weekly, write_report_atomic
from ai_agent_radar.summarize import ProjectSummary


@pytest.fixture
def report_bundle(repo_factory, score_factory) -> ReportBundle:
    item = (
        repo_factory(),
        score_factory(),
        ProjectSummary(
            one_line="一个实用 Agent Skill",
            audience="开发者",
            why_now="正在升温",
            enhanced=False,
        ),
    )
    news = NewsRecord(
        canonical_url="https://openai.com/news/codex",
        title="Codex update",
        source="OpenAI",
        tier="official",
        published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    status = SourceStatus(name="github:general", ok=True, item_count=1)
    return ReportBundle(
        ranked=(item,),
        new=(item,),
        rising=(item,),
        useful=(item,),
        dropped=("old/project",),
        categories={"general": (item,)},
        news=(news,),
        statuses=(status,),
    )


def test_daily_matches_golden_and_contains_original_links(report_bundle) -> None:
    markdown = render_daily(date(2026, 7, 20), report_bundle)

    assert markdown == (Path(__file__).parent / "golden" / "daily.md").read_text(encoding="utf-8")
    for heading in ("今日摘要", "今日新发现", "增长最快", "最实用", "分类榜", "官方更新与资讯", "来源状态"):
        assert f"## {heading}" in markdown
    assert "https://github.com/acme/agent-skill" in markdown
    assert "7 日新增" in markdown


def test_weekly_matches_golden_and_contains_rank_movement_and_recommendations(report_bundle) -> None:
    markdown = render_weekly(date(2026, 7, 20), report_bundle)

    assert markdown == (Path(__file__).parent / "golden" / "weekly.md").read_text(encoding="utf-8")
    for heading in ("综合热度 Top 20", "新上榜", "掉榜", "本周黑马", "连续升温", "值得立即试用", "下周关注"):
        assert f"## {heading}" in markdown


def test_reports_escape_untrusted_markdown_and_unsafe_links(repo_factory, score_factory) -> None:
    item = (
        repo_factory(full_name="evil](javascript:alert(1))\n## injected", url="javascript:alert(1)"),
        score_factory(reasons=("reason\n## injected",)),
        ProjectSummary(one_line="[fake](javascript:alert(1))\n## injected", audience="开发者", why_now="现在", enhanced=False),
    )
    news = NewsRecord(
        canonical_url="data:text/html,bad",
        title="[bad](javascript:alert(1))\n## injected",
        source="<untrusted>",
        tier="custom",
        published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    bundle = ReportBundle(ranked=(item,), new=(), rising=(), useful=(), dropped=("bad\n## injected",), categories={}, news=(news,), statuses=())

    markdown = render_weekly(date(2026, 7, 20), bundle)

    assert "](javascript:" not in markdown
    assert "](data:" not in markdown
    assert "\n## injected" not in markdown
    assert "evil\\]" in markdown
    assert "https://github.com/acme/agent-skill" not in markdown


def test_write_report_atomic_overwrites_idempotently(tmp_path) -> None:
    path = tmp_path / "nested" / "report.md"

    write_report_atomic(path, "first")
    write_report_atomic(path, "second")

    assert path.read_text(encoding="utf-8") == "second"
    assert not path.with_suffix(".md.tmp").exists()
