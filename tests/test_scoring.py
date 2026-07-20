from datetime import datetime, timedelta, timezone

import pytest

from ai_agent_radar.config import WeightConfig
from ai_agent_radar.scoring import rank_repositories, score_repository


def test_score_is_weighted_and_explainable(repo_factory, trend_factory, radar_config) -> None:
    repo = repo_factory(
        stars=400,
        readme="Install\nExample",
        has_skill_md=True,
        has_examples=True,
        has_tests=True,
        license_spdx="MIT",
        pushed_at="2026-07-19T00:00:00Z",
    )
    trend = trend_factory(stars_1d=35, stars_7d=180, forks_7d=12, new_release=True)

    score = score_repository(repo, trend, radar_config.weights, datetime(2026, 7, 20, tzinfo=timezone.utc))

    assert 0 <= score.total <= 100
    assert score.total == round(score.heat * 0.45 + score.utility * 0.25 + score.freshness * 0.20 + score.relevance * 0.10, 2)
    assert any("7 日新增 180 stars" in reason for reason in score.reasons)
    assert any("SKILL.md" in reason for reason in score.reasons)
    assert "热度：发现新版本" in score.reasons


def test_zero_star_complete_new_project_can_rank(repo_factory, trend_factory, radar_config) -> None:
    repo = repo_factory(
        stars=0,
        has_skill_md=True,
        has_examples=True,
        has_tests=True,
        license_spdx="MIT",
        readme="Install and usage",
    )

    score = score_repository(repo, trend_factory(first_seen=True), radar_config.weights, datetime(2026, 7, 20, tzinfo=timezone.utc))

    assert score.utility >= 70
    assert score.freshness >= 50


def test_rank_repositories_uses_stable_total_star_name_order(repo_factory, score_factory) -> None:
    lower_stars = repo_factory(full_name="acme/Alpha", stars=5)
    higher_stars = repo_factory(repository_id=2, full_name="acme/beta", stars=10)
    lower_name = repo_factory(repository_id=3, full_name="acme/alpha", stars=10)
    score = score_factory(total=75)

    ranked = rank_repositories([(lower_stars, score), (higher_stars, score), (lower_name, score)])

    assert [repo.full_name for repo, _ in ranked] == ["acme/alpha", "acme/beta", "acme/Alpha"]


def test_rank_repositories_breaks_casefolded_name_ties_by_repository_id(repo_factory, score_factory) -> None:
    later_id = repo_factory(repository_id=2, full_name="acme/Agent", stars=10)
    earlier_id = repo_factory(repository_id=1, full_name="acme/agent", stars=10)
    score = score_factory(total=75)

    ranked = rank_repositories([(later_id, score), (earlier_id, score)])

    assert [repo.repository_id for repo, _ in ranked] == [1, 2]


def test_score_rejects_weights_that_do_not_total_100(repo_factory, trend_factory) -> None:
    with pytest.raises(ValueError, match="weights must total 100"):
        score_repository(
            repo_factory(),
            trend_factory(),
            WeightConfig(heat=100, utility=100, freshness=100, relevance=100),
            datetime(2026, 7, 20, tzinfo=timezone.utc),
        )


def test_negative_trend_deltas_are_clamped_before_logarithmic_scoring(repo_factory, trend_factory, radar_config) -> None:
    score = score_repository(
        repo_factory(),
        trend_factory(stars_1d=-1, stars_7d=-2, forks_7d=-3),
        radar_config.weights,
        datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    assert score.heat == 0
    assert 0 <= score.total <= 100


def test_push_after_report_cutoff_is_clamped_to_zero_days(
    repo_factory, trend_factory, radar_config
) -> None:
    cutoff = datetime(2026, 7, 20, tzinfo=timezone.utc)
    score = score_repository(
        repo_factory(pushed_at=cutoff + timedelta(hours=6)),
        trend_factory(),
        radar_config.weights,
        cutoff,
    )

    assert "新鲜度：基础分；最近推送距今 0 天" in score.reasons
    assert all("距今 -" not in reason for reason in score.reasons)


def test_reasons_describe_actual_and_missing_evidence(repo_factory, trend_factory, radar_config) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    repo = repo_factory(
        description="",
        topics=(),
        readme="",
        license_spdx=None,
        has_skill_md=False,
        has_mcp=False,
        has_examples=False,
        has_tests=False,
        matched_categories=(),
        created_at=now - timedelta(days=365),
        pushed_at=now - timedelta(days=365),
        updated_at=now - timedelta(days=365),
    )

    score = score_repository(repo, trend_factory(), radar_config.weights, now)

    assert "热度：未检测到近 1 日或 7 日增长" in score.reasons
    assert "实用性：未检测到 README、许可证、技能/MCP、示例或测试" in score.reasons
    assert "新鲜度：基础分；最近推送距今 365 天" in score.reasons
    assert "相关性：未找到分类、主题或关键词证据" in score.reasons
    assert all("质量门槛" not in reason and "主题相关且" not in reason for reason in score.reasons)
