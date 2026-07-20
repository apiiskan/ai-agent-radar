from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .config import RadarConfig
from .models import RepoRecord, SourceStatus


@dataclass(frozen=True)
class GitHubCollection:
    repositories: tuple[RepoRecord, ...]
    statuses: tuple[SourceStatus, ...]
    rate_remaining: int | None


class GitHubClient:
    def __init__(
        self,
        token: str,
        client: httpx.Client,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.rate_remaining: int | None = None

    def search(self, query: str, category: str, per_page: int) -> list[RepoRecord]:
        response = self.client.get(
            "https://api.github.com/search/repositories",
            headers=self.headers,
            params={"q": query, "sort": "updated", "order": "desc", "per_page": per_page},
            timeout=20,
        )
        self._capture_rate(response)
        response.raise_for_status()
        return [self._normalize(item, category) for item in response.json().get("items", [])]

    def safe_search(
        self, query: str, category: str, per_page: int
    ) -> tuple[list[RepoRecord], SourceStatus]:
        try:
            repos = self.search(query, category, per_page)
            return repos, SourceStatus(
                name=f"github:{category}:{query}",
                ok=True,
                item_count=len(repos),
                last_success_at=self.now(),
            )
        except httpx.HTTPStatusError as exc:
            limited = (
                exc.response.status_code == 403
                and exc.response.headers.get("x-ratelimit-remaining") == "0"
            )
            message = "GitHub rate limit exhausted" if limited else f"GitHub HTTP {exc.response.status_code}"
            return [], SourceStatus(name=f"github:{category}:{query}", ok=False, error=message)
        except httpx.HTTPError as exc:
            return [], SourceStatus(name=f"github:{category}:{query}", ok=False, error=type(exc).__name__)

    def collect(self, config: RadarConfig) -> GitHubCollection:
        repos: dict[int, RepoRecord] = {}
        statuses: list[SourceStatus] = []
        for category, queries in config.queries.items():
            for query in queries:
                found, status = self.safe_search(query, category, config.limits.search_per_query)
                for repo in found:
                    previous = repos.get(repo.repository_id)
                    categories = set(repo.matched_categories)
                    if previous:
                        categories.update(previous.matched_categories)
                    repos[repo.repository_id] = repo.model_copy(
                        update={"matched_categories": tuple(sorted(categories))}
                    )
                statuses.append(status)
                if self.rate_remaining == 0:
                    return GitHubCollection(tuple(repos.values()), tuple(statuses), self.rate_remaining)
        enriched = [self.enrich(repo) for repo in repos.values()]
        return GitHubCollection(tuple(enriched), tuple(statuses), self.rate_remaining)

    def enrich(self, repo: RepoRecord) -> RepoRecord:
        base_url = f"https://api.github.com/repos/{repo.full_name}"
        readme = self._optional_json(f"{base_url}/readme")
        release = self._optional_json(f"{base_url}/releases/latest")
        root = self._optional_json(f"{base_url}/contents")
        content = ""
        if isinstance(readme, dict) and readme.get("encoding") == "base64":
            try:
                content = base64.b64decode(readme.get("content", "")).decode(
                    "utf-8", errors="replace"
                )
            except (TypeError, ValueError):
                content = ""
        names = {item.get("name", "").casefold() for item in root} if isinstance(root, list) else set()
        return repo.model_copy(
            update={
                "readme": content[:20_000],
                "latest_release": release.get("tag_name") if isinstance(release, dict) else None,
                "has_skill_md": "skill.md" in names,
                "has_mcp": any("mcp" in name for name in names),
                "has_examples": any(name in {"example", "examples", "demo", "demos"} for name in names),
                "has_tests": any(name in {"test", "tests"} for name in names),
            }
        )

    def _optional_json(self, url: str) -> dict | list | None:
        if self.rate_remaining is not None and self.rate_remaining <= 10:
            return None
        try:
            response = self.client.get(url, headers=self.headers, timeout=20)
            self._capture_rate(response)
            if response.status_code in {403, 404}:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None

    def _capture_rate(self, response: httpx.Response) -> None:
        value = response.headers.get("x-ratelimit-remaining")
        self.rate_remaining = int(value) if value and value.isdigit() else self.rate_remaining

    @staticmethod
    def _normalize(item: dict, category: str) -> RepoRecord:
        license_data = item.get("license") or {}
        return RepoRecord(
            repository_id=item["id"],
            full_name=item["full_name"],
            url=item["html_url"],
            description=item.get("description") or "",
            topics=tuple(item.get("topics") or ()),
            stars=item.get("stargazers_count", 0),
            forks=item.get("forks_count", 0),
            open_issues=item.get("open_issues_count", 0),
            watchers=item.get("watchers_count", 0),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            pushed_at=item.get("pushed_at"),
            archived=item.get("archived", False),
            fork=item.get("fork", False),
            license_spdx=license_data.get("spdx_id"),
            language=item.get("language"),
            matched_categories=(category,),
        )
