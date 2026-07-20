import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ai_agent_radar.github import GitHubCollection
from ai_agent_radar.models import NewsRecord, RepoSnapshot, SourceStatus
from ai_agent_radar.news import NewsCollection
from ai_agent_radar.pipeline import PipelineDependencies, run_pipeline
from ai_agent_radar.snapshots import load_snapshot, write_snapshot_atomic
from ai_agent_radar.summarize import ProjectSummary


@pytest.fixture
def publish_calls() -> list[tuple[str, str, str]]:
    return []


@pytest.fixture
def fixed_dependencies(repo_factory, publish_calls) -> PipelineDependencies:
    github = GitHubCollection(
        repositories=(
            repo_factory(has_skill_md=True, has_examples=True),
            repo_factory(
                repository_id=2,
                full_name="acme/archived-agent",
                archived=True,
            ),
        ),
        statuses=(SourceStatus(name="github:test", ok=True, item_count=2),),
        rate_remaining=100,
    )
    news = NewsCollection(
        items=(
            NewsRecord(
                canonical_url="https://example.com/shanghai-today",
                title="Shanghai today",
                source="Example",
                tier="trusted",
                published_at=datetime(2026, 7, 19, 16, 30, tzinfo=timezone.utc),
            ),
        ),
        statuses=(SourceStatus(name="feed:test", ok=True, item_count=1),),
    )

    def publish(title: str, body: str, label: str) -> str:
        publish_calls.append((title, body, label))
        return "https://github.com/o/r/issues/1"

    return PipelineDependencies(
        collect_github=lambda config: github,
        collect_news=lambda feeds: news,
        summarize=lambda repo, score: ProjectSummary(
            one_line=repo.description,
            audience="开发者",
            why_now="；".join(score.reasons),
            enhanced=False,
        ),
        publish_issue=publish,
    )


def test_daily_pipeline_writes_original_paths_counts_and_no_issue(
    fixed_dependencies, publish_calls, tmp_path, config_path
) -> None:
    result = run_pipeline(
        "daily",
        date(2026, 7, 20),
        tmp_path,
        config_path,
        fixed_dependencies,
        publish=False,
    )

    assert result.report_path == str(tmp_path / "reports/daily/2026-07-20.md")
    assert result.snapshot_path == str(tmp_path / "data/snapshots/2026-07-20.json")
    assert Path(result.report_path).exists()
    assert Path(result.snapshot_path).exists()
    assert (result.candidates, result.filtered, result.ranked) == (2, 1, 1)
    assert publish_calls == []
    assert "Shanghai today" in Path(result.report_path).read_text(encoding="utf-8")


def test_relative_config_and_repeated_date_overwrite_the_same_outputs(
    fixed_dependencies, tmp_path, config_path
) -> None:
    relative_config = tmp_path / "config/radar.yaml"
    relative_config.parent.mkdir(parents=True)
    relative_config.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")

    first = run_pipeline(
        "daily",
        date(2026, 7, 20),
        tmp_path,
        Path("config/radar.yaml"),
        fixed_dependencies,
    )
    first_report = Path(first.report_path).read_text(encoding="utf-8")
    first_snapshot = Path(first.snapshot_path or "").read_text(encoding="utf-8")
    second = run_pipeline(
        "daily",
        date(2026, 7, 20),
        tmp_path,
        Path("config/radar.yaml"),
        fixed_dependencies,
    )

    assert second.report_path == first.report_path
    assert second.snapshot_path == first.snapshot_path
    assert Path(second.report_path).read_text(encoding="utf-8") == first_report
    assert Path(second.snapshot_path or "").read_text(encoding="utf-8") == first_snapshot
    assert len(list((tmp_path / "reports/daily").glob("*.md"))) == 1
    assert len(list((tmp_path / "data/snapshots").glob("2026-07-20.json"))) == 1


def test_weekly_pipeline_uses_iso_week_history_bundles_and_issue_upsert(
    repo_factory, publish_calls, tmp_path, config_path
) -> None:
    history_path = tmp_path / "data/snapshots/2026-07-19.json"
    write_snapshot_atomic(
        history_path,
        [
            RepoSnapshot(
                report_date=date(2026, 7, 19),
                repository_id=1,
                full_name="acme/agent-skill",
                stars=5,
                forks=0,
                open_issues=0,
                pushed_at=None,
                latest_release=None,
                total_score=40,
            ),
            RepoSnapshot(
                report_date=date(2026, 7, 19),
                repository_id=9,
                full_name="old/dropped-agent",
                stars=100,
                forks=10,
                open_issues=0,
                pushed_at=None,
                latest_release=None,
                total_score=90,
            ),
        ],
    )
    github = GitHubCollection(
        repositories=(repo_factory(stars=15, forks=2, has_skill_md=True),),
        statuses=(SourceStatus(name="github:test", ok=True, item_count=1),),
        rate_remaining=50,
    )

    def publish(title: str, body: str, label: str) -> str:
        publish_calls.append((title, body, label))
        return "https://github.com/o/r/issues/7"

    dependencies = PipelineDependencies(
        collect_github=lambda config: github,
        collect_news=lambda feeds: NewsCollection(items=(), statuses=()),
        summarize=lambda repo, score: ProjectSummary(
            one_line=repo.description,
            audience="开发者",
            why_now="；".join(score.reasons),
            enhanced=False,
        ),
        publish_issue=publish,
    )

    result = run_pipeline(
        "weekly",
        date(2026, 7, 20),
        tmp_path,
        config_path,
        dependencies,
        publish=True,
    )

    assert result.report_path.endswith("reports/weekly/2026-W30.md")
    assert result.issue_url == "https://github.com/o/r/issues/7"
    assert len(publish_calls) == 1
    title, body, label = publish_calls[0]
    assert title == "AI Agent Radar 周榜 · 2026-W30"
    assert label == "radar-weekly"
    assert "old/dropped-agent" in body
    assert "近 1 日新增 10 stars" in body
    assert load_snapshot(Path(result.snapshot_path or ""))[0].total_score > 0


def test_pipeline_merges_source_state_and_compacts_snapshots_after_90_days(
    repo_factory, tmp_path, config_path
) -> None:
    state_path = tmp_path / "data/state/sources.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            [
                SourceStatus(
                    name="github:test",
                    ok=False,
                    error="Timeout",
                    consecutive_failures=1,
                ).model_dump(mode="json")
            ]
        ),
        encoding="utf-8",
    )
    old_path = tmp_path / "data/snapshots/2026-04-20.json"
    write_snapshot_atomic(
        old_path,
        [
            RepoSnapshot(
                report_date=date(2026, 4, 20),
                repository_id=8,
                full_name="old/agent",
                stars=1,
                forks=0,
                open_issues=0,
                pushed_at=None,
                latest_release=None,
            )
        ],
    )
    dependencies = PipelineDependencies(
        collect_github=lambda config: GitHubCollection(
            repositories=(repo_factory(has_skill_md=True),),
            statuses=(SourceStatus(name="github:test", ok=False, error="Timeout"),),
            rate_remaining=None,
        ),
        collect_news=lambda feeds: NewsCollection(items=(), statuses=()),
        summarize=lambda repo, score: ProjectSummary(
            one_line=repo.description,
            audience="开发者",
            why_now="；".join(score.reasons),
            enhanced=False,
        ),
    )

    result = run_pipeline("daily", date(2026, 7, 20), tmp_path, config_path, dependencies)

    assert result.source_statuses[0].consecutive_failures == 2
    assert not old_path.exists()
    assert (tmp_path / "data/snapshots/2026-04.json.gz").exists()


def test_pipeline_rejects_publish_without_publisher_before_collecting(
    tmp_path, config_path
) -> None:
    collected = False

    def collect_github(config):
        nonlocal collected
        collected = True
        return GitHubCollection(repositories=(), statuses=(), rate_remaining=None)

    dependencies = PipelineDependencies(
        collect_github=collect_github,
        collect_news=lambda feeds: NewsCollection(items=(), statuses=()),
        summarize=lambda repo, score: pytest.fail("nothing should be summarized"),
    )

    with pytest.raises(ValueError, match="publish requested without an Issue publisher"):
        run_pipeline(
            "daily",
            date(2026, 7, 20),
            tmp_path,
            config_path,
            dependencies,
            publish=True,
        )

    assert collected is False
