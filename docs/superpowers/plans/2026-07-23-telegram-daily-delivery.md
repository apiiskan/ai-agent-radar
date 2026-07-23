# Telegram Daily Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver every successful daily Markdown report to one private Telegram chat, alert on failed daily runs, and provide a secure bootstrap test that discovers the user's `/start` chat without exposing credentials in GitHub logs.

**Architecture:** A focused `telegram.py` module owns Bot API requests and sanitized errors. A `notifications.py` service turns durable reports and GitHub Actions metadata into bounded Telegram payloads, while explicit CLI commands and the daily workflow orchestrate delivery. A one-time bootstrap command discovers exactly one private `/start` chat and sends its full chat ID only into that same private Telegram conversation.

**Tech Stack:** Python 3.11+, `httpx`, `argparse`, `pytest`, GitHub Actions YAML, GitHub CLI.

## Global Constraints

- Use only the official `https://api.telegram.org` Bot API.
- Keep `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` exclusively in GitHub Actions Secrets.
- Do not use third-party Telegram GitHub Actions.
- Upload only `reports/daily/YYYY-MM-DD.md` and reject files larger than 50 MB.
- Use plain text with document captions capped at 1024 characters and alerts below 4096 characters.
- Retry at most three times for HTTP 429, 500, 502, 503, 504, connection errors, and timeouts; cap waits at 60 seconds.
- Never print or persist the bot token, unmasked chat ID, report body, or multipart body.
- Existing report commits and GitHub Issue publication remain the durable source of truth.
- Automated tests must not contact Telegram.

---

### Task 1: Secure Telegram bootstrap test

**Files:**
- Create: `src/ai_agent_radar/telegram.py`
- Modify: `src/ai_agent_radar/cli.py`
- Modify: `.github/workflows/daily.yml`
- Create: `tests/test_telegram.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_workflows.py`

**Interfaces:**
- Produces: `TelegramPublisher(token: str, chat_id: str | None, client: httpx.Client)`
- Produces: `TelegramPublisher.discover_private_start_chat() -> str`
- Produces: `TelegramPublisher.send_alert(text: str, *, chat_id: str | None = None) -> int`
- Produces: CLI command `ai-agent-radar telegram-test`

- [ ] **Step 1: Write failing unit tests**

Add tests using `httpx.MockTransport` that require discovery to accept exactly one private `/start`, reject zero or multiple candidates, send a test message to the discovered chat, and keep the token and full chat ID out of exceptions and CLI output. Add a parser/CLI test requiring only `TELEGRAM_BOT_TOKEN`.

- [ ] **Step 2: Verify the tests fail for the missing module and command**

Run:

```bash
.venv/bin/python -m pytest tests/test_telegram.py tests/test_cli.py -q
```

Expected: failure because `ai_agent_radar.telegram` and `telegram-test` do not exist.

- [ ] **Step 3: Implement the smallest bootstrap client and CLI path**

Implement:

```python
class TelegramError(RuntimeError):
    pass


class TelegramPublisher:
    def __init__(
        self,
        token: str,
        chat_id: str | None,
        client: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None: ...

    def discover_private_start_chat(self) -> str: ...

    def send_alert(self, text: str, *, chat_id: str | None = None) -> int: ...
```

The `telegram-test` command discovers the chat, sends a private message containing `TELEGRAM_CHAT_ID: <full-id>`, and prints only:

```json
{"ok": true, "kind": "telegram-test", "message_id": 123, "chat_id": "***1234"}
```

- [ ] **Step 4: Add a manual workflow input without changing scheduled behavior**

Add a boolean `telegram_test` input to `workflow_dispatch`, a `telegram-test` job guarded by:

```yaml
if: github.event_name == 'workflow_dispatch' && inputs.telegram_test
```

and guard the existing `radar` job with:

```yaml
if: github.event_name != 'workflow_dispatch' || !inputs.telegram_test
```

The bootstrap job receives only `TELEGRAM_BOT_TOKEN` and runs `ai-agent-radar telegram-test`.

- [ ] **Step 5: Verify Task 1**

Run:

```bash
.venv/bin/python -m pytest tests/test_telegram.py tests/test_cli.py tests/test_workflows.py -q
.venv/bin/ruff check src tests
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 6: Commit and run the live branch workflow**

```bash
git add src/ai_agent_radar/telegram.py src/ai_agent_radar/cli.py \
  .github/workflows/daily.yml tests/test_telegram.py tests/test_cli.py \
  tests/test_workflows.py
git commit -m "feat: add secure Telegram bootstrap test"
git push -u origin codex/telegram-daily-delivery
gh workflow run daily.yml --repo apiiskan/ai-agent-radar \
  --ref codex/telegram-daily-delivery -f telegram_test=true
```

Expected: the workflow succeeds and the private Telegram chat receives exactly one test message containing the chat ID needed for `TELEGRAM_CHAT_ID`.

### Task 2: Telegram document publisher and retry contract

**Files:**
- Modify: `src/ai_agent_radar/telegram.py`
- Modify: `tests/test_telegram.py`

**Interfaces:**
- Consumes: `TelegramPublisher`
- Produces: `TelegramPublisher.send_document(path: Path, caption: str) -> int`

- [ ] **Step 1: Write failing transport tests**

Test successful multipart upload fields and bytes, local 50 MB rejection, caption bounds, `ok: false`, non-retryable 4xx, retryable 429/5xx/timeouts, three-attempt cap, and sanitized errors.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_telegram.py -q
```

Expected: failures for missing `send_document` and retry behavior.

- [ ] **Step 3: Implement bounded API requests**

Add `send_document`, a private `_request` loop, safe Telegram response parsing, and bounded retry delays. Open the report only after validating its resolved path and size.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_telegram.py -q
```

Expected: all Telegram transport tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ai_agent_radar/telegram.py tests/test_telegram.py
git commit -m "feat: add Telegram document publisher"
```

### Task 3: Daily notification service and CLI

**Files:**
- Create: `src/ai_agent_radar/notifications.py`
- Modify: `src/ai_agent_radar/cli.py`
- Create: `tests/test_notifications.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `send_daily_notification(day, root, repository, publisher) -> dict[str, object]`
- Produces: `send_failure_notification(environment, generation_exit_code, publisher) -> dict[str, object]`
- Produces: `ai-agent-radar notify daily [--date YYYY-MM-DD] [--root PATH] [--config PATH]`
- Produces: `ai-agent-radar notify failure [--generation-exit-code N]`

- [ ] **Step 1: Write failing service and CLI tests**

Test summary extraction, fallback summary, stable `main` report URL, caption bounds, missing Secrets, missing report, path containment, sanitized success JSON, and alerts that never include report content.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_notifications.py tests/test_cli.py -q
```

Expected: failures because the service and notify commands are missing.

- [ ] **Step 3: Implement report and failure notifications**

Resolve `reports/daily/<date>.md` under `root`, extract the first non-empty sentence below `## 今日摘要`, build `https://github.com/<repository>/blob/main/<relative-path>`, and call the publisher. Build run URLs from `GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`, and `GITHUB_RUN_ID`.

- [ ] **Step 4: Implement CLI result codes**

Return `0` for delivery, `2` for missing/invalid configuration and local report validation, and `1` for sanitized Telegram failures. Successful output contains only kind, message ID, and report path.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python -m pytest tests/test_notifications.py tests/test_cli.py -q
git add src/ai_agent_radar/notifications.py src/ai_agent_radar/cli.py \
  tests/test_notifications.py tests/test_cli.py
git commit -m "feat: add Telegram notification commands"
```

### Task 4: Daily workflow delivery and failure alerts

**Files:**
- Modify: `.github/workflows/daily.yml`
- Modify: `tests/test_workflows.py`

**Interfaces:**
- Consumes: `ai-agent-radar notify daily`
- Consumes: `ai-agent-radar notify failure`

- [ ] **Step 1: Write failing workflow-contract tests**

Assert the report notification follows durable Issue publication, receives both Telegram Secrets, the failure alert uses `if: always()`, the successful path does not send an alert, and generation status propagation remains last.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflows.py -q
```

Expected: failures because production Telegram notification steps are absent.

- [ ] **Step 3: Wire production notification steps**

Run `ai-agent-radar notify daily` only after successful generation, commit, and Issue publication. Run `ai-agent-radar notify failure --generation-exit-code ...` on an earlier failure or non-zero generation status. Give Telegram steps only the two Telegram Secrets plus standard Actions metadata.

- [ ] **Step 4: Verify and commit**

```bash
.venv/bin/python -m pytest tests/test_workflows.py -q
git add .github/workflows/daily.yml tests/test_workflows.py
git commit -m "ci: deliver daily reports to Telegram"
```

### Task 5: Secure local configuration, documentation, and full regression

**Files:**
- Create: `scripts/configure_telegram.py`
- Create: `tests/test_configure_telegram.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `TelegramPublisher.discover_private_start_chat()`
- Produces: hidden-token local setup for `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

- [ ] **Step 1: Write failing configuration tests**

Test hidden input, `getMe`, exactly-one chat discovery, webhook/ambiguity failures, and two `gh secret set --repo apiiskan/ai-agent-radar` subprocesses receiving values through standard input.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_configure_telegram.py -q
```

Expected: failure because the script does not exist.

- [ ] **Step 3: Implement the setup utility**

Use `getpass.getpass`, `shutil.which("gh")`, `gh auth status`, the shared Telegram client, and `subprocess.run(..., input=value, text=True, check=True)`. Print only bot username, masked chat ID, and success.

- [ ] **Step 4: Document setup, manual test, and recovery**

Document `/start`, repository Secret names, the bootstrap workflow, `scripts/configure_telegram.py`, normal daily delivery, and failure diagnosis without including real credentials or chat identifiers.

- [ ] **Step 5: Run the full verification suite**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
git diff --check
```

Expected: all tests pass, Ruff reports no errors, and `git diff --check` is silent.

- [ ] **Step 6: Commit**

```bash
git add scripts/configure_telegram.py tests/test_configure_telegram.py README.md
git commit -m "docs: add secure Telegram setup"
```
