from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, model_validator


class ConfigurationError(ValueError):
    """A normalized failure to read or validate radar configuration."""


class FeedConfig(BaseModel):
    name: str
    url: str
    tier: Literal["official", "trusted", "custom"]
    kind: Literal["rss", "html", "github_releases"] = "rss"


class WeightConfig(BaseModel):
    heat: int = Field(ge=0)
    utility: int = Field(ge=0)
    freshness: int = Field(ge=0)
    relevance: int = Field(ge=0)


class LimitConfig(BaseModel):
    search_per_query: int = Field(ge=1, le=100)
    daily_top: int = Field(ge=1, le=50)
    weekly_top: int = Field(ge=1, le=100)


class ExclusionConfig(BaseModel):
    repositories: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class QualityPolicyConfig(BaseModel):
    recent_release_days: int = Field(default=90, ge=1, le=3650)
    fork_min_ahead_commits: int = Field(default=3, ge=1, le=10_000)
    official_organizations: list[str] = Field(default_factory=list)
    trusted_topics: list[str] = Field(default_factory=list)
    allow_official_relevance_exception: bool = True
    keyword_stuffing_ratio: float = Field(default=0.45, gt=0, le=1)


class RadarConfig(BaseModel):
    timezone: str
    queries: dict[str, list[str]]
    feeds: list[FeedConfig]
    weights: WeightConfig
    limits: LimitConfig
    exclusions: ExclusionConfig
    quality: QualityPolicyConfig = Field(default_factory=QualityPolicyConfig)

    @model_validator(mode="after")
    def validate_invariants(self) -> "RadarConfig":
        ZoneInfo(self.timezone)
        if sum(self.weights.model_dump().values()) != 100:
            raise ValueError("weights must total 100")
        if not any(self.queries.values()):
            raise ValueError("at least one query is required")
        return self


def load_config(path: Path) -> RadarConfig:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(f"unable to read radar configuration: {path}") from exc
    try:
        data = yaml.safe_load(content)
        return RadarConfig.model_validate(data)
    except (ValueError, yaml.YAMLError, ZoneInfoNotFoundError) as exc:
        raise ConfigurationError(f"invalid radar configuration: {exc}") from exc
