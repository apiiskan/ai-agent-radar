from datetime import datetime, timezone
from math import log1p

from .config import WeightConfig
from .models import RepoRecord, ScoreBreakdown, TrendMetrics


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def score_repository(
    repo: RepoRecord,
    trend: TrendMetrics,
    weights: WeightConfig,
    now: datetime,
) -> ScoreBreakdown:
    now = now.astimezone(timezone.utc)
    heat = _bounded(
        log1p(trend.stars_1d) * 16
        + log1p(trend.stars_7d) * 8
        + log1p(trend.forks_7d) * 6
        + (12 if trend.new_release else 0)
    )
    utility_flags = [
        bool(repo.readme),
        bool(repo.license_spdx),
        repo.has_skill_md or repo.has_mcp,
        repo.has_examples,
        repo.has_tests,
    ]
    utility = _bounded(sum(utility_flags) * 20)
    age_days = max(0, (now - repo.created_at.astimezone(timezone.utc)).days)
    push_days = (now - (repo.pushed_at or repo.updated_at).astimezone(timezone.utc)).days
    freshness = _bounded(
        (55 if trend.first_seen else 20)
        + max(0, 30 - push_days * 2)
        + (15 if trend.new_release else 0)
        + max(0, 10 - age_days / 30)
    )
    evidence = len(repo.matched_categories) * 20 + min(30, len(repo.topics) * 5)
    text = (repo.description + " " + repo.readme).casefold()
    evidence += 25 if any(
        term in text for term in ("agent", "skill", "mcp", "codex", "claude", "grok", "kimi")
    ) else 0
    relevance = _bounded(evidence)
    total = _bounded(
        (heat * weights.heat + utility * weights.utility + freshness * weights.freshness + relevance * weights.relevance)
        / 100
    )
    reasons: list[str] = []
    if trend.stars_7d:
        reasons.append(f"7 日新增 {trend.stars_7d} stars")
    if repo.has_skill_md:
        reasons.append("包含 SKILL.md")
    if repo.has_mcp:
        reasons.append("包含 MCP 入口")
    if repo.has_examples:
        reasons.append("提供使用示例")
    if trend.new_release:
        reasons.append("今日发现新版本")
    if trend.first_seen:
        reasons.append("今日首次发现")
    return ScoreBreakdown(
        heat=heat,
        utility=utility,
        freshness=freshness,
        relevance=relevance,
        total=total,
        reasons=tuple(reasons or ["主题相关且通过质量门槛"]),
    )


def rank_repositories(scored: list[tuple[RepoRecord, ScoreBreakdown]]) -> list[tuple[RepoRecord, ScoreBreakdown]]:
    return sorted(
        scored,
        key=lambda item: (
            -item[1].total,
            -item[0].stars,
            item[0].full_name.casefold(),
            item[0].repository_id,
        ),
    )
