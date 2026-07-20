# Task 8 Report: Idempotent Issue Publishing, Pipeline, and CLI

## Status

Complete. Task 8 composes the public interfaces delivered by Tasks 1–7 without changing their business logic or data models.

Base commit: `cb2c873`.

## TDD evidence

The relocated worktree retained an absolute virtualenv launcher path. The first requested `.venv/bin/pytest ...` invocation therefore stopped before collection with `bad interpreter: /private/tmp/codex-ai-agent-radar/.venv/bin/python3.14`. Subsequent pytest evidence uses the equivalent interpreter form `PYTHONPATH=src .venv/bin/python -m pytest ...`.

### RED

1. Publisher and parser modules:
   - Command: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_publish.py tests/test_cli.py -q`
   - Result: collection stopped with two expected `ModuleNotFoundError` errors for `ai_agent_radar.publish` and `ai_agent_radar.cli`.
2. Pipeline composition:
   - Command: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_pipeline.py -q`
   - Result: collection stopped with the expected `ModuleNotFoundError: No module named 'ai_agent_radar.pipeline'`.
3. CLI execution and exit codes:
   - Command: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_cli.py -q`
   - Result: `5 failed, 1 passed`; failures were the expected missing `main` and `run_pipeline` composition boundary.
4. Brief-compatible publisher fixture:
   - Command: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_publish.py::test_upsert_updates_matching_issue_instead_of_creating -q`
   - Result: `1 failed`; the publisher incorrectly used `POST` when the brief's labeled-Issue fixture omitted a title field.

### GREEN

1. Initial publisher/parser slice: `3 passed in 0.02s`.
2. Pipeline slice after correcting one test-only Markdown expectation: `5 passed in 0.06s`.
3. CLI slice: `6 passed in 0.09s`.
4. Publisher compatibility slice: `2 passed in 0.02s`.
5. Final focused verification:
   - Command: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_publish.py tests/test_pipeline.py tests/test_cli.py -q`
   - Result: `13 passed in 0.09s`.
   - Focused Ruff result: `All checks passed!`.

## Implementation

- `src/ai_agent_radar/publish.py`
  - Queries open Issues by the stable mode label.
  - Updates the matching Issue body with `PATCH`; creates with `POST` only when no match exists.
  - Uses GitHub's versioned JSON headers and returns the original Issue URL.
- `src/ai_agent_radar/pipeline.py`
  - Resolves absolute and repository-relative config paths.
  - Injects GitHub/news collectors, summarization, and optional Issue publishing.
  - Composes deduplication, quality gating, prior snapshots, timezone-aware trends, deterministic scoring/ranking, new/rising/useful/category/dropped bundles, and news windows.
  - Merges source state, atomically writes report and ranked snapshot files, and compacts daily snapshots older than the 90-day cutoff.
  - Uses `reports/daily/YYYY-MM-DD.md` and ISO-week `reports/weekly/YYYY-Www.md`; repeated same-date runs overwrite the same report and snapshot.
  - Returns the original output paths, original candidate count, rejected count, ranked count, source statuses, and optional Issue URL in `RunResult`.
  - Validates the publish dependency before collection or local writes.
- `src/ai_agent_radar/cli.py`
  - Defaults to dry-run and only wires `IssuePublisher` when `--publish` is explicit.
  - Accepts injected argv/environment for deterministic tests.
  - Requires `GITHUB_TOKEN`; publishing additionally requires `GITHUB_REPOSITORY`.
  - Uses configured timezone for an omitted report date.
  - Returns 0 when at least one source is healthy, 1 for total source failure or pipeline failure, and 2 for precondition/configuration errors.
  - Prints structured JSON and replaces unexpected exception details with a stable message so environment secrets cannot leak.

## Tests added

- `tests/test_publish.py`: update-vs-create behavior and Issue payload.
- `tests/test_pipeline.py`: absolute/relative config, output paths and counts, timezone news selection, same-date idempotence, ISO-week naming, trend/dropped bundles, publishing, source-state merge, 90-day compaction, and early publish validation.
- `tests/test_cli.py`: default dry-run, publish preconditions, exit 0/1/2, dependency wiring, RunResult output, and secret-safe failures.

## Full verification

Command: `PYTHONPATH=src .venv/bin/python -m pytest -q && .venv/bin/ruff check . && git diff --check`

- Pytest: `73 passed in 0.14s`.
- Ruff: `All checks passed!`.
- Diff whitespace check: exit 0, no findings.

## Self-review

- Correctness: Checked every Task 8 requirement against the code and focused tests. Same-date history excludes the current snapshot, paths remain stable, weekly filenames use `date.isocalendar()`, and news dates are converted through the configured timezone.
- Readability: Kept the pipeline as orchestration and extracted only a row alias, history loader, and report-item adapter.
- Architecture: Reused all established public modules; no compatibility edits to Tasks 1–7 were required.
- Security: Publishing is opt-in, publish prerequisites are explicit, API credentials remain in headers only, response bodies never include environment values, and unexpected failures are sanitized.
- Performance: Collection remains one pass, ranking is bounded by collected candidates, Issue lookup is capped at 100, and history reads only uncompacted daily JSON retained within the active window.

## Concerns

- No product-code concern remains.
- Local-only tooling note: moving the worktree invalidated the generated pytest launcher shebang. Verification used `.venv/bin/python -m pytest`; this does not affect committed files or the installed project entry point in a normally created environment.
