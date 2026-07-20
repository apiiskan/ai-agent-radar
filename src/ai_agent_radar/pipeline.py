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
from .trends import calculate_trend

PipelineRow = tuple[RepoRecord, ScoreBreakdown, ProjectSummary, TrendMetrics]


@dataclass(frozen=True)
class PipelineDependencies:
    collect_github: Callable[[RadarConfig], GitHubCollection]
    collect_news: Callable[[list[FeedConfig]], NewsCollection]
    summarize: Callable[[RepoRecord, ScoreBreakdown], ProjectSummary]
    publish_issue: Callable[[str, str, str], str] | None = None


def _load_history(root: Path, day: date) -> list[RepoSnapshot]:
    history: list[RepoSnapshot] = []
    for path in sorted((root / "data/snapshots").glob("*.json")):
        try:
            snapshot_day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if snapshot_day < day:
            history.extend(load_snapshot(path))
    return history


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
    if publish and dependencies.publish_issue is None:
        raise ValueError("publish requested without an Issue publisher")

    resolved_config = config_path if config_path.is_absolute() else root / config_path
    config = load_config(resolved_config)
    github_result = dependencies.collect_github(config)
    news_result = dependencies.collect_news(config.feeds)
    accepted, rejected = quality_gate(
        dedupe_repositories(list(github_result.repositories)), config
    )
    history = _load_history(root, day)
    local_zone = ZoneInfo(config.timezone)
    news_start, news_end = _report_window(mode, day, local_zone)
    now = news_end

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
    new_rows = [row for row in rows if row[3].first_seen]
    rising_rows = sorted(rows, key=lambda row: (-row[1].heat, -row[1].total))
    useful_rows = sorted(rows, key=lambda row: (-row[1].utility, -row[1].total))
    categories = {
        category: _report_items(
            [row for row in rows if category in row[0].matched_categories], limit
        )
        for category in config.queries
    }

    latest_history_day = max((item.report_date for item in history), default=None)
    previous_ranked = [
        item for item in history if item.report_date == latest_history_day
    ]
    current_ids = {repo.repository_id for repo in accepted}
    dropped = tuple(
        item.full_name
        for item in sorted(
            previous_ranked,
            key=lambda item: (-item.total_score, item.full_name.casefold(), item.repository_id),
        )
        if item.repository_id not in current_ids
    )

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
        render_daily(day, bundle, top_limit=limit)
        if mode == "daily"
        else render_weekly(day, bundle, top_limit=limit)
    )
    write_report_atomic(report_path, markdown)
    write_snapshot_atomic(
        snapshot_path,
        [snapshots_by_id[row[0].repository_id] for row in rows],
    )
    compact_old_snapshots(snapshot_path.parent, day - timedelta(days=90))

    issue_url = None
    if publish:
        label = "radar-daily" if mode == "daily" else "radar-weekly"
        period = filename.removesuffix(".md")
        title = f"AI Agent Radar {'日报' if mode == 'daily' else '周榜'} · {period}"
        relative_report = report_path.relative_to(root)
        issue_body = (
            markdown
            if len(markdown) <= 60_000
            else markdown[:58_000] + f"\n\n完整报告：`{relative_report}`"
        )
        assert dependencies.publish_issue is not None
        issue_url = dependencies.publish_issue(title, issue_body, label)

    return RunResult(
        report_path=str(report_path),
        snapshot_path=str(snapshot_path),
        issue_url=issue_url,
        candidates=len(github_result.repositories),
        filtered=len(rejected),
        ranked=len(rows),
        source_statuses=statuses,
    )
