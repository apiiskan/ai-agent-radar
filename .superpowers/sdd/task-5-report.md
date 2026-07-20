# Task 5 Report: Deterministic, Explainable Scoring

## RED

1. `.venv/bin/pytest tests/test_scoring.py -q` failed during collection with
   `ModuleNotFoundError: No module named 'ai_agent_radar.scoring'`.
2. Added a deterministic casefolded-name tie test; it failed with ranking `[2, 1]` rather than `[1, 2]`.
3. Added a non-normalized-weight bound test; it failed because `ScoreBreakdown.total` received `262.37`.

## GREEN

- `.venv/bin/pytest tests/test_scoring.py -q`: 5 passed
- `.venv/bin/pytest -q`: 34 passed
- `.venv/bin/ruff check src/ai_agent_radar/scoring.py tests/test_scoring.py`: All checks passed
- `git diff --check`: passed

## Files

- `src/ai_agent_radar/scoring.py`: bounded deterministic scoring, reasons, and ranking.
- `tests/test_scoring.py`: weight/reason, zero-star project, deterministic ranking, and score-bound coverage.

## Self-review

Verified all sub-scores and totals are bounded, configured weights drive the total, no model output is consumed,
and ranking resolves every score/star/name tie using repository ID.

## Concerns

None.
