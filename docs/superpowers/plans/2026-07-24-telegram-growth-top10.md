# Telegram Growth Top 10 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace successful Telegram Markdown attachment delivery with one bounded plain-text message containing the first ten projects from the daily report's `增长最快` ranking.

**Architecture:** Extend the existing notification service with an immutable parsed ranking entry, a deterministic Markdown section parser, and a length-bounded text renderer. Keep the CLI command and Telegram transport unchanged, but switch the service from `send_document` to `send_alert` and rename the visible Actions step.

**Tech Stack:** Python 3.11+, standard-library `dataclasses` and `re`, existing `httpx` Telegram transport, `pytest`, Ruff, GitHub Actions YAML.

## Global Constraints

- Use only the first ten valid entries under the exact `## 增长最快` heading.
- Preserve report order and never re-rank or substitute another section.
- Send exactly one plain-text Telegram message and no Markdown attachment.
- Include rank, repository full name, composite score, one-day Star growth, seven-day Star growth, repository URL, and complete-report URL.
- Render missing growth as `暂无数据` and zero growth as `+0★`.
- Never exceed the Telegram `sendMessage` limit of 4096 characters.
- Never truncate a repository name or URL and never split output across messages.
- Keep GitHub publication, Secrets, retries, sanitized failure alerts, and generation status propagation unchanged.
- Automated tests must not contact Telegram.

---

### Task 1: Growth ranking parser and bounded message renderer

**Files:**
- Modify: `src/ai_agent_radar/notifications.py`
- Modify: `tests/test_notifications.py`

**Interfaces:**
- Produces: `GrowthEntry(rank: int, repository: str, score: str, stars_1d: int | None, stars_7d: int | None, url: str)`
- Produces: `extract_growth_top(markdown: str, limit: int = 10) -> tuple[GrowthEntry, ...]`
- Produces: `render_growth_message(day: date, entries: Sequence[GrowthEntry], report_url: str) -> str`
- Changes: `send_daily_notification(...)` calls `publisher.send_alert(message)` instead of `publisher.send_document(path, caption)`

- [ ] **Step 1: Write failing parser tests**

Add a real-shaped report fixture inline and require exact parsed values:

```python
def test_extract_growth_top_preserves_order_and_limits_to_ten():
    markdown = growth_report(12)
    entries = extract_growth_top(markdown)
    assert len(entries) == 10
    assert entries[0] == GrowthEntry(
        rank=1,
        repository="owner/repo-1",
        score="84.23",
        stars_1d=54,
        stars_7d=123,
        url="https://github.com/owner/repo-1",
    )
    assert entries[-1].repository == "owner/repo-10"
```

Add separate cases proving other sections are ignored, malformed entries are skipped, a missing/empty section raises `NotificationError`, and one-to-nine valid entries succeed.

- [ ] **Step 2: Run parser tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_notifications.py -q
```

Expected: failures because `GrowthEntry` and `extract_growth_top` do not exist.

- [ ] **Step 3: Implement the immutable entry and parser**

Add:

```python
@dataclass(frozen=True)
class GrowthEntry:
    rank: int
    repository: str
    score: str
    stars_1d: int | None
    stars_7d: int | None
    url: str
```

Use anchored regular expressions for ordered Markdown links, `综合分`, `近 1 日新增 N stars`, and `7 日新增 N stars`. Slice only the lines between `## 增长最快` and the next `## ` heading, consume the immediately following indented scoring line, preserve valid-entry order, and stop at `limit`.

- [ ] **Step 4: Run parser tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_notifications.py -q
```

Expected: all parser tests pass.

- [ ] **Step 5: Write failing renderer and service tests**

Require the compact output:

```python
assert message == (
    "🔥 AI Agent Radar · 增长最快 Top 1 · 2026-07-24\n\n"
    "1. owner/repo\n"
    "综合分 84.23 · 1日 +54★ · 7日 暂无数据\n"
    "https://github.com/owner/repo\n\n"
    "完整日报:\n"
    "https://github.com/o/r/blob/main/reports/daily/2026-07-24.md"
)
```

Add cases for `+0★`, one-to-nine actual title counts, bounded output with long valid repository names, and service behavior asserting one `send_alert` call and zero `send_document` calls.

- [ ] **Step 6: Run renderer tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_notifications.py -q
```

Expected: failures because `render_growth_message` is missing and the service still uploads a document.

- [ ] **Step 7: Implement bounded rendering and switch delivery**

Format each entry as:

```python
block = (
    f"{entry.rank}. {entry.repository}\n"
    f"综合分 {entry.score} · 1日 {_growth(entry.stars_1d)}"
    f" · 7日 {_growth(entry.stars_7d)}\n"
    f"{entry.url}"
)
```

Reserve the complete-report suffix first, append whole blocks while the message remains at most 4096 characters, omit only unavailable growth labels on overflow, and stop before a whole block would overflow. Raise `NotificationError` if no full entry fits. Update `send_daily_notification` to parse, render, and call `publisher.send_alert`.

- [ ] **Step 8: Verify Task 1 and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_notifications.py tests/test_cli.py -q
.venv/bin/ruff check src/ai_agent_radar/notifications.py tests/test_notifications.py
git add src/ai_agent_radar/notifications.py tests/test_notifications.py
git commit -m "feat: send Telegram growth Top 10"
```

Expected: all selected tests pass and Ruff reports no errors.

### Task 2: Workflow contract, documentation, regression, and live delivery

**Files:**
- Modify: `.github/workflows/daily.yml`
- Modify: `tests/test_workflows.py`
- Modify: `README.md`
- Modify: `tests/test_configure_telegram.py`

**Interfaces:**
- Consumes: existing `ai-agent-radar notify daily`
- Preserves: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` Secret wiring

- [ ] **Step 1: Write failing workflow and documentation tests**

Require the successful step name and command:

```python
step = next(
    step
    for step in workflow["jobs"]["radar"]["steps"]
    if step.get("name") == "Send Telegram growth Top 10"
)
assert step["run"] == "ai-agent-radar notify daily"
assert step["env"] == {
    "TELEGRAM_BOT_TOKEN": "${{ secrets.TELEGRAM_BOT_TOKEN }}",
    "TELEGRAM_CHAT_ID": "${{ secrets.TELEGRAM_CHAT_ID }}",
}
```

Update README assertions to require `增长最快 Top 10`, `一条文本消息`, and the complete GitHub report link behavior, while rejecting language that says successful delivery sends the complete Markdown attachment.

- [ ] **Step 2: Run contract tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflows.py tests/test_configure_telegram.py -q
```

Expected: failures because the workflow and README still describe full-report delivery.

- [ ] **Step 3: Rename the workflow step and revise README**

Change only:

```yaml
- name: Send Telegram growth Top 10
  if: steps.generate.outputs.exit_code == '0' && success()
  env:
    TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
    TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
  run: ai-agent-radar notify daily
```

Document that Telegram receives one plain-text `增长最快 Top 10` message and that the complete Markdown report stays on GitHub. Keep setup, bootstrap test, failure recovery, and Secret instructions.

- [ ] **Step 4: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
git diff --check
```

Expected: all tests pass, Ruff reports no errors, and `git diff --check` is silent.

- [ ] **Step 5: Commit and push**

Run:

```bash
git add .github/workflows/daily.yml tests/test_workflows.py README.md \
  tests/test_configure_telegram.py
git commit -m "docs: document Telegram growth Top 10"
git push origin codex/telegram-daily-delivery
```

Expected: the feature branch is up to date on GitHub.

- [ ] **Step 6: Trigger and verify live delivery**

Run:

```bash
gh workflow run daily.yml -R apiiskan/ai-agent-radar \
  --ref codex/telegram-daily-delivery -f telegram_test=false
gh run watch RUN_ID -R apiiskan/ai-agent-radar --exit-status
```

Expected: `radar` succeeds, `Send Telegram growth Top 10` succeeds, the failure alert and status propagation steps are skipped, and Telegram receives one plain-text Top 10 message with no attachment.
