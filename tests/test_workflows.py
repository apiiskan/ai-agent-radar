from pathlib import Path

import yaml


def load_workflow(name: str) -> dict:
    return yaml.safe_load(
        Path(f".github/workflows/{name}.yml").read_text(encoding="utf-8")
    )


def test_daily_workflow_has_schedule_dispatch_permissions_and_concurrency() -> None:
    workflow = load_workflow("daily")
    assert workflow["permissions"] == {"contents": "write", "issues": "write"}
    assert "schedule" in workflow[True]  # PyYAML parses the key `on` as boolean True.
    assert "workflow_dispatch" in workflow[True]
    assert workflow["concurrency"]["cancel-in-progress"] is True


def test_weekly_workflow_uses_monday_0030_utc() -> None:
    workflow = load_workflow("weekly")
    assert workflow[True]["schedule"][0]["cron"] == "30 0 * * 1"
