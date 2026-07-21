from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import FeedConfig, RadarConfig, load_config
from .filtering import dedupe_repositories, quality_gate
from .github import GitHubCollection
from .models import RepoRecord, RepoSnapshot, RunResult, ScoreBreakdown, TrendMetrics
from .news import NewsCollection
from .reporting import ReportBundle, render_daily, render_weekly, write_report_atomic
from .scoring import rank_repositories, score_repository
from .snapshots import compact_old_snapshots, load_snapshot, write_snapshot_atomic
from .state import merge_source_state
from .summarize import ProjectSummary
from .trends import analyze_weekly_charts, calculate_trend

PipelineRow = tuple[RepoRecord, ScoreBreakdown, ProjectSummary, TrendMetrics]


@dataclass(frozen=True)
class PipelineDependencies:
    collect_github: Callable[[RadarConfig], GitHubCollection]
    collect_news: Callable[[list[FeedConfig]], NewsCollection]
    summarize: Callable[[RepoRecord, ScoreBreakdown], ProjectSummary]
    publish_issue: Callable[[str, str, str], str] | None = None


def _load_history(root: Path, day: date) -> list[RepoSnapshot]:
    history: list[RepoSnapshot] = []
    snapshot_directory = root / "data/snapshots"
    paths = sorted(snapshot_directory.glob("*.json")) + sorted(
        snapshot_directory.glob("*.json.gz")
    )
    for path in paths:
        history.extend(
            snapshot for snapshot in load_snapshot(path) if snapshot.report_date < day
        )
    deduplicated = {
        (snapshot.report_date, snapshot.repository_id): snapshot for snapshot in history
    }
    return list(deduplicated.values())


def _report_items(
    rows: list[PipelineRow], limit: int
) -> tuple[tuple[RepoRecord, ScoreBreakdown, ProjectSummary], ...]:
    return tuple((repo, score, summary) for repo, score, summary, _ in rows[:limit])


def _report_window(
    mode: str, day: date, local_zone: ZoneInfo
) -> tuple[datetime, datetime]:
    end_time = time(hour=8, minute=30 if mode == "weekly" else 0)
    end = datetime.combine(day, end_time, tzinfo=local_zone).astimezone(timezone.utc)
    return end - timedelta(days=7 if mode == "weekly" else 1), end


def run_pipeline(
    mode: str,
    day: date,
    root: Path,
    config_path: Path,
    dependencies: PipelineDependencies,
    publish: bool = False,
) -> RunResult:
    if mode not in {"daily", "weekly"}:
        raise ValueError("mode must be daily or weekly")
    if publish:
        raise ValueError("generation cannot publish; publish existing report after repository push")

    resolved_config = config_path if config_path.is_absolute() else root / config_path
    config = load_config(resolved_config)
    local_zone = ZoneInfo(config.timezone)
    news_start, news_end = _report_window(mode, day, local_zone)
    now = news_end
    github_result = dependencies.collect_github(config)
    news_result = dependencies.collect_news(config.feeds)
    accepted, rejected = quality_gate(
        dedupe_repositories(list(github_result.repositories)), config, now=now
    )
    history = _load_history(root, day)

    rows: list[PipelineRow] = []
    snapshots_by_id: dict[int, RepoSnapshot] = {}
    for repo in accepted:
        draft = RepoSnapshot(
            report_date=day,
            repository_id=repo.repository_id,
            full_name=repo.full_name,
            stars=repo.stars,
            forks=repo.forks,
            open_issues=repo.open_issues,
            pushed_at=repo.pushed_at,
            latest_release=repo.latest_release,
            categories=repo.matched_categories,
            discovery_complete=github_result.complete,
        )
        trend = calculate_trend(draft, history)
        score = score_repository(repo, trend, config.weights, now)
        rows.append((repo, score, dependencies.summarize(repo, score), trend))
        snapshots_by_id[repo.repository_id] = draft.model_copy(
            update={"total_score": score.total}
        )

    ranked = rank_repositories([(repo, score) for repo, score, _, _ in rows])
    rank_by_id = {repo.repository_id: index for index, (repo, _) in enumerate(ranked)}
    rows.sort(key=lambda row: rank_by_id[row[0].repository_id])
    limit = config.limits.daily_top if mode == "daily" else config.limits.weekly_top
    current_snapshots = [snapshots_by_id[row[0].repository_id] for row in rows]
    weekly_analysis = (
        analyze_weekly_charts(current_snapshots, history, limit)
        if mode == "weekly"
        else None
    )
    if weekly_analysis is not None:
        rows = [
            (
                repo,
                score,
                summary,
                trend.model_copy(
                    update={"rank_change": weekly_analysis.rank_changes.get(repo.repository_id)}
                ),
            )
            for repo, score, summary, trend in rows
        ]
        new_ids = set(weekly_analysis.new_ids)
        warming_ids = set(weekly_analysis.continuous_warming_ids)
        new_rows = [row for row in rows if row[0].repository_id in new_ids]
        rising_rows = [row for row in rows if row[0].repository_id in warming_ids]
        dropped = tuple(item.full_name for item in weekly_analysis.dropped)
    else:
        new_rows = [row for row in rows if row[3].first_seen]
        rising_rows = sorted(rows, key=lambda row: (-row[1].heat, -row[1].total))
        dropped = ()
    useful_rows = sorted(rows, key=lambda row: (-row[1].utility, -row[1].total))
    categories = {
        category: _report_items(
            [row for row in rows if category in row[0].matched_categories], limit
        )
        for category in config.queries
    }

    fresh_news = tuple(
        item
        for item in news_result.items
        if news_start <= item.published_at.astimezone(timezone.utc) <= news_end
    )
    statuses = merge_source_state(
        root / "data/state/sources.json",
        github_result.statuses + news_result.statuses,
        now,
    )
    bundle = ReportBundle(
        ranked=_report_items(rows, limit),
        new=_report_items(new_rows, limit),
        rising=_report_items(rising_rows, limit),
        useful=_report_items(useful_rows, min(5, limit)),
        dropped=dropped,
        categories=categories,
        news=fresh_news,
        statuses=statuses,
        weekly_analysis=weekly_analysis,
        discovery_complete=github_result.complete,
    )

    iso_year, iso_week, _ = day.isocalendar()
    filename = (
        f"{day.isoformat()}.md"
        if mode == "daily"
        else f"{iso_year}-W{iso_week:02d}.md"
    )
    report_path = root / "reports" / mode / filename
    snapshot_path = root / "data/snapshots" / f"{day.isoformat()}.json"
    markdown = (
        render_daily(day, bundle, top_limit=limit, ranked_count=len(rows))
        if mode == "daily"
        else render_weekly(day, bundle, top_limit=limit)
    )
    write_report_atomic(report_path, markdown)
    stored_snapshot_path: Path | None = snapshot_path
    existing_valid = _has_valid_snapshot(snapshot_path)
    if github_result.complete:
        write_snapshot_atomic(snapshot_path, current_snapshots)
    elif existing_valid:
        pass
    elif github_result.queries_succeeded > 0 and current_snapshots:
        write_snapshot_atomic(snapshot_path, current_snapshots)
    else:
        stored_snapshot_path = snapshot_path if snapshot_path.exists() else None
    compact_old_snapshots(snapshot_path.parent, day - timedelta(days=90))

    sources_succeeded = sum(status.ok for status in statuses)
    sources_failed = len(statuses) - sources_succeeded

    return RunResult(
        report_path=str(report_path),
        snapshot_path=str(stored_snapshot_path) if stored_snapshot_path else None,
        issue_url=None,
        candidates=len(github_result.repositories),
        filtered=len(rejected),
        ranked=len(rows),
        source_statuses=statuses,
        github_discovery_complete=github_result.complete,
        github_queries_succeeded=github_result.queries_succeeded,
        github_queries_total=github_result.queries_total,
        sources_succeeded=sources_succeeded,
        sources_failed=sources_failed,
        degraded=not github_result.complete or sources_failed > 0,
    )


def _has_valid_snapshot(path: Path) -> bool:
    if not path.exists():
        return False
    snapshots = load_snapshot(path)
    return bool(snapshots) and all(snapshot.discovery_complete for snapshot in snapshots)
