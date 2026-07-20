from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ai_agent_radar.config import ExclusionConfig, LimitConfig, RadarConfig, WeightConfig
from ai_agent_radar.models import RepoRecord, RepoSnapshot, ScoreBreakdown, TrendMetrics


@pytest.fixture
def repo_factory():
    def factory(**overrides) -> RepoRecord:
        data = dict(repository_id=1, full_name="acme/agent-skill", url="https://github.com/acme/agent-skill",
                    description="Agent skill", topics=("agents",), stars=10, forks=1, open_issues=0, watchers=10,
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
                    pushed_at=datetime(2026, 7, 19, tzinfo=timezone.utc), archived=False, fork=False,
                    license_spdx="MIT", language="Python", readme="Install and usage",
                    matched_categories=("general",))
        data.update(overrides)
        return RepoRecord(**data)
    return factory


@pytest.fixture
def snapshot_factory():
    def factory(**overrides) -> RepoSnapshot:
        data = dict(report_date=date(2026, 7, 20), repository_id=1, full_name="acme/agent-skill",
                    stars=10, forks=1, open_issues=0, pushed_at=None, latest_release=None)
        data.update(overrides)
        return RepoSnapshot(**data)
    return factory


@pytest.fixture
def trend_factory():
    def factory(**overrides) -> TrendMetrics:
        data = TrendMetrics().model_dump()
        data.update(overrides)
        return TrendMetrics(**data)
    return factory


@pytest.fixture
def score_factory():
    def factory(**overrides) -> ScoreBreakdown:
        data = dict(heat=50, utility=80, freshness=60, relevance=70, total=62,
                    reasons=("7 日新增 10 stars",))
        data.update(overrides)
        return ScoreBreakdown(**data)
    return factory


@pytest.fixture
def radar_config() -> RadarConfig:
    return RadarConfig(timezone="Asia/Shanghai", queries={"general": ["agent skill"]}, feeds=[],
                       weights=WeightConfig(heat=45, utility=25, freshness=20, relevance=10),
                       limits=LimitConfig(search_per_query=20, daily_top=10, weekly_top=20),
                       exclusions=ExclusionConfig())


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "radar.yaml"
    path.write_text("""timezone: Asia/Shanghai
queries: {general: ['agent skill']}
feeds: []
weights: {heat: 45, utility: 25, freshness: 20, relevance: 10}
limits: {search_per_query: 20, daily_top: 10, weekly_top: 20}
exclusions: {repositories: [], keywords: []}
""", encoding="utf-8")
    return path
