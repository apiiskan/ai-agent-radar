# Telegram Chinese Feature Introductions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a concise Chinese feature introduction to every Telegram growth Top 10 entry, using the existing model summary when available and deterministic Chinese metadata templates otherwise.

**Architecture:** Make report generation guarantee a Chinese `ProjectSummary.one_line` in its existing fallback path, so Telegram never performs a second model call. Extend the growth-section parser to recover and unescape that report summary, then include a bounded introduction line in the existing single-message renderer.

**Tech Stack:** Python 3.11+, existing `Summarizer`, standard-library `re`, `pytest`, Ruff, GitHub Actions.

## Global Constraints

- Telegram must reuse `ProjectSummary.one_line` from the durable report and make no model request.
- Each feature introduction is Chinese and at most 50 characters before Telegram rendering.
- Model absence, HTTP failure, or invalid output must use a deterministic Chinese metadata template.
- The fallback must never expose the original English description.
- Template priority is Chinese description, Skill plus MCP, Skill, MCP, security, observability, orchestration, automation, then generic Agent tooling.
- Telegram remains one plain-text message with at most ten projects and at most 4096 characters.
- Repository names and URLs must never be truncated.
- Existing scoring, report ordering, Secrets, failure alerts, and GitHub publication remain unchanged.

---

### Task 1: Deterministic Chinese summarizer fallback

**Files:**
- Modify: `src/ai_agent_radar/summarize.py`
- Modify: `tests/test_summarize.py`

**Interfaces:**
- Produces: `_fallback_one_line(repo: RepoRecord) -> str`
- Changes: `Summarizer._fallback(...)` always returns a Chinese `one_line` no longer than 50 characters.

- [ ] **Step 1: Write failing fallback tests**

Add a parameterized test covering each priority:

```python
@pytest.mark.parametrize(
    ("repo_overrides", "expected"),
    [
        ({"description": "用于保护 Agent 工具调用安全。"}, "用于保护 Agent 工具调用安全。"),
        ({"description": "English", "has_skill_md": True, "has_mcp": True},
         "提供 Agent Skill 与 MCP 集成，用于扩展智能体工作流。"),
        ({"description": "English", "has_skill_md": True},
         "提供可复用的 Agent Skill，用于扩展 AI 助手能力。"),
        ({"description": "English", "has_mcp": True},
         "提供 MCP 集成，让 AI Agent 能连接和调用外部工具。"),
        ({"description": "English", "topics": ("agent-security",)},
         "用于检测和防护 AI Agent 的安全风险与危险调用。"),
        ({"description": "English", "topics": ("observability",)},
         "用于监控和分析 AI Agent 的运行状态与调用链路。"),
        ({"description": "English", "topics": ("multi-agent", "orchestration")},
         "用于构建和编排 AI Agent 及多智能体工作流。"),
        ({"description": "English", "topics": ("automation",)},
         "为 AI Agent 提供开发工具与自动化能力。"),
        ({"description": "English", "topics": ()},
         "面向 AI Agent 场景的开源工具，提供相关开发能力。"),
    ],
)
def test_fallback_one_line_is_deterministic_chinese(
    repo_factory, score_factory, repo_overrides, expected
):
    result = Summarizer(api_key=None).summarize(
        repo_factory(**repo_overrides), score_factory()
    )
    assert result.one_line == expected
    assert len(result.one_line) <= 50
```

Add tests proving a long Chinese description is capped at 50 characters with
`…`, and HTTP/model-schema failures use the same Chinese fallback.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_summarize.py -q
```

Expected: failures because fallback currently returns the original English description.

- [ ] **Step 3: Implement the metadata-priority helper**

Add:

```python
MAX_FALLBACK_ONE_LINE_LENGTH = 50
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _bounded_intro(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= MAX_FALLBACK_ONE_LINE_LENGTH:
        return normalized
    return normalized[: MAX_FALLBACK_ONE_LINE_LENGTH - 1] + "…"
```

Build a case-folded evidence string from `full_name`, `description`, `topics`,
and the bounded README prefix. Select the exact templates from Step 1 in the
specified priority and call the helper from `Summarizer._fallback`.

- [ ] **Step 4: Verify and commit Task 1**

Run:

```bash
.venv/bin/python -m pytest tests/test_summarize.py -q
.venv/bin/ruff check src/ai_agent_radar/summarize.py tests/test_summarize.py
git add src/ai_agent_radar/summarize.py tests/test_summarize.py
git commit -m "feat: add Chinese summary fallback"
```

Expected: all summarizer tests pass and Ruff reports no errors.

### Task 2: Parse, unescape, and render feature introductions

**Files:**
- Modify: `src/ai_agent_radar/notifications.py`
- Modify: `tests/test_notifications.py`

**Interfaces:**
- Changes: `GrowthEntry` gains `introduction: str`.
- Produces: `_plain_introduction(markdown_text: str, limit: int = 50) -> str`.
- Changes: `render_growth_message(...)` adds `功能：<introduction>` for every entry.

- [ ] **Step 1: Write failing parser and renderer tests**

Update the real-shaped fixture to include escaped summaries and assert:

```python
assert entries[0].introduction == "用于编排 Agent（支持 MCP）。"
assert "功能：用于编排 Agent（支持 MCP）。" in message
```

Add a 70-character introduction case requiring a 49-character prefix plus
`…`, and an overflow case proving the renderer reduces introductions to 30
characters before dropping an entire project. Update all direct `GrowthEntry`
constructions with the new field.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_notifications.py -q
```

Expected: failures because `GrowthEntry` has no `introduction` and the parser ignores text after `—`.

- [ ] **Step 3: Implement introduction parsing and rendering**

Extend `_ENTRY_RE` with:

```python
r"(?:\s+—\s+(?P<introduction>.+))?$"
```

Reject entries without a non-empty introduction. Remove report Markdown
escapes using:

```python
re.sub(r"\\([\\`*_{}\[\]<>()#+.!|])", r"\1", text)
```

Normalize whitespace, cap the parsed introduction at 50 characters, render it
between repository and metrics, and add a 30-character compact-introduction
mode to the existing overflow pass.

- [ ] **Step 4: Verify and commit Task 2**

Run:

```bash
.venv/bin/python -m pytest tests/test_notifications.py tests/test_cli.py -q
.venv/bin/ruff check src/ai_agent_radar/notifications.py tests/test_notifications.py
git add src/ai_agent_radar/notifications.py tests/test_notifications.py
git commit -m "feat: show Chinese project introductions"
```

Expected: all notification and CLI tests pass and Ruff reports no errors.

### Task 3: Documentation, full regression, and live Telegram verification

**Files:**
- Modify: `README.md`
- Modify: `tests/test_configure_telegram.py`

**Interfaces:**
- Preserves: existing `ai-agent-radar notify daily` workflow command and Secrets.

- [ ] **Step 1: Write failing README contract test**

Require README to state `中文功能介绍`, `模型摘要优先`, `中文规则兜底`, and
`Telegram 阶段不会再次调用模型`.

- [ ] **Step 2: Run the README test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_configure_telegram.py -q
```

Expected: failure because README does not document the introduction behavior.

- [ ] **Step 3: Document the behavior**

Add one concise paragraph to `Telegram 日报` explaining the four exact phrases
from Step 1 and that no additional model Secret or Telegram-stage model call is
introduced.

- [ ] **Step 4: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
git diff --check
```

Expected: all tests pass, Ruff reports no errors, and `git diff --check` is silent.

- [ ] **Step 5: Commit, synchronize, and push**

Run:

```bash
git add README.md tests/test_configure_telegram.py
git commit -m "docs: explain Chinese Telegram introductions"
git fetch origin codex/telegram-daily-delivery
git rebase origin/codex/telegram-daily-delivery
git push origin codex/telegram-daily-delivery
```

Expected: the feature branch contains any Actions-generated report commit plus all introduction changes without force-pushing.

- [ ] **Step 6: Trigger and verify live delivery**

Run:

```bash
gh workflow run daily.yml -R apiiskan/ai-agent-radar \
  --ref codex/telegram-daily-delivery -f telegram_test=false
gh run watch RUN_ID -R apiiskan/ai-agent-radar --exit-status
```

Expected: the workflow succeeds, `Send Telegram growth Top 10` succeeds,
Telegram receives one Top 10 text message where every project has a `功能：`
Chinese introduction, and no attachment is sent.
