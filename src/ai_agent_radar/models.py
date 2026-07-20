from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class RepoRecord(FrozenModel):
    repository_id: int
    full_name: str
    url: str
    description: str = ""
    topics: tuple[str, ...] = ()
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    watchers: int = 0
    created_at: datetime
    updated_at: datetime
    pushed_at: datetime | None = None
    archived: bool = False
    fork: bool = False
    license_spdx: str | None = None
    language: str | None = None
    latest_release: str | None = None
    latest_release_published_at: datetime | None = None
    release_detail_valid: bool = False
    readme_detail_valid: bool = False
    root_detail_valid: bool = False
    readme: str = ""
    has_skill_md: bool = False
    has_mcp: bool = False
    has_examples: bool = False
    has_tests: bool = False
    has_executable_code: bool = False
    has_runnable_entrypoint: bool = False
    fork_ahead_by: int | None = None
    parent_pushed_at: datetime | None = None
    matched_categories: tuple[str, ...] = ()


class NewsRecord(FrozenModel):
    canonical_url: str
    title: str
    source: str
    tier: Literal["official", "trusted", "custom"]
    published_at: datetime
    summary: str = ""
    related_urls: tuple[str, ...] = ()


class RepoSnapshot(FrozenModel):
    report_date: date
    repository_id: int
    full_name: str
    stars: int
    forks: int
    open_issues: int
    pushed_at: datetime | None
    latest_release: str | None
    total_score: float = 0.0
    categories: tuple[str, ...] = ()
    discovery_complete: bool = True


class TrendMetrics(FrozenModel):
    stars_1d: int = 0
    stars_7d: int = 0
    forks_1d: int = 0
    forks_7d: int = 0
    active_days_7d: int = 0
    first_seen: bool = False
    new_release: bool = False
    rank_change: int | None = None


class ScoreBreakdown(FrozenModel):
    heat: float = Field(ge=0, le=100)
    utility: float = Field(ge=0, le=100)
    freshness: float = Field(ge=0, le=100)
    relevance: float = Field(ge=0, le=100)
    total: float = Field(ge=0, le=100)
    reasons: tuple[str, ...]


class SourceStatus(FrozenModel):
    name: str
    ok: bool
    item_count: int = 0
    error: str | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int = 0


class RunResult(FrozenModel):
    report_path: str
    snapshot_path: str | None
    issue_url: str | None
    candidates: int
    filtered: int
    ranked: int
    source_statuses: tuple[SourceStatus, ...]
    github_discovery_complete: bool = True
    github_queries_succeeded: int = 0
    github_queries_total: int = 0
    sources_succeeded: int = 0
    sources_failed: int = 0
    degraded: bool = False
