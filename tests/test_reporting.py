from datetime import date, datetime, timezone
from dataclasses import replace
from pathlib import Path

import pytest

import ai_agent_radar.reporting as reporting
from ai_agent_radar.models import NewsRecord, SourceStatus
from ai_agent_radar.reporting import ReportBundle, render_daily, render_weekly, write_report_atomic
from ai_agent_radar.summarize import ProjectSummary
from ai_agent_radar.trends import WeeklyChartAnalysis


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


def test_daily_summary_distinguishes_displayed_items_from_total_ranked(
    report_bundle,
) -> None:
    markdown = render_daily(
        date(2026, 7, 20), report_bundle, top_limit=10, ranked_count=27
    )

    assert "共排名 27 个项目，展示前 1 个" in markdown


def test_daily_explicitly_marks_incomplete_github_discovery(report_bundle) -> None:
    markdown = render_daily(
        date(2026, 7, 20), replace(report_bundle, discovery_complete=False)
    )

    assert "GitHub 发现不完整" in markdown
    assert "不应据此判断项目消失或掉榜" in markdown


def test_report_markdown_has_no_trailing_whitespace(report_bundle) -> None:
    for markdown in (
        render_daily(date(2026, 7, 20), report_bundle),
        render_weekly(date(2026, 7, 20), report_bundle),
    ):
        assert all(line == line.rstrip() for line in markdown.splitlines())


def test_weekly_matches_golden_and_contains_rank_movement_and_recommendations(report_bundle) -> None:
    markdown = render_weekly(date(2026, 7, 20), report_bundle)

    assert markdown == (Path(__file__).parent / "golden" / "weekly.md").read_text(encoding="utf-8")
    for heading in ("综合热度 Top 20", "新上榜", "掉榜", "本周黑马", "连续升温", "值得立即试用", "下周关注"):
        assert f"## {heading}" in markdown


def test_daily_empty_categories_matches_golden(report_bundle) -> None:
    markdown = render_daily(date(2026, 7, 20), replace(report_bundle, categories={}))

    assert markdown == (Path(__file__).parent / "golden" / "daily_empty_categories.md").read_text(encoding="utf-8")


def test_weekly_uses_custom_top_limit_in_heading_and_content(report_bundle) -> None:
    markdown = render_weekly(date(2026, 7, 20), report_bundle, top_limit=5)

    assert "## 综合热度 Top 5" in markdown
    assert "## 综合热度 Top 20" not in markdown


def test_weekly_renders_rank_category_share_and_growth_evidence(report_bundle) -> None:
    analysis = WeeklyChartAnalysis(
        history_sufficient=True,
        prior_chart_date=date(2026, 7, 13),
        new_ids=(1,),
        dropped=(),
        rank_changes={1: 2},
        warming_history_sufficient=True,
        continuous_warming_ids=(1,),
        dark_horse_ids=(1,),
        category_current_shares={"general": 50.0},
        category_share_changes={"general": 25.0},
        growth_history_sufficient=True,
        stars_growth_total=30,
        stars_growth_positive=1,
        stars_growth_flat=0,
    )
    bundle = replace(report_bundle, dropped=(), weekly_analysis=analysis)

    markdown = render_weekly(date(2026, 7, 20), bundle)

    assert "较上期上升 2 位" in markdown
    assert "## 分类榜与份额变化" in markdown
    assert "general：50.0%（较上期 +25.0 个百分点）" in markdown
    assert "## Star 增长趋势" in markdown
    assert "Top 20 合计新增 30 stars" in markdown


def test_weekly_does_not_fabricate_transition_lists_without_history(report_bundle) -> None:
    bundle = replace(
        report_bundle,
        weekly_analysis=WeeklyChartAnalysis.insufficient("仅有当前日期快照"),
    )

    markdown = render_weekly(date(2026, 7, 20), bundle)

    for heading in ("新上榜", "掉榜", "本周黑马", "连续升温", "Star 增长趋势"):
        section = markdown.split(f"## {heading}", 1)[1].split("## ", 1)[0]
        assert "历史数据不足" in section
    assert "old/project" not in markdown


def test_weekly_growth_and_category_fallbacks_report_only_comparable_ranked_items(
    report_bundle, repo_factory, score_factory
) -> None:
    outside_item = (
        repo_factory(repository_id=2, full_name="acme/outside"),
        score_factory(),
        ProjectSummary(
            one_line="分类榜外项目",
            audience="开发者",
            why_now="待观察",
            enhanced=False,
        ),
    )
    analysis = WeeklyChartAnalysis(
        growth_history_sufficient=False,
        growth_comparable_count=1,
        growth_chart_count=2,
    )
    bundle = replace(
        report_bundle,
        categories={"outside": (outside_item,)},
        weekly_analysis=analysis,
    )

    markdown = render_weekly(date(2026, 7, 20), bundle)

    assert "当前榜单仅 1/2 个项目具备可比基线" in markdown
    assert "份额：0.0%" in markdown


def test_weekly_official_updates_exclude_trusted_and_custom_news(report_bundle) -> None:
    trusted = NewsRecord(
        canonical_url="https://trusted.example/news",
        title="Trusted update",
        source="Trusted",
        tier="trusted",
        published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    custom = NewsRecord(
        canonical_url="https://custom.example/news",
        title="Custom update",
        source="Custom",
        tier="custom",
        published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    bundle = replace(report_bundle, news=(*report_bundle.news, trusted, custom))

    daily = render_daily(date(2026, 7, 20), bundle)
    weekly = render_weekly(date(2026, 7, 20), bundle)
    official_section = weekly.split("## 本周重要官方更新", 1)[1].split("## 下周关注", 1)[0]

    assert "Trusted update" in daily
    assert "Custom update" in daily
    assert "Codex update" in official_section
    assert "Trusted update" not in official_section
    assert "Custom update" not in official_section


def test_report_links_keep_valid_http_urls_with_parentheses_clickable(report_bundle, repo_factory, score_factory) -> None:
    item = (
        repo_factory(url="https://example.com/tool_(software)"),
        score_factory(),
        ProjectSummary(one_line="带括号的链接", audience="开发者", why_now="正在升温", enhanced=False),
    )

    markdown = render_daily(date(2026, 7, 20), replace(report_bundle, new=(item,)))

    assert "[acme/agent-skill](https://example.com/tool_\\(software\\))" in markdown


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


def test_write_report_atomic_uses_unique_temp_files_for_each_write(tmp_path, monkeypatch) -> None:
    path = tmp_path / "report.md"
    temporary_paths = []
    original_replace = reporting.os.replace

    def capture_replace(source, destination) -> None:
        temporary_paths.append(Path(source))
        original_replace(source, destination)

    monkeypatch.setattr(reporting.os, "replace", capture_replace)

    write_report_atomic(path, "first")
    write_report_atomic(path, "second")

    assert path.read_text(encoding="utf-8") == "second"
    assert len(set(temporary_paths)) == 2
    assert all(temporary_path.parent == path.parent for temporary_path in temporary_paths)


def test_write_report_atomic_cleans_its_temp_file_when_replacement_fails(tmp_path, monkeypatch) -> None:
    path = tmp_path / "report.md"
    temporary_paths = []

    def fail_replace(source, destination) -> None:
        temporary_paths.append(Path(source))
        raise OSError("replace failed")

    monkeypatch.setattr(reporting.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_report_atomic(path, "content")

    assert len(temporary_paths) == 1
    assert not temporary_paths[0].exists()


@pytest.mark.parametrize("failure_stage", ("write", "close"))
def test_write_report_atomic_cleans_its_temp_file_when_writing_or_closing_fails(
    tmp_path, monkeypatch, failure_stage
) -> None:
    created_path = tmp_path / ".report.md.created.tmp"
    created_path.write_text("partial", encoding="utf-8")

    class FailingTemporaryFile:
        name = str(created_path)

        def __enter__(self):
            return self

        def write(self, content) -> None:
            if failure_stage == "write":
                raise OSError("write failed")

        def __exit__(self, exc_type, exc_value, traceback) -> bool:
            if failure_stage == "close":
                raise OSError("close failed")
            return False

    monkeypatch.setattr(reporting.tempfile, "NamedTemporaryFile", lambda **kwargs: FailingTemporaryFile())

    with pytest.raises(OSError, match=f"{failure_stage} failed"):
        write_report_atomic(tmp_path / "report.md", "content")

    assert not created_path.exists()
