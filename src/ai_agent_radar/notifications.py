from __future__ import annotations

import re
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

MAX_TELEGRAM_MESSAGE_LENGTH = 4096
_ENTRY_RE = re.compile(
    r"^(?P<rank>\d+)\. "
    r"\[(?P<repository>[^\]]+)\]"
    r"\((?P<url>https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\)"
    r"(?:\s+—.*)?$"
)
_SCORE_RE = re.compile(r"综合分 `(?P<score>[0-9]+(?:\.[0-9]+)?)`")
_STARS_1D_RE = re.compile(r"近 1 日新增 (?P<count>\d+) stars")
_STARS_7D_RE = re.compile(r"7 日新增 (?P<count>\d+) stars")


class NotificationError(RuntimeError):
    """A local notification configuration error safe to expose."""


@dataclass(frozen=True)
class GrowthEntry:
    rank: int
    repository: str
    score: str
    stars_1d: int | None
    stars_7d: int | None
    url: str


class NotificationPublisher(Protocol):
    def send_alert(self, text: str) -> int: ...


def extract_growth_top(markdown: str, limit: int = 10) -> tuple[GrowthEntry, ...]:
    if limit < 1:
        raise NotificationError("growth ranking limit must be positive")
    lines = markdown.splitlines()
    try:
        heading_index = next(
            index for index, line in enumerate(lines) if line.strip() == "## 增长最快"
        )
    except StopIteration:
        raise NotificationError("growth ranking is empty")
    section: list[str] = []
    for line in lines[heading_index + 1 :]:
        if line.strip().startswith("## "):
            break
        section.append(line)

    entries: list[GrowthEntry] = []
    for index, line in enumerate(section):
        entry_match = _ENTRY_RE.fullmatch(line.strip())
        if not entry_match or index + 1 >= len(section):
            continue
        details = section[index + 1].strip()
        score_match = _SCORE_RE.search(details)
        if not score_match:
            continue
        stars_1d_match = _STARS_1D_RE.search(details)
        stars_7d_match = _STARS_7D_RE.search(details)
        entries.append(
            GrowthEntry(
                rank=int(entry_match.group("rank")),
                repository=entry_match.group("repository"),
                score=score_match.group("score"),
                stars_1d=(
                    int(stars_1d_match.group("count"))
                    if stars_1d_match
                    else None
                ),
                stars_7d=(
                    int(stars_7d_match.group("count"))
                    if stars_7d_match
                    else None
                ),
                url=entry_match.group("url"),
            )
        )
        if len(entries) == limit:
            break
    if not entries:
        raise NotificationError("growth ranking is empty")
    return tuple(entries)


def render_growth_message(
    day: date,
    entries: Sequence[GrowthEntry],
    report_url: str,
) -> str:
    candidates = tuple(entries[:10])
    if not candidates:
        raise NotificationError("growth ranking is empty")
    message = _compose_growth_message(day, candidates, report_url, omit_missing=False)
    if len(message) <= MAX_TELEGRAM_MESSAGE_LENGTH:
        return message

    selected: list[GrowthEntry] = []
    for entry in candidates:
        trial = (*selected, entry)
        message = _compose_growth_message(
            day,
            trial,
            report_url,
            omit_missing=True,
        )
        if len(message) > MAX_TELEGRAM_MESSAGE_LENGTH:
            break
        selected.append(entry)
    if not selected:
        raise NotificationError("growth ranking entry is too long for Telegram")
    return _compose_growth_message(
        day,
        tuple(selected),
        report_url,
        omit_missing=True,
    )


def send_daily_notification(
    day: date,
    root: Path,
    repository: str,
    publisher: NotificationPublisher,
) -> dict[str, object]:
    resolved_root = root.resolve()
    relative_path = Path("reports") / "daily" / f"{day.isoformat()}.md"
    report_path = (resolved_root / relative_path).resolve()
    if not report_path.is_relative_to(resolved_root):
        raise NotificationError("daily report resolves outside repository root")
    if not report_path.is_file():
        raise NotificationError("daily report does not exist")
    try:
        markdown = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise NotificationError("daily report cannot be read") from exc
    report_url = (
        f"https://github.com/{repository}/blob/main/{relative_path.as_posix()}"
    )
    entries = extract_growth_top(markdown)
    message = render_growth_message(day, entries, report_url)
    message_id = publisher.send_alert(message)
    return {
        "kind": "daily",
        "message_id": message_id,
        "report_path": relative_path.as_posix(),
        "report_url": report_url,
    }


def build_failure_alert(
    environment: Mapping[str, str],
    generation_exit_code: int | None,
) -> str:
    repository = environment.get("GITHUB_REPOSITORY") or "unknown"
    workflow = environment.get("GITHUB_WORKFLOW") or "AI Agent Radar Daily"
    lines = [
        "⚠️ AI Agent Radar 日报运行失败",
        f"仓库: {repository}",
        f"工作流: {workflow}",
    ]
    if generation_exit_code is not None:
        lines.append(f"生成退出码: {generation_exit_code}")
    server = environment.get("GITHUB_SERVER_URL") or "https://github.com"
    run_id = environment.get("GITHUB_RUN_ID")
    if run_id:
        lines.append(
            f"运行: {server.rstrip('/')}/{repository}/actions/runs/{run_id}"
        )
    return "\n".join(lines)[:4096]


def send_failure_notification(
    environment: Mapping[str, str],
    generation_exit_code: int | None,
    publisher: NotificationPublisher,
) -> dict[str, object]:
    message_id = publisher.send_alert(
        build_failure_alert(environment, generation_exit_code)
    )
    return {"kind": "failure", "message_id": message_id}


def _compose_growth_message(
    day: date,
    entries: Sequence[GrowthEntry],
    report_url: str,
    *,
    omit_missing: bool,
) -> str:
    title = (
        f"🔥 AI Agent Radar · 增长最快 Top {len(entries)} · {day.isoformat()}"
    )
    blocks = [
        _growth_entry_block(entry, omit_missing=omit_missing)
        for entry in entries
    ]
    return (
        f"{title}\n\n"
        + "\n\n".join(blocks)
        + f"\n\n完整日报:\n{report_url}"
    )


def _growth_entry_block(entry: GrowthEntry, *, omit_missing: bool) -> str:
    metrics = [f"综合分 {entry.score}"]
    for label, value in (("1日", entry.stars_1d), ("7日", entry.stars_7d)):
        if value is None and omit_missing:
            continue
        growth = "暂无数据" if value is None else f"+{value}★"
        metrics.append(f"{label} {growth}")
    return (
        f"{entry.rank}. {entry.repository}\n"
        + " · ".join(metrics)
        + f"\n{entry.url}"
    )
