import json
from datetime import datetime, timezone

from ai_agent_radar.models import SourceStatus
from ai_agent_radar.state import merge_source_state


def test_source_state_preserves_last_success_and_counts_failures(tmp_path) -> None:
    path = tmp_path / "sources.json"
    success_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
    merge_source_state(path, (SourceStatus(name="feed:xAI", ok=True),), success_at)

    statuses = merge_source_state(
        path,
        (SourceStatus(name="feed:xAI", ok=False, error="TimeoutError"),),
        datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    assert statuses[0].last_success_at == success_at
    assert statuses[0].consecutive_failures == 1


def test_source_state_resets_failures_and_persists_to_disk(tmp_path) -> None:
    path = tmp_path / "sources.json"
    first_failure = datetime(2026, 7, 19, tzinfo=timezone.utc)
    merge_source_state(path, (SourceStatus(name="github", ok=False),), first_failure)
    success_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    statuses = merge_source_state(path, (SourceStatus(name="github", ok=True),), success_at)

    assert statuses == (
        SourceStatus(name="github", ok=True, last_success_at=success_at, consecutive_failures=0),
    )
    stored = SourceStatus.model_validate(json.loads(path.read_text(encoding="utf-8"))[0])
    assert stored == statuses[0]
