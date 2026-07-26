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


def test_daily_manual_telegram_test_is_isolated_from_report_generation() -> None:
    workflow = load_workflow("daily")
    dispatch = workflow[True]["workflow_dispatch"]
    telegram_input = dispatch["inputs"]["telegram_test"]
    telegram_job = workflow["jobs"]["telegram-test"]
    radar_job = workflow["jobs"]["radar"]

    assert telegram_input["type"] == "boolean"
    assert telegram_input["default"] is False
    assert telegram_job["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.telegram_test"
    )
    assert radar_job["if"] == (
        "github.event_name != 'workflow_dispatch' || !inputs.telegram_test"
    )
    command_step = next(
        step
        for step in telegram_job["steps"]
        if step.get("run") == "ai-agent-radar telegram-test"
    )
    assert command_step["env"] == {
        "TELEGRAM_BOT_TOKEN": "${{ secrets.TELEGRAM_BOT_TOKEN }}"
    }


def test_weekly_workflow_uses_monday_0030_utc() -> None:
    workflow = load_workflow("weekly")
    assert workflow[True]["schedule"][0]["cron"] == "30 0 * * 1"


def test_daily_and_weekly_workflows_generate_push_then_publish_existing_report() -> None:
    for name in ("daily", "weekly"):
        workflow = load_workflow(name)
        runs = workflow_runs(workflow)

        assert workflow["permissions"] == {"contents": "write", "issues": "write"}
        assert "workflow_dispatch" in workflow[True]
        assert workflow["concurrency"]["cancel-in-progress"] is True
        assert "pip install ." in runs
        generate_index = next(
            index for index, run in enumerate(runs) if f"ai-agent-radar {name}" in run
        )
        commit_script = next(run for run in runs if "git commit" in run)
        commit_index = runs.index(commit_script)
        publish_index = next(
            index
            for index, run in enumerate(runs)
            if f"ai-agent-radar publish {name}" in run
        )
        assert "--publish" not in runs[generate_index]
        assert "git add data reports" in commit_script
        assert "git add ." not in commit_script
        assert "git push" in commit_script
        assert generate_index < commit_index < publish_index


def test_degraded_generation_is_committed_before_workflow_returns_nonzero() -> None:
    for name in ("daily", "weekly"):
        workflow = load_workflow(name)
        steps = workflow["jobs"]["radar"]["steps"]
        generate = next(step for step in steps if step.get("id") == "generate")
        publish = next(step for step in steps if step.get("name") == "Publish durable report")
        finish = next(step for step in steps if step.get("name") == "Propagate generation status")

        assert "GITHUB_OUTPUT" in generate["run"]
        assert publish["if"] == "steps.generate.outputs.exit_code == '0'"
        assert steps.index(publish) < steps.index(finish)
        assert "exit " in finish["run"]


def test_daily_workflow_delivers_report_then_alerts_on_failure() -> None:
    workflow = load_workflow("daily")
    steps = workflow["jobs"]["radar"]["steps"]
    publish = next(step for step in steps if step.get("name") == "Publish durable report")
    notify_daily = next(
        step for step in steps if step.get("name") == "Send Telegram growth Top 10"
    )
    notify_failure = next(
        step for step in steps if step.get("name") == "Send Telegram failure alert"
    )
    finish = next(step for step in steps if step.get("name") == "Propagate generation status")

    assert steps.index(publish) < steps.index(notify_daily)
    assert steps.index(notify_daily) < steps.index(notify_failure)
    assert steps.index(notify_failure) < steps.index(finish)
    assert notify_daily["if"] == (
        "steps.generate.outputs.exit_code == '0' && success()"
    )
    assert notify_daily["run"] == "ai-agent-radar notify daily"
    assert notify_failure["if"] == (
        "always() && "
        "(steps.generate.outputs.exit_code != '0' || failure())"
    )
    assert "ai-agent-radar notify failure" in notify_failure["run"]
    assert "GENERATION_EXIT_CODE" in notify_failure["run"]
    assert finish["if"] == (
        "always() && steps.generate.outputs.exit_code != '0'"
    )


def test_daily_telegram_steps_receive_only_telegram_secrets() -> None:
    steps = load_workflow("daily")["jobs"]["radar"]["steps"]
    expected_environment = {
        "TELEGRAM_BOT_TOKEN": "${{ secrets.TELEGRAM_BOT_TOKEN }}",
        "TELEGRAM_CHAT_ID": "${{ secrets.TELEGRAM_CHAT_ID }}",
    }
    for name in ("Send Telegram growth Top 10", "Send Telegram failure alert"):
        step = next(step for step in steps if step.get("name") == name)
        assert step["env"]["TELEGRAM_BOT_TOKEN"] == expected_environment[
            "TELEGRAM_BOT_TOKEN"
        ]
        assert step["env"]["TELEGRAM_CHAT_ID"] == expected_environment[
            "TELEGRAM_CHAT_ID"
        ]
        assert "GITHUB_TOKEN" not in step["env"]


def test_daily_and_weekly_use_distinct_mode_specific_concurrency_groups() -> None:
    daily_group = load_workflow("daily")["concurrency"]["group"]
    weekly_group = load_workflow("weekly")["concurrency"]["group"]

    assert daily_group == "ai-agent-radar-daily"
    assert weekly_group == "ai-agent-radar-weekly"
    assert daily_group != weekly_group
