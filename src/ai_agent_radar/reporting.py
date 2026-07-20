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
from .trends import WeeklyChartAnalysis

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
    weekly_analysis: WeeklyChartAnalysis | None = None
    discovery_complete: bool = True


def render_daily(
    day: date,
    bundle: ReportBundle,
    top_limit: int = 10,
    ranked_count: int | None = None,
) -> str:
    """Render the daily report with a deterministic ordering and item limit."""
    _validate_limit(top_limit)
    lines = [
        f"# AI Agent Radar 日报 · {day.isoformat()}",
        "",
        "## 今日摘要",
        "",
        (
            f"共排名 {ranked_count} 个项目，展示前 {len(bundle.ranked)} 个，"
            f"收录 {len(bundle.news)} 条资讯。"
            if ranked_count is not None
            else f"发现并排名 {len(bundle.ranked)} 个项目，收录 {len(bundle.news)} 条资讯。"
        ),
        "",
    ]
    if not bundle.discovery_complete:
        lines.extend(
            [
                "- ⚠️ GitHub 发现不完整；本期为降级结果，不应据此判断项目消失或掉榜。",
                "",
            ]
        )
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
    analysis = bundle.weekly_analysis or WeeklyChartAnalysis.insufficient(
        "未提供可比较的历史榜单"
    )
    lines = [f"# AI Agent Radar 周榜 · 截至 {day.isoformat()}", ""]
    lines.extend(
        [
            f"## 综合热度 Top {top_limit}",
            "",
            *_repo_lines(bundle.ranked[:top_limit], analysis.rank_changes),
            "",
        ]
    )
    if analysis.history_sufficient:
        lines.extend(
            _item_section(
                "新上榜", _select_items(bundle.ranked, analysis.new_ids), top_limit
            )
        )
        lines.extend(
            [
                "## 掉榜",
                "",
                *_dropped_lines(tuple(item.full_name for item in analysis.dropped)),
                "",
            ]
        )
        lines.extend(
            _item_section(
                "本周黑马",
                _select_items(bundle.ranked, analysis.dark_horse_ids),
                top_limit,
            )
        )
    else:
        for title in ("新上榜", "掉榜", "本周黑马"):
            lines.extend(_insufficient_section(title, analysis.insufficient_reason))

    if analysis.warming_history_sufficient:
        lines.extend(
            _item_section(
                "连续升温",
                _select_items(bundle.ranked, analysis.continuous_warming_ids),
                top_limit,
            )
        )
    else:
        lines.extend(
            _insufficient_section("连续升温", "连续升温至少需要 4 个完整日期快照")
        )

    lines.extend(["## 分类榜与份额变化", ""])
    if not bundle.categories:
        lines.extend(["- 暂无分类项目。", ""])
    else:
        for category, items in sorted(bundle.categories.items()):
            lines.extend([f"### {_escape_text(category)}", "", *_repo_lines(items[:top_limit])])
            share = analysis.category_current_shares.get(
                category, _category_share_from_bundle(bundle, category)
            )
            if analysis.history_sufficient:
                change = analysis.category_share_changes.get(category, 0.0)
                lines.extend(
                    [
                        f"- 份额：{_escape_text(category)}：{share:.1f}%（较上期 {change:+.1f} 个百分点）",
                        "",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"- 份额：{share:.1f}%（历史数据不足，无法计算变化）",
                        "",
                    ]
                )

    lines.extend(["## Star 增长趋势", ""])
    if analysis.growth_history_sufficient:
        lines.extend(
            [
                (
                    f"- Top {top_limit} 合计新增 {analysis.stars_growth_total} stars；"
                    f"{analysis.stars_growth_positive} 个增长，"
                    f"{analysis.stars_growth_flat} 个持平。"
                ),
                "",
            ]
        )
    else:
        coverage = (
            f"（当前榜单仅 {analysis.growth_comparable_count}/"
            f"{analysis.growth_chart_count} 个项目具备可比基线）"
            if analysis.growth_chart_count
            else ""
        )
        lines.extend(
            [
                f"- 历史数据不足，尚不能形成可信的 7 日 Star 增长趋势{coverage}。",
                "",
            ]
        )

    lines.extend(_item_section("值得立即试用", bundle.useful, top_limit))

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
            *(
                []
                if bundle.discovery_complete
                else ["- ⚠️ GitHub 发现不完整；已抑制消失、掉榜和榜单迁移结论。"]
            ),
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


def _repo_lines(
    items: tuple[RankedItem, ...], rank_changes: Mapping[int, int] | None = None
) -> list[str]:
    if not items:
        return ["- 暂无符合质量门槛的条目。"]
    return [
        f"{index}. {_link_or_text(repo.full_name, repo.url)} — {_escape_text(summary.one_line)}\n"
        f"   综合分 `{score.total:g}`；{_reasons_text(score.reasons)}"
        f"{_rank_change_text((rank_changes or {}).get(repo.repository_id))}"
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


def _select_items(items: tuple[RankedItem, ...], ids: tuple[int, ...]) -> tuple[RankedItem, ...]:
    wanted = set(ids)
    return tuple(item for item in items if item[0].repository_id in wanted)


def _insufficient_section(title: str, reason: str | None) -> list[str]:
    detail = _escape_text(reason or "缺少完整的上一期榜单")
    return [f"## {title}", "", f"- 历史数据不足：{detail}。", ""]


def _rank_change_text(change: int | None) -> str:
    if change is None:
        return ""
    if change > 0:
        return f"；较上期上升 {change} 位"
    if change < 0:
        return f"；较上期下降 {abs(change)} 位"
    return "；较上期持平"


def _category_share_from_bundle(bundle: ReportBundle, category: str) -> float:
    if not bundle.ranked:
        return 0.0
    ranked_ids = {item[0].repository_id for item in bundle.ranked}
    category_ids = {
        item[0].repository_id for item in bundle.categories.get(category, ())
    }
    return round(len(category_ids & ranked_ids) * 100 / len(ranked_ids), 1)


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
