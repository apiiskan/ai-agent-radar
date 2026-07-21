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

---

## Follow-up Required-Findings Fix

### RED

- `.venv/bin/pytest tests/test_scoring.py -q` → `3 failed, 4 passed`: non-100 weights did not raise, negative
  trend deltas raised from `log1p`, and the fallback reason did not describe actual evidence.
- `.venv/bin/pytest tests/test_scoring.py -q` → `1 failed, 6 passed`: a new-release heat contribution had no
  corresponding heat reason.

### GREEN

- `.venv/bin/pytest tests/test_scoring.py -q` → `7 passed in 0.01s`
- `.venv/bin/ruff check src/ai_agent_radar/scoring.py tests/test_scoring.py` → `All checks passed!`
- `.venv/bin/pytest -q` → `36 passed in 0.06s`
- `git diff --check` → passed

### Files

- `src/ai_agent_radar/scoring.py`: validates 100-point weights, clamps logarithmic deltas, and emits truthful
  heat, utility, freshness, and relevance evidence.
- `tests/test_scoring.py`: covers invalid weights, all three negative deltas, no-evidence reasons, and release heat.

### Self-review

Each `log1p` input is clamped at zero; totals use only validated weights; every reason maps to observed data or a
truthful absence/base-score statement; and the prior relevance/quality-gate fallback is removed.

### Concerns

None.
