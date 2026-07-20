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
    complete: bool = True
    queries_total: int = 0
    queries_succeeded: int = 0


@dataclass(frozen=True)
class _OptionalJson:
    payload: dict | list | None
    valid: bool


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
        self.limited = False

    def search(self, query: str, category: str, per_page: int) -> list[RepoRecord]:
        response = self.client.get(
            "https://api.github.com/search/repositories",
            headers=self.headers,
            params={"q": query, "sort": "updated", "order": "desc", "per_page": per_page},
            timeout=20,
        )
        self._capture_rate(response)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("GitHub search response must be a JSON object")
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise ValueError("GitHub search response items must be a list")
        return [self._normalize(item, category) for item in items]

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
            message = (
                "GitHub rate limit exhausted"
                if self.limited
                else f"GitHub HTTP {exc.response.status_code}"
            )
            return [], SourceStatus(name=f"github:{category}:{query}", ok=False, error=message)
        except httpx.HTTPError as exc:
            return [], SourceStatus(name=f"github:{category}:{query}", ok=False, error=type(exc).__name__)
        except (KeyError, TypeError, ValueError) as exc:
            return [], SourceStatus(name=f"github:{category}:{query}", ok=False, error=type(exc).__name__)

    def collect(self, config: RadarConfig) -> GitHubCollection:
        repos: dict[int, RepoRecord] = {}
        statuses: list[SourceStatus] = []
        queries_total = sum(len(queries) for queries in config.queries.values())
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
                if self.limited:
                    return GitHubCollection(
                        tuple(repos.values()),
                        tuple(statuses),
                        self.rate_remaining,
                        complete=False,
                        queries_total=queries_total,
                        queries_succeeded=sum(status.ok for status in statuses),
                    )
        enriched = [self.enrich(repo) for repo in repos.values()]
        queries_succeeded = sum(status.ok for status in statuses)
        optional_detail_failures = sum(
            not (
                repo.readme_detail_valid
                and repo.release_detail_valid
                and repo.root_detail_valid
            )
            for repo in enriched
        )
        optional_details_complete = optional_detail_failures == 0
        if optional_detail_failures:
            statuses.append(
                SourceStatus(
                    name="github:optional-details",
                    ok=False,
                    item_count=len(enriched) - optional_detail_failures,
                    error=(
                        f"{optional_detail_failures} repositories have incomplete optional details"
                    ),
                )
            )
        return GitHubCollection(
            tuple(enriched),
            tuple(statuses),
            self.rate_remaining,
            complete=(
                queries_succeeded == queries_total and optional_details_complete
            ),
            queries_total=queries_total,
            queries_succeeded=queries_succeeded,
        )

    def enrich(self, repo: RepoRecord) -> RepoRecord:
        base_url = f"https://api.github.com/repos/{repo.full_name}"
        readme_result = self._optional_json(f"{base_url}/readme")
        release_result = self._optional_json(f"{base_url}/releases/latest")
        root_result = self._optional_json(f"{base_url}/contents")
        readme = readme_result.payload
        release = release_result.payload
        root = root_result.payload
        content = ""
        if isinstance(readme, dict) and readme.get("encoding") == "base64":
            try:
                content = base64.b64decode(readme.get("content", "")).decode(
                    "utf-8", errors="replace"
                )
            except (TypeError, ValueError):
                content = ""
        names: set[str] = set()
        root_detail_valid = root_result.valid and (
            root is None
            or (
                isinstance(root, list)
                and all(
                    isinstance(item, dict) and isinstance(item.get("name"), str)
                    for item in root
                )
            )
        )
        if isinstance(root, list):
            for item in root:
                if isinstance(item, dict):
                    name = item.get("name")
                    if isinstance(name, str):
                        names.add(name.casefold())
        latest_release: str | None = None
        latest_release_published_at: datetime | None = None
        release_detail_valid = release_result.valid and release is None
        if isinstance(release, dict):
            tag = release.get("tag_name")
            published_at = release.get("published_at")
            if isinstance(tag, str) and tag.strip():
                latest_release = tag.strip()
                release_detail_valid = True
                if isinstance(published_at, str):
                    try:
                        latest_release_published_at = _parse_datetime(published_at)
                    except ValueError:
                        release_detail_valid = False

        has_executable_code = any(_is_executable_root_entry(name) for name in names)
        has_runnable_entrypoint = any(_is_runnable_entrypoint(name) for name in names)
        fork_ahead_by: int | None = None
        parent_pushed_at: datetime | None = None
        if repo.fork:
            fork_ahead_by, parent_pushed_at = self._fork_evidence(repo, base_url)
        return repo.model_copy(
            update={
                "readme": content[:20_000],
                "latest_release": latest_release,
                "latest_release_published_at": latest_release_published_at,
                "release_detail_valid": release_detail_valid,
                "readme_detail_valid": readme_result.valid,
                "root_detail_valid": root_detail_valid,
                "has_skill_md": "skill.md" in names,
                "has_mcp": any("mcp" in name for name in names),
                "has_examples": any(name in {"example", "examples", "demo", "demos"} for name in names),
                "has_tests": any(name in {"test", "tests"} for name in names),
                "has_executable_code": has_executable_code,
                "has_runnable_entrypoint": has_runnable_entrypoint,
                "fork_ahead_by": fork_ahead_by,
                "parent_pushed_at": parent_pushed_at,
            }
        )

    def _optional_json(self, url: str) -> _OptionalJson:
        if self.limited or (self.rate_remaining is not None and self.rate_remaining <= 10):
            return _OptionalJson(None, False)
        try:
            response = self.client.get(url, headers=self.headers, timeout=20)
            self._capture_rate(response)
            if response.status_code == 404:
                return _OptionalJson(None, True)
            if response.status_code == 403:
                return _OptionalJson(None, False)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, (dict, list)):
                return _OptionalJson(None, False)
            return _OptionalJson(payload, True)
        except (httpx.HTTPError, ValueError):
            return _OptionalJson(None, False)

    def _fork_evidence(
        self, repo: RepoRecord, base_url: str
    ) -> tuple[int | None, datetime | None]:
        detail_result = self._optional_json(base_url)
        detail = detail_result.payload
        if not detail_result.valid or not isinstance(detail, dict):
            return None, None
        parent = detail.get("parent")
        branch = detail.get("default_branch")
        if not isinstance(parent, dict) or not isinstance(branch, str):
            return None, None
        parent_branch = parent.get("default_branch")
        parent_pushed = parent.get("pushed_at")
        if not isinstance(parent_branch, str) or not isinstance(parent_pushed, str):
            return None, None
        try:
            parent_pushed_at = _parse_datetime(parent_pushed)
        except ValueError:
            return None, None
        owner = repo.full_name.partition("/")[0]
        comparison_result = self._optional_json(
            f"{base_url}/compare/{parent_branch}...{owner}:{branch}"
        )
        comparison = comparison_result.payload
        ahead_by = comparison.get("ahead_by") if isinstance(comparison, dict) else None
        return (
            ahead_by if comparison_result.valid and isinstance(ahead_by, int) else None,
            parent_pushed_at,
        )

    def _capture_rate(self, response: httpx.Response) -> None:
        value = response.headers.get("x-ratelimit-remaining")
        self.rate_remaining = int(value) if value and value.isdigit() else self.rate_remaining
        self.limited = self.limited or response.status_code == 429 or (
            response.status_code == 403 and bool(response.headers.get("retry-after"))
        ) or self.rate_remaining == 0

    @staticmethod
    def _normalize(item: object, category: str) -> RepoRecord:
        if not isinstance(item, dict):
            raise ValueError("GitHub search item must be a JSON object")
        license_data = item.get("license")
        if not isinstance(license_data, dict):
            license_data = {}
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


_CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".sh",
}
_CODE_DIRECTORIES = {"src", "app", "cmd", "lib", "packages"}
_RUNNABLE_ENTRIES = {
    "pyproject.toml",
    "setup.py",
    "package.json",
    "cargo.toml",
    "go.mod",
    "dockerfile",
    "compose.yml",
    "docker-compose.yml",
    "main.py",
    "cli.py",
}


def _is_executable_root_entry(name: str) -> bool:
    return name in _CODE_DIRECTORIES or any(name.endswith(suffix) for suffix in _CODE_EXTENSIONS)


def _is_runnable_entrypoint(name: str) -> bool:
    return name in _RUNNABLE_ENTRIES or name.startswith("install.")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
