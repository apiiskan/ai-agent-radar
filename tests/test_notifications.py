from datetime import date
from pathlib import Path

import pytest

from ai_agent_radar.notifications import (
    NotificationError,
    build_failure_alert,
    extract_daily_summary,
    send_daily_notification,
)


class CapturingPublisher:
    def __init__(self) -> None:
        self.documents: list[tuple[Path, str]] = []
        self.alerts: list[str] = []

    def send_document(self, path: Path, caption: str) -> int:
        self.documents.append((path, caption))
        return 42

    def send_alert(self, text: str) -> int:
        self.alerts.append(text)
        return 43


def test_daily_notification_uses_summary_file_and_stable_github_url(tmp_path) -> None:
    report = tmp_path / "reports/daily/2026-07-23.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        """# AI Agent Radar 日报 · 2026-07-23

## 今日摘要

共排名 294 个项目，展示前 10 个，收录 6 条资讯。

## 今日新发现
1. item
""",
        encoding="utf-8",
    )
    publisher = CapturingPublisher()

    result = send_daily_notification(
        date(2026, 7, 23), tmp_path, "apiiskan/ai-agent-radar", publisher
    )

    assert result == {
        "kind": "daily",
        "message_id": 42,
        "report_path": "reports/daily/2026-07-23.md",
        "report_url": (
            "https://github.com/apiiskan/ai-agent-radar/blob/main/"
            "reports/daily/2026-07-23.md"
        ),
    }
    assert publisher.documents == [
        (
            report.resolve(),
            "AI Agent Radar 日报 · 2026-07-23\n"
            "共排名 294 个项目，展示前 10 个，收录 6 条资讯。\n"
            "GitHub: https://github.com/apiiskan/ai-agent-radar/blob/main/"
            "reports/daily/2026-07-23.md",
        )
    ]


def test_summary_extraction_has_stable_fallback() -> None:
    assert extract_daily_summary("# report\n\n## 今日新发现\n") == (
        "今日摘要暂不可用，请查看完整报告。"
    )


def test_daily_caption_keeps_url_and_stays_within_telegram_limit(tmp_path) -> None:
    report = tmp_path / "reports/daily/2026-07-23.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "# report\n\n## 今日摘要\n\n" + "长" * 2000 + "\n\n## next\n",
        encoding="utf-8",
    )
    publisher = CapturingPublisher()

    send_daily_notification(
        date(2026, 7, 23), tmp_path, "apiiskan/ai-agent-radar", publisher
    )

    caption = publisher.documents[0][1]
    assert len(caption) <= 1024
    assert caption.endswith("/reports/daily/2026-07-23.md")


def test_daily_notification_rejects_missing_report(tmp_path) -> None:
    with pytest.raises(NotificationError, match="daily report does not exist"):
        send_daily_notification(
            date(2026, 7, 23),
            tmp_path,
            "apiiskan/ai-agent-radar",
            CapturingPublisher(),
        )


def test_daily_notification_rejects_report_symlink_outside_root(tmp_path) -> None:
    outside = tmp_path.parent / "outside-report.md"
    outside.write_text("secret report", encoding="utf-8")
    report = tmp_path / "reports/daily/2026-07-23.md"
    report.parent.mkdir(parents=True)
    report.symlink_to(outside)

    with pytest.raises(NotificationError, match="outside repository root"):
        send_daily_notification(
            date(2026, 7, 23),
            tmp_path,
            "apiiskan/ai-agent-radar",
            CapturingPublisher(),
        )


def test_failure_alert_contains_only_bounded_actions_metadata() -> None:
    alert = build_failure_alert(
        {
            "GITHUB_REPOSITORY": "apiiskan/ai-agent-radar",
            "GITHUB_WORKFLOW": "AI Agent Radar Daily",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "12345",
            "UNTRUSTED_EXCEPTION": "report body bot-token",
        },
        generation_exit_code=7,
    )

    assert alert == (
        "⚠️ AI Agent Radar 日报运行失败\n"
        "仓库: apiiskan/ai-agent-radar\n"
        "工作流: AI Agent Radar Daily\n"
        "生成退出码: 7\n"
        "运行: https://github.com/apiiskan/ai-agent-radar/actions/runs/12345"
    )
    assert "report body" not in alert
    assert "bot-token" not in alert
    assert len(alert) < 4096
