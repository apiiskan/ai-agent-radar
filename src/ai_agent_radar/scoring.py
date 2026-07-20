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
    if sum(weights.model_dump().values()) != 100:
        raise ValueError("weights must total 100")

    now = now.astimezone(timezone.utc)
    stars_1d = max(0, trend.stars_1d)
    stars_7d = max(0, trend.stars_7d)
    forks_7d = max(0, trend.forks_7d)
    heat = _bounded(
        log1p(stars_1d) * 16
        + log1p(stars_7d) * 8
        + log1p(forks_7d) * 6
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
    total = round(
        (heat * weights.heat + utility * weights.utility + freshness * weights.freshness + relevance * weights.relevance)
        / 100,
        2,
    )
    reasons: list[str] = []
    if stars_1d:
        reasons.append(f"热度：近 1 日新增 {stars_1d} stars")
    if stars_7d:
        reasons.append(f"热度：7 日新增 {stars_7d} stars")
    if forks_7d:
        reasons.append(f"热度：7 日新增 {forks_7d} forks")
    if trend.new_release:
        reasons.append("热度：发现新版本")
    if not (stars_1d or stars_7d or forks_7d or trend.new_release):
        reasons.append("热度：未检测到近 1 日或 7 日增长")
    if repo.readme:
        reasons.append("实用性：包含 README")
    if repo.license_spdx:
        reasons.append(f"实用性：许可证为 {repo.license_spdx}")
    if repo.has_skill_md:
        reasons.append("实用性：包含 SKILL.md")
    if repo.has_mcp:
        reasons.append("实用性：包含 MCP 入口")
    if repo.has_examples:
        reasons.append("实用性：提供使用示例")
    if repo.has_tests:
        reasons.append("实用性：包含测试")
    if not any(utility_flags):
        reasons.append("实用性：未检测到 README、许可证、技能/MCP、示例或测试")
    reasons.append(f"新鲜度：基础分；最近推送距今 {push_days} 天")
    if trend.new_release:
        reasons.append("新鲜度：今日发现新版本")
    if trend.first_seen:
        reasons.append("新鲜度：今日首次发现")
    if age_days < 300:
        reasons.append(f"新鲜度：创建距今 {age_days} 天")
    if repo.matched_categories:
        reasons.append(f"相关性：匹配分类 {', '.join(repo.matched_categories)}")
    if repo.topics:
        reasons.append(f"相关性：主题 {', '.join(repo.topics)}")
    if any(term in text for term in ("agent", "skill", "mcp", "codex", "claude", "grok", "kimi")):
        reasons.append("相关性：描述或 README 包含智能体关键词")
    if relevance == 0:
        reasons.append("相关性：未找到分类、主题或关键词证据")
    return ScoreBreakdown(
        heat=heat,
        utility=utility,
        freshness=freshness,
        relevance=relevance,
        total=total,
        reasons=tuple(reasons),
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
