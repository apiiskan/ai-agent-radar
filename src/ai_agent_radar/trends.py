from datetime import timedelta

from .models import RepoSnapshot, TrendMetrics


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
        new_release=bool(previous and current.latest_release != previous.latest_release),
    )
