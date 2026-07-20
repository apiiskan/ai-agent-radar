from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Mapping

from .models import RepoSnapshot, TrendMetrics


@dataclass(frozen=True)
class WeeklyChartAnalysis:
    history_sufficient: bool = False
    prior_chart_date: date | None = None
    new_ids: tuple[int, ...] = ()
    dropped: tuple[RepoSnapshot, ...] = ()
    rank_changes: Mapping[int, int] = field(default_factory=dict)
    warming_history_sufficient: bool = False
    continuous_warming_ids: tuple[int, ...] = ()
    dark_horse_ids: tuple[int, ...] = ()
    category_current_shares: Mapping[str, float] = field(default_factory=dict)
    category_share_changes: Mapping[str, float] = field(default_factory=dict)
    growth_history_sufficient: bool = False
    growth_comparable_count: int = 0
    growth_chart_count: int = 0
    stars_growth_total: int = 0
    stars_growth_positive: int = 0
    stars_growth_flat: int = 0
    insufficient_reason: str | None = None

    @classmethod
    def insufficient(cls, reason: str) -> "WeeklyChartAnalysis":
        return cls(insufficient_reason=reason)


def calculate_trend(current: RepoSnapshot, history: list[RepoSnapshot]) -> TrendMetrics:
    prior_snapshots = sorted(
        (
            snapshot
            for snapshot in history
            if snapshot.repository_id == current.repository_id
            and snapshot.report_date < current.report_date
        ),
        key=lambda snapshot: snapshot.report_date,
    )
    previous = prior_snapshots[-1] if prior_snapshots else None
    cutoff = current.report_date - timedelta(days=7)
    baseline = next(
        (snapshot for snapshot in prior_snapshots if snapshot.report_date >= cutoff),
        prior_snapshots[0] if prior_snapshots else None,
    )
    active_days = len(
        {snapshot.report_date for snapshot in prior_snapshots if snapshot.report_date >= cutoff}
    )

    return TrendMetrics(
        stars_1d=max(0, current.stars - previous.stars) if previous else 0,
        stars_7d=max(0, current.stars - baseline.stars) if baseline else 0,
        forks_1d=max(0, current.forks - previous.forks) if previous else 0,
        forks_7d=max(0, current.forks - baseline.forks) if baseline else 0,
        active_days_7d=active_days,
        first_seen=not prior_snapshots,
        new_release=bool(
            previous
            and current.latest_release is not None
            and current.latest_release != previous.latest_release
        ),
    )


def analyze_weekly_charts(
    current: list[RepoSnapshot],
    history: list[RepoSnapshot],
    top_limit: int,
) -> WeeklyChartAnalysis:
    """Compare valid dated charts and derive evidence-based weekly claims."""
    if top_limit < 1:
        raise ValueError("top_limit must be at least 1")

    current_chart = _ranked(current, top_limit)
    current_shares = _category_shares(current_chart)
    if not current_chart:
        return WeeklyChartAnalysis(
            category_current_shares=current_shares,
            insufficient_reason="当前榜单为空",
        )
    if not all(snapshot.discovery_complete for snapshot in current):
        return WeeklyChartAnalysis(
            category_current_shares=current_shares,
            insufficient_reason="GitHub 发现不完整，已抑制榜单变化判断",
        )

    history_by_day = _complete_days(history)
    prior_dates = [day for day in history_by_day if day < current_chart[0].report_date]
    if not prior_dates:
        return WeeklyChartAnalysis(
            category_current_shares=current_shares,
            insufficient_reason="仅有当前日期快照",
        )

    prior_date = max(prior_dates)
    prior_chart = _ranked(history_by_day[prior_date], top_limit)
    current_rank = {item.repository_id: rank for rank, item in enumerate(current_chart, 1)}
    prior_rank = {item.repository_id: rank for rank, item in enumerate(prior_chart, 1)}
    new_ids = tuple(item.repository_id for item in current_chart if item.repository_id not in prior_rank)
    dropped = tuple(item for item in prior_chart if item.repository_id not in current_rank)
    rank_changes = {
        repository_id: prior_rank[repository_id] - current_rank[repository_id]
        for repository_id in current_rank.keys() & prior_rank.keys()
    }
    prior_shares = _category_shares(prior_chart)
    categories = sorted(current_shares.keys() | prior_shares.keys())
    share_changes = {
        category: round(current_shares.get(category, 0.0) - prior_shares.get(category, 0.0), 1)
        for category in categories
    }

    current_day = current_chart[0].report_date
    series_by_day = {
        day: snapshots
        for day, snapshots in history_by_day.items()
        if current_day - timedelta(days=7) <= day < current_day
    }
    series_by_day[current_day] = current
    series_days = sorted(series_by_day)
    warming_sufficient = len(series_days) >= 4
    warming_ids: tuple[int, ...] = ()
    if warming_sufficient:
        last_days = series_days[-4:]
        warming_ids = tuple(
            item.repository_id
            for item in current_chart
            if _has_repeated_positive_growth(
                item.repository_id, last_days, series_by_day
            )
        )

    baseline_dates = [
        day
        for day in series_days
        if current_day - timedelta(days=7) <= day <= current_day - timedelta(days=6)
    ]
    growth_sufficient = bool(baseline_dates)
    growth_by_id: dict[int, tuple[int, int]] = {}
    if growth_sufficient:
        baseline = _by_id(series_by_day[min(baseline_dates)])
        for item in current_chart:
            old = baseline.get(item.repository_id)
            if old is not None:
                growth_by_id[item.repository_id] = (old.stars, max(0, item.stars - old.stars))
        growth_sufficient = len(growth_by_id) == len(current_chart)

    dark_horse_ids = tuple(
        repository_id
        for repository_id in new_ids
        if repository_id in growth_by_id
        and growth_by_id[repository_id][1] >= 20
        and (
            growth_by_id[repository_id][0] == 0
            or growth_by_id[repository_id][1] / growth_by_id[repository_id][0] >= 0.5
        )
    )
    growth_values = [growth for _, growth in growth_by_id.values()]
    return WeeklyChartAnalysis(
        history_sufficient=True,
        prior_chart_date=prior_date,
        new_ids=new_ids,
        dropped=dropped,
        rank_changes=rank_changes,
        warming_history_sufficient=warming_sufficient,
        continuous_warming_ids=warming_ids,
        dark_horse_ids=dark_horse_ids,
        category_current_shares=current_shares,
        category_share_changes=share_changes,
        growth_history_sufficient=growth_sufficient,
        growth_comparable_count=len(growth_by_id),
        growth_chart_count=len(current_chart),
        stars_growth_total=sum(growth_values),
        stars_growth_positive=sum(value > 0 for value in growth_values),
        stars_growth_flat=sum(value == 0 for value in growth_values),
    )


def _ranked(snapshots: list[RepoSnapshot], limit: int) -> list[RepoSnapshot]:
    return sorted(
        snapshots,
        key=lambda item: (
            -item.total_score,
            -item.stars,
            item.full_name.casefold(),
            item.repository_id,
        ),
    )[:limit]


def _complete_days(history: list[RepoSnapshot]) -> dict[date, list[RepoSnapshot]]:
    grouped: dict[date, list[RepoSnapshot]] = {}
    for snapshot in history:
        grouped.setdefault(snapshot.report_date, []).append(snapshot)
    return {
        day: snapshots
        for day, snapshots in grouped.items()
        if snapshots and all(snapshot.discovery_complete for snapshot in snapshots)
    }


def _category_shares(chart: list[RepoSnapshot]) -> dict[str, float]:
    if not chart:
        return {}
    counts: dict[str, int] = {}
    for snapshot in chart:
        for category in set(snapshot.categories):
            counts[category] = counts.get(category, 0) + 1
    return {
        category: round(count * 100 / len(chart), 1)
        for category, count in sorted(counts.items())
    }


def _by_id(snapshots: list[RepoSnapshot]) -> dict[int, RepoSnapshot]:
    return {snapshot.repository_id: snapshot for snapshot in snapshots}


def _has_repeated_positive_growth(
    repository_id: int,
    days: list[date],
    snapshots_by_day: Mapping[date, list[RepoSnapshot]],
) -> bool:
    series: list[int] = []
    for day in days:
        snapshot = _by_id(snapshots_by_day[day]).get(repository_id)
        if snapshot is None:
            return False
        series.append(snapshot.stars)
    deltas = [later - earlier for earlier, later in zip(series, series[1:])]
    return len(deltas) == 3 and all(delta > 0 for delta in deltas) and sum(deltas) >= 3
