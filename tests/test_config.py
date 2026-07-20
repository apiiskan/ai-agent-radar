from pathlib import Path

import pytest

from ai_agent_radar.config import load_config


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
