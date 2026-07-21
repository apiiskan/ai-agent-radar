from pathlib import Path

import pytest

from ai_agent_radar.config import ConfigurationError, load_config


def test_load_config_resolves_weights_and_queries(tmp_path: Path) -> None:
    path = tmp_path / "radar.yaml"
    path.write_text(
        """
timezone: Asia/Shanghai
queries:
  codex: ['codex agent skill']
feeds:
  - name: OpenAI
    url: https://openai.com/news/rss.xml
    tier: official
weights: {heat: 45, utility: 25, freshness: 20, relevance: 10}
limits: {search_per_query: 20, daily_top: 10, weekly_top: 20}
exclusions: {repositories: [], keywords: []}
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.timezone == "Asia/Shanghai"
    assert config.queries["codex"] == ["codex agent skill"]
    assert config.weights.heat == 45


def test_load_config_rejects_weights_not_equal_to_100(tmp_path: Path) -> None:
    path = tmp_path / "radar.yaml"
    path.write_text(
        """
timezone: Asia/Shanghai
queries: {codex: ['codex']}
feeds: []
weights: {heat: 40, utility: 25, freshness: 20, relevance: 10}
limits: {search_per_query: 20, daily_top: 10, weekly_top: 20}
exclusions: {repositories: [], keywords: []}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="weights must total 100"):
        load_config(path)


@pytest.mark.parametrize(
    "content",
    [
        "timezone: [",
        """timezone: Mars/Olympus
queries: {general: [agent]}
feeds: []
weights: {heat: 45, utility: 25, freshness: 20, relevance: 10}
limits: {search_per_query: 20, daily_top: 10, weekly_top: 20}
exclusions: {repositories: [], keywords: []}
""",
    ],
)
def test_load_config_normalizes_yaml_and_timezone_failures(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "radar.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invalid radar configuration"):
        load_config(path)


def test_load_config_normalizes_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="unable to read radar configuration"):
        load_config(tmp_path / "missing.yaml")


def test_repository_config_uses_structured_official_release_sources() -> None:
    config = load_config(Path("config/radar.yaml"))
    releases = {feed.name: feed for feed in config.feeds if feed.kind == "github_releases"}

    assert releases["Anthropic Releases"].url.startswith(
        "https://api.github.com/repos/anthropics/claude-code/releases"
    )
    assert releases["xAI Releases"].url.startswith(
        "https://api.github.com/repos/xai-org/xai-sdk-python/releases"
    )
    assert releases["Kimi Releases"].url.startswith(
        "https://api.github.com/repos/MoonshotAI/kimi-cli/releases"
    )


def test_repository_config_exposes_quality_evidence_policy() -> None:
    config = load_config(Path("config/radar.yaml"))

    assert config.quality.recent_release_days == 90
    assert config.quality.fork_min_ahead_commits == 3
    assert "openai" in config.quality.official_organizations
    assert "mcp" in config.quality.trusted_topics
    assert config.quality.allow_official_relevance_exception is True
