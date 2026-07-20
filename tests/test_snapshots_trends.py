from datetime import date, datetime, timezone

from ai_agent_radar.snapshots import compact_old_snapshots, load_snapshot, write_snapshot_atomic
from ai_agent_radar.trends import calculate_trend


def test_write_snapshot_atomic_round_trips_snapshot_data(tmp_path, snapshot_factory) -> None:
    path = tmp_path / "2026-07-20.json"
    snapshots = [snapshot_factory(stars=150, pushed_at=datetime(2026, 7, 20, tzinfo=timezone.utc))]

    write_snapshot_atomic(path, snapshots)

    assert load_snapshot(path) == snapshots
    assert not path.with_suffix(".json.tmp").exists()


def test_calculate_trend_uses_latest_and_seven_day_baselines(snapshot_factory) -> None:
    current = snapshot_factory(report_date=date(2026, 7, 20), stars=150, forks=20)
    history = [
        snapshot_factory(report_date=date(2026, 7, 19), stars=140, forks=18),
        snapshot_factory(report_date=date(2026, 7, 13), stars=100, forks=10),
    ]

    trend = calculate_trend(current, history)

    assert trend.stars_1d == 10
    assert trend.stars_7d == 50
    assert trend.forks_7d == 10
    assert trend.active_days_7d == 2


def test_calculate_trend_ignores_non_historical_data_and_clamps_negative_deltas(snapshot_factory) -> None:
    current = snapshot_factory(report_date=date(2026, 7, 20), stars=100, forks=10, latest_release="v2")
    history = [
        snapshot_factory(report_date=date(2026, 7, 20), stars=1, forks=1, latest_release="v0"),
        snapshot_factory(report_date=date(2026, 7, 21), stars=1, forks=1, latest_release="v0"),
        snapshot_factory(report_date=date(2026, 7, 19), stars=110, forks=11, latest_release="v1"),
    ]

    trend = calculate_trend(current, history)

    assert trend.stars_1d == 0
    assert trend.stars_7d == 0
    assert trend.forks_1d == 0
    assert trend.forks_7d == 0
    assert trend.active_days_7d == 1
    assert trend.new_release is True


def test_compact_old_snapshots_creates_month_archives_for_old_daily_files(
    tmp_path, snapshot_factory
) -> None:
    first = tmp_path / "2026-01-01.json"
    second = tmp_path / "2026-01-02.json"
    march_snapshot = tmp_path / "2026-03-31.json"
    write_snapshot_atomic(first, [snapshot_factory(report_date=date(2026, 1, 1))])
    write_snapshot_atomic(second, [snapshot_factory(report_date=date(2026, 1, 2))])
    write_snapshot_atomic(march_snapshot, [snapshot_factory(report_date=date(2026, 3, 31))])

    archives = compact_old_snapshots(tmp_path, date(2026, 4, 1))

    assert archives == [tmp_path / "2026-01.json.gz", tmp_path / "2026-03.json.gz"]
    assert all(archive.exists() for archive in archives)
    assert not first.exists() and not second.exists()
    assert not march_snapshot.exists()


def test_compact_old_snapshots_leaves_files_at_or_after_cutoff(tmp_path, snapshot_factory) -> None:
    path = tmp_path / "2026-04-01.json"
    write_snapshot_atomic(path, [snapshot_factory(report_date=date(2026, 4, 1))])

    archives = compact_old_snapshots(tmp_path, date(2026, 4, 1))

    assert archives == []
    assert path.exists()
