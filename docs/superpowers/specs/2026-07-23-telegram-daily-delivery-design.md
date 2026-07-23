# Telegram Daily Delivery Design

## Purpose

Extend AI Agent Radar so every successful daily run sends the user a concise
Telegram summary, the complete durable Markdown report as a document, and the
GitHub report URL. If daily generation is degraded or any earlier workflow step
fails, send a short Telegram alert instead of a report attachment.

The integration must keep the existing GitHub Issue and repository report
publication flow intact. Telegram is an additional delivery channel, not the
source of truth.

## Goals

- Deliver the complete daily report to one private Telegram chat.
- Attach the existing `reports/daily/YYYY-MM-DD.md` file without truncating it.
- Include the report date, generated summary sentence, and GitHub URL in the
  document caption.
- Send a concise alert when generation is degraded or an earlier workflow step
  fails.
- Keep the bot token and chat ID exclusively in GitHub Secrets.
- Avoid third-party Telegram GitHub Actions.
- Provide a one-time local setup utility that discovers the private chat ID and
  writes both GitHub Secrets without printing or storing the token.
- Preserve deterministic tests by mocking Telegram rather than sending real
  messages in CI.

## Non-goals

- Telegram groups, channels, topics, webhooks, commands, or interactive bot
  behavior.
- Sending the weekly report.
- Rendering the Markdown report into Telegram-native rich messages.
- Replacing GitHub Issues or repository reports.
- Persisting Telegram API responses, chat content, tokens, or chat IDs in the
  repository.
- Automatically creating a bot through BotFather.

## Selected Approach

Implement a first-party `TelegramPublisher` using the project's existing
`httpx` dependency. Add explicit CLI notification commands and call them from
the existing daily GitHub Actions workflow after durable report publication.

This is preferred over shell `curl` because multipart uploads, retry behavior,
response validation, and secret redaction can be tested directly. It is
preferred over third-party Actions because the bot token is not delegated to an
additional supply-chain component.

## Telegram API Constraints

The implementation uses the official Bot API over HTTPS:

- `sendDocument` uploads the Markdown file with `multipart/form-data`.
- `sendMessage` sends failure alerts.
- Documents are rejected locally if they exceed the current official 50 MB
  Bot API limit.
- Document captions are plain text and capped at the current official
  1024-character post-entity limit.
- Failure alerts are plain text and capped below the current official
  4096-character message limit.

Official reference:
<https://core.telegram.org/bots/api#senddocument>

No Telegram parse mode is used. This avoids formatting failures or injection
from repository names, summaries, or URLs.

## Components

### `TelegramPublisher`

Create `src/ai_agent_radar/telegram.py` with one focused class:

- Constructor inputs: bot token, chat ID, and optional `httpx.Client`.
- `send_document(path, caption)`: validates the file, uploads it, validates the
  Bot API response, and returns the sent message ID.
- `send_alert(text)`: validates and sends a plain-text message, then returns the
  sent message ID.

The class constructs the API URL internally. The token must never be included
in an exception, log message, result model, or command output. Telegram error
responses may be summarized using status code and a sanitized description.

### Notification service

Create a small notification module that owns report-specific behavior:

- Resolve the daily report path from the configured timezone and optional
  `--date`.
- Read the durable Markdown file already written by the pipeline.
- Extract the title and the first non-empty sentence under `## 今日摘要`.
- Build the stable GitHub report URL from `GITHUB_REPOSITORY`, the default
  branch `main`, and the report-relative path.
- Build a plain-text caption within 1024 characters.
- Send the Markdown document through `TelegramPublisher`.
- Build a generic failure alert containing the repository, workflow name, run
  URL, generation exit code when available, and no raw exception or log text.

The GitHub Actions run URL is derived from `GITHUB_SERVER_URL`,
`GITHUB_REPOSITORY`, and `GITHUB_RUN_ID`.

### CLI

Extend the existing CLI with:

```text
ai-agent-radar notify daily [--date YYYY-MM-DD] [--root PATH] [--config PATH]
ai-agent-radar notify failure [--generation-exit-code N]
```

Both commands require:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GITHUB_REPOSITORY`

`notify daily` also requires the durable report file to exist. Missing
configuration, an invalid report path, or a report over 50 MB returns exit code
`2`. Telegram network or API failures return exit code `1`. Successful delivery
returns `0` and prints only non-sensitive JSON containing the delivery kind and
Telegram message ID.

`notify failure` never reads or attaches the report. It sends only a short,
sanitized alert.

## Workflow

Update `.github/workflows/daily.yml` while preserving the current generation,
commit, and GitHub Issue steps.

The resulting order is:

1. Generate the daily report and retain the generation exit code.
2. Commit and push generated `data/` and `reports/`.
3. Publish the durable GitHub Issue when generation succeeded.
4. Send the Telegram document when all earlier steps succeeded and the
   generation exit code is `0`.
5. Send a Telegram failure alert when an earlier step failed or the generation
   exit code is non-zero.
6. Propagate a non-zero generation exit code so degraded collection remains a
   failed run.

The Telegram steps receive:

```text
TELEGRAM_BOT_TOKEN: secrets.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID: secrets.TELEGRAM_CHAT_ID
```

The existing GitHub publication step retains its own `GITHUB_TOKEN`, but the
Telegram steps do not receive it. The notification command uses standard
GitHub Actions environment variables for repository and run metadata.

If Telegram delivery fails after a successful GitHub publication, the workflow
fails while leaving the committed report and GitHub Issue intact. This makes a
broken notification channel visible without losing the report.

## Retry and Error Handling

Telegram requests use bounded retries:

- Retry HTTP `429` using Telegram's `parameters.retry_after` when present.
- Retry HTTP `500`, `502`, `503`, `504`, connection errors, and timeouts.
- Use at most three attempts.
- Bound each retry delay to a safe maximum of 60 seconds.
- Do not retry other `4xx` responses.

Every final failure raises a sanitized domain error. The bot token, chat ID,
multipart body, and report contents are excluded from error strings.

## One-time Secure Configuration

Add `scripts/configure_telegram.py` for local use only:

1. Verify `gh` exists and is authenticated for the target repository.
2. Read the Bot Token using hidden terminal input.
3. Call Telegram `getMe` to validate the token.
4. Call `getUpdates` and collect unique private chats containing a `/start`
   message sent to the bot.
5. Continue only when exactly one unique private chat matches; refuse zero or
   multiple matches instead of guessing.
6. Write `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` with
   `gh secret set --repo apiiskan/ai-agent-radar`, passing values over standard
   input.
7. Print only the bot username, a masked chat identifier, and confirmation that
   both Secrets were set.

The utility does not write a local config file, shell history entry, temporary
token file, or repository artifact. It does not delete webhooks or pending
updates. If a webhook prevents `getUpdates`, it stops with a clear instruction.

The user must first open the bot's private chat and send `/start`.

## Security

- The Bot Token and chat ID are GitHub Secrets, never Variables.
- Secrets are never passed as command-line arguments.
- API requests use only `https://api.telegram.org`.
- Report filenames and paths are resolved under the repository root; path
  traversal and arbitrary file upload are rejected.
- Only the generated daily Markdown report may be attached.
- Telegram responses are not persisted.
- CI tests use mocked transports and synthetic tokens.
- The implementation does not broaden repository permissions beyond the
  existing `contents: write` and `issues: write`.

## Tests

Add focused tests for:

- Successful multipart document upload and message ID parsing.
- Correct chat ID, filename, caption, MIME type, and stable GitHub URL.
- Summary extraction from a real-shaped daily report.
- Missing summary fallback.
- Missing Secrets and missing report failures.
- Path traversal rejection.
- 50 MB local file-size rejection.
- Caption and alert length bounds.
- `429` retry behavior using `retry_after`.
- Retryable `5xx`, timeout, and connection failures.
- Non-retryable `4xx` behavior.
- Telegram `ok: false` response validation.
- Token, chat ID, report body, and multipart content redaction from errors and
  CLI output.
- Failure alerts never attach report content.
- Chat discovery accepts one private `/start`, rejects zero or ambiguous
  candidates, and sends secret values to `gh` over standard input.
- Workflow contract: success notification order, failure notification
  condition, Secret wiring, and generation status propagation.
- README documentation for setup, manual verification, and recovery.

No automated test contacts Telegram.

## Manual Verification

After implementation and GitHub Secret configuration:

1. Send `/start` to the bot in a private chat.
2. Run the local configuration utility.
3. Manually dispatch `AI Agent Radar Daily`.
4. Confirm the Action succeeds.
5. Confirm Telegram receives one document named for the current daily report.
6. Confirm the caption contains the date, summary sentence, and working GitHub
   URL.
7. Download the attachment and compare its bytes with the committed report.

A synthetic failure command may be tested locally against a mocked endpoint,
but production failure alerts are not triggered by intentionally breaking the
live daily workflow.

## Acceptance Criteria

- A successful scheduled or manually dispatched daily run sends exactly one
  complete Markdown attachment to the configured private chat.
- The attachment matches the committed report byte-for-byte.
- The caption includes the date, report summary, and GitHub URL.
- A degraded generation sends an alert and no attachment, then the workflow
  fails with the original generation status.
- An earlier workflow failure sends an alert when the notification step can
  still run.
- Missing or invalid Telegram configuration fails explicitly without exposing
  secrets.
- Existing GitHub Issue publication and report commits continue to work.
- Unit, CLI, workflow-contract, full regression, and lint checks pass.
