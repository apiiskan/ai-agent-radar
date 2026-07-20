"""Deterministic Markdown reports for the AI Agent Radar."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from .models import NewsRecord, RepoRecord, ScoreBreakdown, SourceStatus
from .summarize import ProjectSummary

RankedItem = tuple[RepoRecord, ScoreBreakdown, ProjectSummary]

_MARKDOWN_ESCAPES = "\\`*_{}[]<>()#+.!|"


@dataclass(frozen=True)
class ReportBundle:
    ranked: tuple[RankedItem, ...]
    new: tuple[RankedItem, ...]
    rising: tuple[RankedItem, ...]
    useful: tuple[RankedItem, ...]
    dropped: tuple[str, ...]
    categories: Mapping[str, tuple[RankedItem, ...]]
    news: tuple[NewsRecord, ...]
    statuses: tuple[SourceStatus, ...]


def render_daily(day: date, bundle: ReportBundle, top_limit: int = 10) -> str:
    """Render the daily report with a deterministic ordering and item limit."""
    _validate_limit(top_limit)
    lines = [
        f"# AI Agent Radar 日报 · {day.isoformat()}",
        "",
        "## 今日摘要",
        "",
        f"发现并排名 {len(bundle.ranked)} 个项目，收录 {len(bundle.news)} 条资讯。",
        "",
    ]
    for title, items in (
        ("今日新发现", bundle.new),
        ("增长最快", bundle.rising),
        ("最实用", bundle.useful),
    ):
        lines.extend(_item_section(title, items, top_limit))

    lines.extend(["## 分类榜", ""])
    if not bundle.categories:
        lines.extend(["- 暂无分类项目。", ""])
    else:
        for category, items in sorted(bundle.categories.items()):
            lines.extend([f"### {_escape_text(category)}", "", *_repo_lines(items[:top_limit]), ""])

    lines.extend(["## 官方更新与资讯", "", *_news_lines(bundle.news), ""])
    lines.extend(["## 来源状态", "", *_status_lines(bundle.statuses), ""])
    return "\n".join(lines)


def render_weekly(day: date, bundle: ReportBundle, top_limit: int = 20) -> str:
    """Render the weekly ranking report with a deterministic ordering and item limit."""
    _validate_limit(top_limit)
    lines = [f"# AI Agent Radar 周榜 · 截至 {day.isoformat()}", ""]
    for title, items in (
        (f"综合热度 Top {top_limit}", bundle.ranked),
        ("新上榜", bundle.new),
    ):
        lines.extend(_item_section(title, items, top_limit))

    lines.extend(["## 掉榜", "", *_dropped_lines(bundle.dropped), ""])
    for title, items in (
        ("本周黑马", bundle.new),
        ("连续升温", bundle.rising),
        ("值得立即试用", bundle.useful),
    ):
        lines.extend(_item_section(title, items, top_limit))

    official_news = tuple(item for item in bundle.news if item.tier == "official")
    lines.extend(["## 本周重要官方更新", "", *_news_lines(official_news), ""])
    lines.extend(
        [
            "## 下周关注",
            "",
            "- 持续追踪本周增长项目的 Release、提交活跃度和社区采用情况。",
            "",
            "## 数据完整性",
            "",
            *_status_lines(bundle.statuses),
            "",
        ]
    )
    return "\n".join(lines)


def write_report_atomic(path: Path, markdown: str) -> None:
    """Atomically replace *path* with Markdown content, creating its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(markdown)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _item_section(title: str, items: tuple[RankedItem, ...], limit: int) -> list[str]:
    return [f"## {title}", "", *_repo_lines(items[:limit]), ""]


def _repo_lines(items: tuple[RankedItem, ...]) -> list[str]:
    if not items:
        return ["- 暂无符合质量门槛的条目。"]
    return [
        f"{index}. {_link_or_text(repo.full_name, repo.url)} — {_escape_text(summary.one_line)}  \n"
        f"   综合分 `{score.total:g}`；{_reasons_text(score.reasons)}"
        for index, (repo, score, summary) in enumerate(items, 1)
    ]


def _news_lines(items: tuple[NewsRecord, ...]) -> list[str]:
    if not items:
        return ["- 今日无新资讯。"]
    return [
        f"- {_link_or_text(item.title, item.canonical_url)} — "
        f"{_escape_text(item.source)}（{_escape_text(item.tier)}）"
        for item in sorted(items, key=lambda item: (item.published_at, item.canonical_url, item.title))
    ]


def _status_lines(statuses: tuple[SourceStatus, ...]) -> list[str]:
    if not statuses:
        return ["- 暂无来源状态。"]
    return [
        f"- {'✅' if status.ok else '⚠️'} {_escape_text(status.name)}: "
        f"{status.item_count if status.ok else _escape_text(status.error or '未知错误')}"
        for status in sorted(statuses, key=lambda status: status.name)
    ]


def _dropped_lines(dropped: tuple[str, ...]) -> list[str]:
    if not dropped:
        return ["- 本周无掉榜项目。"]
    return [f"- {_escape_text(name)}" for name in sorted(dropped)]


def _reasons_text(reasons: tuple[str, ...]) -> str:
    return "；".join(_escape_text(reason) for reason in reasons) or "评分依据不足。"


def _link_or_text(label: str, url: str) -> str:
    escaped_label = _escape_text(label)
    if _is_safe_url(url):
        return f"[{escaped_label}]({_markdown_destination(url)})"
    return escaped_label


def _is_safe_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and not any(character.isspace() or character in "\\[]<>" for character in url)
    )


def _markdown_destination(url: str) -> str:
    return url.replace("(", r"\(").replace(")", r"\)")


def _escape_text(value: str) -> str:
    single_line = " ".join(value.splitlines())
    return "".join(f"\\{character}" if character in _MARKDOWN_ESCAPES else character for character in single_line)


def _validate_limit(limit: int) -> None:
    if limit < 1:
        raise ValueError("top_limit must be at least 1")
