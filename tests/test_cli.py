import json
from datetime import date

import pytest

import ai_agent_radar.cli as cli
from ai_agent_radar.cli import build_parser
from ai_agent_radar.models import RunResult, SourceStatus


def test_cli_defaults_to_dry_run() -> None:
    args = build_parser().parse_args(["daily", "--date", "2026-07-20"])

    assert args.publish is False
    assert args.mode == "daily"


@pytest.mark.parametrize(
    ("arguments", "environment", "message"),
    [
        (["daily", "--date", "2026-07-20"], {}, "GITHUB_TOKEN is required"),
        (
            ["daily", "--date", "2026-07-20", "--publish"],
            {"GITHUB_TOKEN": "top-secret"},
            "--publish requires GITHUB_REPOSITORY",
        ),
    ],
)
def test_cli_returns_two_for_missing_publish_preconditions_without_leaking_secrets(
    arguments, environment, message, capsys
) -> None:
    exit_code = cli.main(arguments, environment)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {"ok": False, "error": message}
    assert "top-secret" not in json.dumps(payload)


@pytest.mark.parametrize(("healthy", "expected_exit"), [(True, 0), (False, 1)])
def test_cli_reports_result_and_uses_source_health_for_exit_code(
    healthy, expected_exit, tmp_path, config_path, monkeypatch, capsys
) -> None:
    calls = []
    result = RunResult(
        report_path="reports/daily/2026-07-20.md",
        snapshot_path="data/snapshots/2026-07-20.json",
        issue_url=None,
        candidates=3,
        filtered=1,
        ranked=2,
        source_statuses=(
            SourceStatus(name="github:test", ok=healthy, error=None if healthy else "Timeout"),
        ),
    )

    def fake_run_pipeline(mode, day, root, selected_config, dependencies, publish=False):
        calls.append((mode, day, root, selected_config, dependencies, publish))
        return result

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    exit_code = cli.main(
        [
            "daily",
            "--date",
            "2026-07-20",
            "--root",
            str(tmp_path),
            "--config",
            str(config_path),
        ],
        {"GITHUB_TOKEN": "top-secret"},
    )

    output = capsys.readouterr().out
    assert exit_code == expected_exit
    assert json.loads(output)["ranked"] == 2
    assert "top-secret" not in output
    assert calls[0][0:4] == (
        "daily",
        date(2026, 7, 20),
        tmp_path.resolve(),
        config_path,
    )
    assert calls[0][5] is False
    assert calls[0][4].publish_issue is None


def test_cli_returns_one_for_pipeline_failure_without_echoing_exception_secret(
    tmp_path, config_path, monkeypatch, capsys
) -> None:
    def fail_pipeline(*args, **kwargs):
        raise RuntimeError("upstream included top-secret")

    monkeypatch.setattr(cli, "run_pipeline", fail_pipeline)

    exit_code = cli.main(
        [
            "weekly",
            "--date",
            "2026-07-20",
            "--root",
            str(tmp_path),
            "--config",
            str(config_path),
        ],
        {"GITHUB_TOKEN": "top-secret"},
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert json.loads(output) == {"ok": False, "error": "pipeline failed"}
    assert "top-secret" not in output
