from datetime import date
from pathlib import Path

import pytest

from ai_agent_radar.notifications import (
    GrowthEntry,
    NotificationError,
    build_failure_alert,
    extract_growth_top,
    render_growth_message,
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


def growth_report(count: int, *, include_other_sections: bool = True) -> str:
    before = (
        "## 今日新发现\n\n"
        "1. [wrong/section](https://github.com/wrong/section)\n"
        "   综合分 `99`；热度：近 1 日新增 999 stars；热度：7 日新增 999 stars\n\n"
        if include_other_sections
        else ""
    )
    entries = []
    for rank in range(1, count + 1):
        one_day = 54 if rank == 1 else rank
        seven_day = 123 if rank == 1 else rank * 2
        entries.append(
            f"{rank}. [owner/repo-{rank}]"
            f"(https://github.com/owner/repo-{rank}) — description\n"
            f"   综合分 `84.{rank:02d}`；热度：近 1 日新增 {one_day} stars；"
            f"热度：7 日新增 {seven_day} stars；实用性：包含 README"
        )
    return (
        "# report\n\n"
        f"{before}"
        "## 增长最快\n\n"
        + "\n".join(entries)
        + "\n\n## 最实用\n\n"
        "1. [wrong/after](https://github.com/wrong/after)\n"
        "   综合分 `98`；热度：近 1 日新增 888 stars\n"
    )


def test_extract_growth_top_preserves_order_ignores_other_sections_and_limits_to_ten() -> None:
    entries = extract_growth_top(growth_report(12))

    assert len(entries) == 10
    assert entries[0] == GrowthEntry(
        rank=1,
        repository="owner/repo-1",
        score="84.01",
        stars_1d=54,
        stars_7d=123,
        url="https://github.com/owner/repo-1",
    )
    assert entries[-1].repository == "owner/repo-10"
    assert all("wrong/" not in entry.repository for entry in entries)


def test_extract_growth_top_skips_malformed_entries_and_keeps_missing_growth() -> None:
    markdown = """# report

## 增长最快

1. malformed entry
   综合分 `99`；热度：近 1 日新增 99 stars
2. [owner/valid](https://github.com/owner/valid) — description
   综合分 `72.5`；实用性：包含 README
3. [not-github/repo](https://example.com/repo)
   综合分 `70`

## 最实用
"""

    assert extract_growth_top(markdown) == (
        GrowthEntry(
            rank=2,
            repository="owner/valid",
            score="72.5",
            stars_1d=None,
            stars_7d=None,
            url="https://github.com/owner/valid",
        ),
    )


@pytest.mark.parametrize(
    "markdown",
    [
        "# report\n\n## 最实用\n",
        "# report\n\n## 增长最快\n\nmalformed\n\n## 最实用\n",
    ],
)
def test_extract_growth_top_rejects_missing_or_empty_valid_section(markdown) -> None:
    with pytest.raises(NotificationError, match="growth ranking is empty"):
        extract_growth_top(markdown)


def test_render_growth_message_formats_missing_and_zero_growth() -> None:
    entries = (
        GrowthEntry(
            rank=1,
            repository="owner/repo",
            score="84.23",
            stars_1d=0,
            stars_7d=None,
            url="https://github.com/owner/repo",
        ),
    )

    message = render_growth_message(
        date(2026, 7, 24),
        entries,
        "https://github.com/o/r/blob/main/reports/daily/2026-07-24.md",
    )

    assert message == (
        "🔥 AI Agent Radar · 增长最快 Top 1 · 2026-07-24\n\n"
        "1. owner/repo\n"
        "综合分 84.23 · 1日 +0★ · 7日 暂无数据\n"
        "https://github.com/owner/repo\n\n"
        "完整日报:\n"
        "https://github.com/o/r/blob/main/reports/daily/2026-07-24.md"
    )


def test_render_growth_message_stops_before_overflow_without_truncating_links() -> None:
    entries = tuple(
        GrowthEntry(
            rank=rank,
            repository=f"owner/{'r' * 350}-{rank}",
            score="80",
            stars_1d=None,
            stars_7d=None,
            url=f"https://github.com/owner/{'r' * 350}-{rank}",
        )
        for rank in range(1, 11)
    )

    message = render_growth_message(
        date(2026, 7, 24),
        entries,
        "https://github.com/o/r/blob/main/reports/daily/2026-07-24.md",
    )

    assert len(message) <= 4096
    rendered_count = int(message.split("Top ", 1)[1].split(" ", 1)[0])
    assert 0 < rendered_count < 10
    for entry in entries[:rendered_count]:
        assert entry.repository in message
        assert entry.url in message
    assert entries[rendered_count].repository not in message


def test_daily_notification_sends_growth_text_and_stable_github_url(tmp_path) -> None:
    report = tmp_path / "reports/daily/2026-07-23.md"
    report.parent.mkdir(parents=True)
    report.write_text(growth_report(10), encoding="utf-8")
    publisher = CapturingPublisher()

    result = send_daily_notification(
        date(2026, 7, 23), tmp_path, "apiiskan/ai-agent-radar", publisher
    )

    assert result == {
        "kind": "daily",
        "message_id": 43,
        "report_path": "reports/daily/2026-07-23.md",
        "report_url": (
            "https://github.com/apiiskan/ai-agent-radar/blob/main/"
            "reports/daily/2026-07-23.md"
        ),
    }
    assert publisher.documents == []
    assert len(publisher.alerts) == 1
    message = publisher.alerts[0]
    assert "增长最快 Top 10" in message
    assert "owner/repo-1" in message
    assert "owner/repo-10" in message
    assert message.endswith("/reports/daily/2026-07-23.md")


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
