from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup

from .config import FeedConfig
from .models import NewsRecord, SourceStatus
from .normalize import canonicalize_url, normalize_text


@dataclass(frozen=True)
class NewsCollection:
    items: tuple[NewsRecord, ...]
    statuses: tuple[SourceStatus, ...]


def collect_news(feeds: list[FeedConfig], fetch: Callable[[str], bytes]) -> NewsCollection:
    """Collect configured feeds while retaining a status for every source."""
    by_url: dict[str, NewsRecord] = {}
    statuses: list[SourceStatus] = []
    for source in feeds:
        try:
            payload = fetch(source.url)
            if source.kind == "rss":
                records = _parse_rss(source, payload)
            elif source.kind == "html":
                records = _parse_html(source, payload)
            else:
                records = _parse_github_releases(source, payload)
            for record in records:
                existing = by_url.get(record.canonical_url)
                if existing is None or (record.tier == "official" and existing.tier != "official"):
                    by_url[record.canonical_url] = record
            statuses.append(
                SourceStatus(
                    name=f"feed:{source.name}",
                    ok=True,
                    item_count=len(records),
                    last_success_at=datetime.now(timezone.utc),
                )
            )
        except Exception as exc:
            statuses.append(SourceStatus(name=f"feed:{source.name}", ok=False, error=type(exc).__name__))
    return NewsCollection(tuple(_merge_similar_events(list(by_url.values()))), tuple(statuses))


def _merge_similar_events(records: list[NewsRecord]) -> list[NewsRecord]:
    merged: list[NewsRecord] = []
    for record in sorted(records, key=lambda item: (item.tier != "official", item.published_at)):
        match_index = next(
            (
                index
                for index, item in enumerate(merged)
                if abs(record.published_at - item.published_at) <= timedelta(days=2)
                and SequenceMatcher(
                    None, normalize_text(record.title), normalize_text(item.title)
                ).ratio()
                >= 0.9
            ),
            None,
        )
        if match_index is None:
            merged.append(record)
            continue

        primary = merged[match_index]
        related = tuple(sorted(set(primary.related_urls + (record.canonical_url,))))
        merged[match_index] = primary.model_copy(update={"related_urls": related})
    return merged


def _parse_rss(source: FeedConfig, payload: bytes) -> list[NewsRecord]:
    records: list[NewsRecord] = []
    for entry in feedparser.parse(payload).entries:
        link = entry.get("link")
        published_parts = entry.get("published_parsed") or entry.get("updated_parsed")
        if not isinstance(link, str) or not published_parts or not _is_absolute_http_url(link):
            continue
        published = datetime(*published_parts[:6], tzinfo=timezone.utc)
        title = entry.get("title", "Untitled")
        summary = entry.get("summary", "")
        records.append(
            NewsRecord(
                canonical_url=canonicalize_url(link),
                title=title if isinstance(title, str) else "Untitled",
                source=source.name,
                tier=source.tier,
                published_at=published,
                summary=summary if isinstance(summary, str) else "",
            )
        )
    return records


def _parse_github_releases(source: FeedConfig, payload: bytes) -> list[NewsRecord]:
    releases = json.loads(payload)
    if not isinstance(releases, list):
        raise ValueError("GitHub releases response must be a JSON array")

    records: list[NewsRecord] = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        url = release.get("html_url")
        published_at = release.get("published_at")
        if (
            not isinstance(url, str)
            or not _is_absolute_http_url(url)
            or not isinstance(published_at, str)
        ):
            continue
        try:
            published = _parse_datetime(published_at)
        except ValueError:
            continue
        name = release.get("name")
        tag = release.get("tag_name")
        title = "Untitled"
        for candidate in (name, tag):
            if isinstance(candidate, str) and candidate.strip():
                title = candidate.strip()
                break
        body = release.get("body")
        records.append(
            NewsRecord(
                canonical_url=canonicalize_url(url),
                title=title,
                source=source.name,
                tier=source.tier,
                published_at=published,
                summary=body[:1000] if isinstance(body, str) else "",
            )
        )
    return records


def _is_absolute_http_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)


def _parse_html(source: FeedConfig, payload: bytes) -> list[NewsRecord]:
    soup = BeautifulSoup(payload, "html.parser")
    source_parts = urlsplit(source.url)
    records: list[NewsRecord] = []
    for article in soup.select("article"):
        link = article.select_one("a[href]")
        time = article.select_one("time[datetime]")
        if link is None or time is None:
            continue
        try:
            published = _parse_datetime(time["datetime"])
            target = urljoin(source.url, link["href"])
        except (KeyError, TypeError, ValueError):
            continue
        target_parts = urlsplit(target)
        if (
            target_parts.scheme not in {"http", "https"}
            or target_parts.netloc.lower() != source_parts.netloc.lower()
        ):
            continue
        records.append(
            NewsRecord(
                canonical_url=canonicalize_url(target),
                title=link.get_text(" ", strip=True) or "Untitled",
                source=source.name,
                tier=source.tier,
                published_at=published,
                summary=article.get_text(" ", strip=True)[:1000],
            )
        )
    return records


def _parse_datetime(value: str) -> datetime:
    published = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return published.replace(tzinfo=timezone.utc) if published.tzinfo is None else published
