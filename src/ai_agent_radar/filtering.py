from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from .config import RadarConfig
from .models import RepoRecord


@dataclass(frozen=True)
class Rejection:
    repository: RepoRecord
    reason: str


def dedupe_repositories(repositories: list[RepoRecord]) -> list[RepoRecord]:
    merged: dict[int, RepoRecord] = {}
    for repository in repositories:
        previous = merged.get(repository.repository_id)
        if previous is None:
            merged[repository.repository_id] = repository
            continue
        categories = tuple(sorted(set(previous.matched_categories) | set(repository.matched_categories)))
        merged[repository.repository_id] = previous.model_copy(
            update={"matched_categories": categories}
        )
    return list(merged.values())


def quality_gate(
    repositories: list[RepoRecord],
    config: RadarConfig,
    *,
    now: datetime | None = None,
) -> tuple[list[RepoRecord], list[Rejection]]:
    accepted: list[RepoRecord] = []
    rejected: list[Rejection] = []
    excluded_names = {name.casefold() for name in config.exclusions.repositories}
    evaluated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    for repository in repositories:
        reason = _rejection_reason(
            repository,
            excluded_names,
            config.exclusions.keywords,
            config,
            evaluated_at,
        )
        if reason is None:
            accepted.append(repository)
        else:
            rejected.append(Rejection(repository, reason))

    return accepted, rejected


def _rejection_reason(
    repository: RepoRecord,
    excluded_names: set[str],
    excluded_keywords: list[str],
    config: RadarConfig,
    now: datetime,
) -> str | None:
    if repository.full_name.casefold() in excluded_names:
        return "explicit exclusion"
    if repository.archived and not _has_recent_release(repository, config, now):
        return "archived without recent verified release"
    if repository.fork and not _has_independent_fork_evidence(repository, config):
        return "fork without independent development evidence"
    if not any(
        (
            repository.readme.strip(),
            repository.has_skill_md,
            repository.has_mcp,
            repository.has_executable_code,
            repository.has_runnable_entrypoint,
        )
    ):
        return "empty shell"

    searchable_text = (
        f"{repository.description} {repository.readme} {' '.join(repository.topics)}"
    ).casefold()
    if any(keyword.casefold() in searchable_text for keyword in excluded_keywords):
        return "excluded keyword"
    official_exception = _has_relevance_exception(repository, config)
    if not official_exception and _looks_keyword_stuffed(repository, config):
        return "keyword stuffing"
    if not official_exception and not _has_topic_evidence(repository):
        return "unrelated content"
    return None


def _has_recent_release(repository: RepoRecord, config: RadarConfig, now: datetime) -> bool:
    published_at = repository.latest_release_published_at
    return bool(
        repository.release_detail_valid
        and repository.latest_release
        and published_at is not None
        and published_at.astimezone(timezone.utc)
        >= now - timedelta(days=config.quality.recent_release_days)
    )


def _has_independent_fork_evidence(repository: RepoRecord, config: RadarConfig) -> bool:
    return bool(
        repository.fork_ahead_by is not None
        and repository.fork_ahead_by >= config.quality.fork_min_ahead_commits
        and repository.parent_pushed_at is not None
        and repository.pushed_at is not None
        and repository.pushed_at.astimezone(timezone.utc)
        > repository.parent_pushed_at.astimezone(timezone.utc)
    )


def _has_relevance_exception(repository: RepoRecord, config: RadarConfig) -> bool:
    if not config.quality.allow_official_relevance_exception:
        return False
    owner = repository.full_name.partition("/")[0].casefold()
    official = {value.casefold() for value in config.quality.official_organizations}
    trusted_topics = {value.casefold() for value in config.quality.trusted_topics}
    return owner in official or bool(
        {topic.casefold() for topic in repository.topics} & trusted_topics
    )


_RELEVANCE_TERMS = {
    "agent",
    "agents",
    "agentic",
    "skill",
    "skills",
    "mcp",
    "codex",
    "claude",
    "grok",
    "kimi",
    "multi-agent",
    "computer-use",
}


def _tokens(repository: RepoRecord) -> list[str]:
    text = f"{repository.description} {repository.readme} {' '.join(repository.topics)}"
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", text.casefold())


def _looks_keyword_stuffed(repository: RepoRecord, config: RadarConfig) -> bool:
    tokens = _tokens(repository)
    if not tokens:
        return False
    relevant = [token for token in tokens if token in _RELEVANCE_TERMS]
    non_relevant = {token for token in tokens if token not in _RELEVANCE_TERMS}
    return (
        len(relevant) >= 6
        and len(relevant) / len(tokens) >= config.quality.keyword_stuffing_ratio
        and len(non_relevant) < 4
    )


def _has_topic_evidence(repository: RepoRecord) -> bool:
    return bool(set(_tokens(repository)) & _RELEVANCE_TERMS)
