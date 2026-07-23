from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Protocol


class NotificationError(RuntimeError):
    """A local notification configuration error safe to expose."""


class NotificationPublisher(Protocol):
    def send_document(self, path: Path, caption: str) -> int: ...

    def send_alert(self, text: str) -> int: ...


def extract_daily_summary(markdown: str) -> str:
    lines = markdown.splitlines()
    try:
        heading_index = next(
            index for index, line in enumerate(lines) if line.strip() == "## 今日摘要"
        )
    except StopIteration:
        return "今日摘要暂不可用，请查看完整报告。"
    for line in lines[heading_index + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if stripped:
            return stripped
    return "今日摘要暂不可用，请查看完整报告。"


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
    title = f"AI Agent Radar 日报 · {day.isoformat()}"
    caption = _daily_caption(title, extract_daily_summary(markdown), report_url)
    message_id = publisher.send_document(report_path, caption)
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


def _daily_caption(title: str, summary: str, report_url: str) -> str:
    suffix = f"\nGitHub: {report_url}"
    available = 1024 - len(title) - len(suffix) - 1
    if available < 1:
        raise NotificationError("daily report URL is too long for Telegram caption")
    if len(summary) > available:
        summary = summary[: max(available - 1, 0)] + "…"
    return f"{title}\n{summary}{suffix}"
