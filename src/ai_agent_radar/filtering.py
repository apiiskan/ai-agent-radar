from dataclasses import dataclass

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
    repositories: list[RepoRecord], config: RadarConfig
) -> tuple[list[RepoRecord], list[Rejection]]:
    accepted: list[RepoRecord] = []
    rejected: list[Rejection] = []
    excluded_names = {name.casefold() for name in config.exclusions.repositories}

    for repository in repositories:
        reason = _rejection_reason(repository, excluded_names, config.exclusions.keywords)
        if reason is None:
            accepted.append(repository)
        else:
            rejected.append(Rejection(repository, reason))

    return accepted, rejected


def _rejection_reason(
    repository: RepoRecord, excluded_names: set[str], excluded_keywords: list[str]
) -> str | None:
    if repository.full_name.casefold() in excluded_names:
        return "explicit exclusion"
    if repository.archived:
        return "archived"
    if repository.fork:
        return "fork"
    if (
        not repository.readme
        and not repository.license_spdx
        and not repository.has_skill_md
        and not repository.has_mcp
    ):
        return "empty shell"

    searchable_text = f"{repository.description} {repository.readme}".casefold()
    if any(keyword.casefold() in searchable_text for keyword in excluded_keywords):
        return "excluded keyword"
    return None
