from datetime import date, datetime, timezone

from ai_agent_radar.snapshots import compact_old_snapshots, load_snapshot, write_snapshot_atomic
from ai_agent_radar.trends import analyze_weekly_charts, calculate_trend


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


def test_release_detail_loss_does_not_fabricate_a_new_release(snapshot_factory) -> None:
    current = snapshot_factory(
        report_date=date(2026, 7, 20),
        latest_release=None,
    )
    history = [
        snapshot_factory(report_date=date(2026, 7, 19), latest_release="v1.0.0")
    ]

    trend = calculate_trend(current, history)

    assert trend.new_release is False


def test_weekly_chart_compares_top_membership_and_populates_rank_change(
    snapshot_factory,
) -> None:
    history = [
        snapshot_factory(
            report_date=date(2026, 7, 19),
            repository_id=1,
            full_name="acme/one",
            total_score=90,
            categories=("codex",),
        ),
        snapshot_factory(
            report_date=date(2026, 7, 19),
            repository_id=2,
            full_name="acme/two",
            total_score=80,
            categories=("codex",),
        ),
    ]
    current = [
        snapshot_factory(
            repository_id=2,
            full_name="acme/two",
            total_score=95,
            categories=("codex",),
        ),
        snapshot_factory(
            repository_id=3,
            full_name="acme/three",
            total_score=85,
            categories=("general",),
        ),
    ]

    analysis = analyze_weekly_charts(current, history, top_limit=2)

    assert analysis.history_sufficient is True
    assert analysis.new_ids == (3,)
    assert tuple(item.repository_id for item in analysis.dropped) == (1,)
    assert analysis.rank_changes == {2: 1}
    assert analysis.category_share_changes == {"codex": -50.0, "general": 50.0}


def test_weekly_chart_uses_the_same_star_tiebreak_as_formal_ranking(
    snapshot_factory,
) -> None:
    history = [
        snapshot_factory(
            report_date=date(2026, 7, 19),
            repository_id=1,
            full_name="zeta/high-star",
            stars=100,
            total_score=50,
        ),
        snapshot_factory(
            report_date=date(2026, 7, 19),
            repository_id=2,
            full_name="alpha/low-star",
            stars=10,
            total_score=50,
        ),
    ]
    current = [
        snapshot_factory(
            repository_id=1,
            full_name="zeta/high-star",
            stars=100,
            total_score=50,
        ),
        snapshot_factory(
            repository_id=2,
            full_name="alpha/low-star",
            stars=110,
            total_score=50,
        ),
    ]

    analysis = analyze_weekly_charts(current, history, top_limit=1)

    assert analysis.new_ids == (2,)
    assert tuple(item.repository_id for item in analysis.dropped) == (1,)


def test_weekly_warming_requires_three_repeated_positive_intervals_and_excludes_flat(
    snapshot_factory,
) -> None:
    history = []
    for snapshot_day, warming_stars, flat_stars in (
        (date(2026, 7, 17), 10, 10),
        (date(2026, 7, 18), 12, 10),
        (date(2026, 7, 19), 15, 10),
    ):
        history.extend(
            [
                snapshot_factory(
                    report_date=snapshot_day,
                    repository_id=1,
                    full_name="acme/warming",
                    stars=warming_stars,
                    total_score=80,
                ),
                snapshot_factory(
                    report_date=snapshot_day,
                    repository_id=2,
                    full_name="acme/flat",
                    stars=flat_stars,
                    total_score=70,
                ),
            ]
        )
    current = [
        snapshot_factory(
            repository_id=1,
            full_name="acme/warming",
            stars=19,
            total_score=90,
        ),
        snapshot_factory(
            repository_id=2,
            full_name="acme/flat",
            stars=10,
            total_score=85,
        ),
    ]

    analysis = analyze_weekly_charts(current, history, top_limit=2)

    assert analysis.warming_history_sufficient is True
    assert analysis.continuous_warming_ids == (1,)


def test_weekly_dark_horse_requires_new_entry_absolute_and_relative_growth(
    snapshot_factory,
) -> None:
    history = []
    for snapshot_day, incumbent_stars, entrant_stars in (
        (date(2026, 7, 13), 100, 10),
        (date(2026, 7, 17), 103, 12),
        (date(2026, 7, 19), 105, 15),
    ):
        history.extend(
            [
                snapshot_factory(
                    report_date=snapshot_day,
                    repository_id=1,
                    full_name="acme/incumbent",
                    stars=incumbent_stars,
                    total_score=90,
                ),
                snapshot_factory(
                    report_date=snapshot_day,
                    repository_id=2,
                    full_name="acme/entrant",
                    stars=entrant_stars,
                    total_score=10,
                ),
            ]
        )
    current = [
        snapshot_factory(
            repository_id=1,
            full_name="acme/incumbent",
            stars=106,
            total_score=95,
        ),
        snapshot_factory(
            repository_id=2,
            full_name="acme/entrant",
            stars=40,
            total_score=99,
        ),
    ]

    analysis = analyze_weekly_charts(current, history, top_limit=1)

    assert analysis.new_ids == (2,)
    assert analysis.dark_horse_ids == (2,)
    assert analysis.growth_history_sufficient is True
    assert analysis.stars_growth_total == 30


def test_weekly_growth_summary_requires_a_baseline_for_every_chart_item(
    snapshot_factory,
) -> None:
    history = [
        snapshot_factory(
            report_date=date(2026, 7, 13),
            repository_id=1,
            full_name="acme/comparable",
            stars=10,
            total_score=50,
        )
    ]
    current = [
        snapshot_factory(
            repository_id=1,
            full_name="acme/comparable",
            stars=20,
            total_score=90,
        ),
        snapshot_factory(
            repository_id=2,
            full_name="acme/new",
            stars=30,
            total_score=80,
        ),
    ]

    analysis = analyze_weekly_charts(current, history, top_limit=2)

    assert analysis.growth_history_sufficient is False
    assert analysis.growth_comparable_count == 1
    assert analysis.growth_chart_count == 2


def test_weekly_analysis_marks_first_snapshot_as_insufficient(snapshot_factory) -> None:
    analysis = analyze_weekly_charts(
        [snapshot_factory(total_score=90)], [], top_limit=20
    )

    assert analysis.history_sufficient is False
    assert analysis.warming_history_sufficient is False
    assert analysis.growth_history_sufficient is False
    assert analysis.new_ids == ()
    assert analysis.dropped == ()
    assert analysis.continuous_warming_ids == ()
    assert analysis.dark_horse_ids == ()


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
