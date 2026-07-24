# Telegram Growth Top 10 Design

## Purpose

Change the daily Telegram delivery from a complete Markdown attachment to one
compact plain-text message showing the ten fastest-growing GitHub projects.
The complete daily report remains available in the repository and its durable
GitHub Issue.

This design supersedes only the successful-delivery format in
`2026-07-23-telegram-daily-delivery-design.md`. Secret handling, failure
alerts, retries, workflow ordering, and GitHub publication remain unchanged.

## Selected Ranking

Telegram uses the first ten ordered entries under the daily report heading:

```text
## 增长最快
```

It does not combine, re-rank, or substitute entries from `今日新发现`,
`最实用`, or category rankings. The report generator remains the sole source
of ranking truth.

If the section contains between one and nine entries, Telegram sends all
available entries and states the actual count. If it is missing or contains no
valid entries, successful delivery fails explicitly so the workflow can send
the existing sanitized failure alert.

## Message Format

Successful delivery sends exactly one plain-text Telegram message:

```text
🔥 AI Agent Radar · 增长最快 Top 10 · 2026-07-24

1. owner/repository
综合分 84.23 · 1日 +54★ · 7日 +123★
https://github.com/owner/repository

...

完整日报:
https://github.com/apiiskan/ai-agent-radar/blob/main/reports/daily/2026-07-24.md
```

Each entry contains:

- rank;
- repository full name;
- composite score;
- one-day Star growth;
- seven-day Star growth;
- original GitHub repository URL.

Missing one-day or seven-day Star growth is displayed as `暂无数据`; zero is
displayed as `+0★`. Repository descriptions and scoring explanations are not
included.

No Telegram parse mode is used. The message is plain text, so repository names
and URLs cannot cause Telegram formatting errors.

## Parsing

Add a focused parser to the notification service. It reads the durable daily
Markdown report and:

1. locates the exact `## 增长最快` heading;
2. stops at the next level-two heading;
3. accepts ordered-list entries whose first line contains a Markdown GitHub
   repository link;
4. reads the immediately following indented scoring line;
5. extracts `综合分`, `近 1 日新增 N stars`, and `7 日新增 N stars`;
6. keeps the existing order and returns at most ten entries.

The parser does not execute Markdown, follow links, or infer data from project
descriptions. Malformed entries are skipped. A fully empty parsed ranking is a
local notification error.

## Length Handling

The Bot API `sendMessage` limit is 4096 characters. The renderer reserves space
for the title and complete-report URL, then adds entries in order.

The compact required fields are expected to fit for ten normal GitHub
repository names and URLs. If the rendered message would exceed 4096
characters:

1. omit one-day and seven-day growth labels that are `暂无数据`;
2. if still too long, stop before the first entry that would exceed the limit;
3. state the actual number sent in the title.

The renderer never truncates a repository name or URL and never splits the
ranking across multiple Telegram messages.

## Components

### Notification service

Replace the successful daily document upload in
`src/ai_agent_radar/notifications.py` with:

- `GrowthEntry`: immutable parsed entry;
- `extract_growth_top(markdown, limit=10)`: deterministic parser;
- `render_growth_message(day, entries, report_url)`: bounded plain-text
  renderer;
- `send_daily_notification(...)`: reads the same durable report, renders the
  message, and calls `TelegramPublisher.send_alert`.

The non-sensitive CLI result retains `kind`, `message_id`, `report_path`, and
`report_url`.

### Telegram publisher

The existing `send_alert` transport sends the successful Top 10 message and
continues to send failure alerts. `send_document` may remain as a tested
general-purpose transport method, but the daily workflow no longer calls it.

### Workflow

The existing command remains:

```text
ai-agent-radar notify daily
```

No new Secret, permission, Action, or workflow step is required. The workflow
step is renamed from `Send Telegram daily report` to
`Send Telegram growth Top 10` so its behavior is visible in Actions.

## Error Handling

- Missing or unreadable report: existing local notification error, exit code
  `2`.
- Missing or empty `增长最快`: local notification error, exit code `2`.
- One to nine valid entries: successful delivery with the actual count.
- Malformed individual entry: skip it and preserve the order of valid entries.
- Telegram network/API failure: sanitized exit code `1` behavior remains
  unchanged.
- Failure alerts contain workflow metadata only and never include report
  content.

## Tests

Add deterministic tests for:

- parsing the first ten entries from a real-shaped `增长最快` section;
- preserving report order without re-ranking;
- ignoring other report sections;
- score, one-day Star growth, and seven-day Star growth extraction;
- `暂无数据` and `+0★` rendering;
- one-to-nine-entry success with the actual count;
- missing and empty section failures;
- malformed entry skipping;
- 4096-character bound without truncated repository names or URLs;
- one `sendMessage` call and no document upload;
- stable complete-report URL;
- non-sensitive CLI output;
- updated GitHub Actions step name and unchanged Secret wiring;
- full regression and Ruff checks.

No automated test contacts Telegram.

## Acceptance Criteria

- Each successful daily run sends exactly one Telegram text message.
- The message contains at most the first ten valid projects from
  `## 增长最快`, in the same order as the report.
- Each displayed project contains its rank, repository, composite score,
  available one-day and seven-day Star growth, and GitHub URL.
- The message contains a working link to the complete report on `main`.
- The message never exceeds 4096 characters and never truncates a repository
  name or URL.
- No successful daily run uploads the Markdown report to Telegram.
- GitHub report commits, Issue publication, failure alerts, Secret handling,
  retry behavior, and degraded-generation exit propagation continue to work.
