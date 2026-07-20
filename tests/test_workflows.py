from pathlib import Path

import yaml


def load_workflow(name: str) -> dict:
    return yaml.safe_load(
        Path(f".github/workflows/{name}.yml").read_text(encoding="utf-8")
    )


def workflow_runs(workflow: dict) -> list[str]:
    return [step["run"] for step in workflow["jobs"]["radar"]["steps"] if "run" in step]


def test_daily_workflow_has_schedule_dispatch_permissions_and_concurrency() -> None:
    workflow = load_workflow("daily")
    assert workflow["permissions"] == {"contents": "write", "issues": "write"}
    assert "schedule" in workflow[True]  # PyYAML parses the key `on` as boolean True.
    assert "workflow_dispatch" in workflow[True]
    assert workflow[True]["schedule"][0]["cron"] == "0 0 * * *"
    assert workflow["concurrency"]["cancel-in-progress"] is True


def test_weekly_workflow_uses_monday_0030_utc() -> None:
    workflow = load_workflow("weekly")
    assert workflow[True]["schedule"][0]["cron"] == "30 0 * * 1"


def test_daily_and_weekly_workflows_cover_install_publish_and_scoped_commit() -> None:
    for name in ("daily", "weekly"):
        workflow = load_workflow(name)
        runs = workflow_runs(workflow)

        assert workflow["permissions"] == {"contents": "write", "issues": "write"}
        assert "workflow_dispatch" in workflow[True]
        assert workflow["concurrency"]["cancel-in-progress"] is True
        assert "pip install ." in runs
        assert f"ai-agent-radar {name} --publish" in runs
        commit_script = next(run for run in runs if "git commit" in run)
        assert "git add data reports" in commit_script
        assert "git add ." not in commit_script


def test_daily_and_weekly_use_distinct_mode_specific_concurrency_groups() -> None:
    daily_group = load_workflow("daily")["concurrency"]["group"]
    weekly_group = load_workflow("weekly")["concurrency"]["group"]

    assert daily_group == "ai-agent-radar-daily"
    assert weekly_group == "ai-agent-radar-weekly"
    assert daily_group != weekly_group
