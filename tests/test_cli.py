import json
from datetime import date, datetime, timezone

import httpx
import pytest

import ai_agent_radar.cli as cli
from ai_agent_radar.cli import build_parser
from ai_agent_radar.config import ConfigurationError
from ai_agent_radar.github import GitHubCollection
from ai_agent_radar.models import RunResult, SourceStatus
from ai_agent_radar.news import NewsCollection


FIXED_CURRENT_TIME = datetime(2026, 7, 20, 1, tzinfo=timezone.utc)


def test_cli_defaults_to_dry_run() -> None:
    args = build_parser().parse_args(["daily", "--date", "2026-07-20"])

    assert args.publish is False
    assert args.mode == "daily"


def test_publish_subcommand_is_explicit_and_targets_an_existing_report() -> None:
    args = build_parser().parse_args(
        ["publish", "weekly", "--date", "2026-07-20"]
    )

    assert args.publish is True
    assert args.mode == "weekly"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/repos/anthropics/claude-code/releases",
        "https://API.GITHUB.COM:443/repos/xai-org/xai-sdk-python/releases",
    ],
)
def test_news_headers_authenticate_only_safe_github_api_origins(url) -> None:
    authenticated = cli._news_headers(url, "token-value")

    assert authenticated["Accept"] == "application/vnd.github+json"
    assert authenticated["Authorization"] == "Bearer token-value"
    assert authenticated["X-GitHub-Api-Version"] == "2022-11-28"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com/repos/anthropics/claude-code/releases",
        "https://api.github.com:8443/repos/anthropics/claude-code/releases",
        "https://api.github.com.evil.example/releases",
        "https://api.github.com:not-a-port/releases",
        "https://api.github.com:99999/releases",
        "https://[api.github.com/releases",
    ],
)
def test_news_headers_reject_unsafe_or_malformed_github_api_origins(url) -> None:
    assert cli._news_headers(url, "token-value") == {}


def test_news_headers_omit_authorization_without_token() -> None:
    headers = cli._news_headers(
        "https://api.github.com/repos/xai-org/xai-sdk-python/releases", None
    )

    assert headers["Accept"] == "application/vnd.github+json"
    assert "Authorization" not in headers


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


@pytest.mark.parametrize("requested", (date(2026, 7, 19), date(2026, 7, 21)))
def test_live_generation_rejects_non_current_dates_before_network_collection(
    requested, tmp_path, config_path, monkeypatch, capsys
) -> None:
    def unexpected_client(*args, **kwargs):
        raise AssertionError("network client must not be created")

    monkeypatch.setattr(cli.httpx, "Client", unexpected_client)

    exit_code = cli.main(
        [
            "daily",
            "--date",
            requested.isoformat(),
            "--root",
            str(tmp_path),
            "--config",
            str(config_path),
        ],
        {"GITHUB_TOKEN": "top-secret"},
        now=lambda: FIXED_CURRENT_TIME,
    )

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert "must equal current date" in payload["error"]
    assert "top-secret" not in json.dumps(payload)


def test_publish_reads_durable_report_without_running_collection(
    tmp_path, config_path, monkeypatch, capsys
) -> None:
    report_path = tmp_path / "reports/daily/2026-07-20.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# durable report\n", encoding="utf-8")
    calls: list[tuple[str, str, str]] = []

    class CapturingPublisher:
        def __init__(self, token, repository, client) -> None:
            assert token == "top-secret"
            assert repository == "o/r"

        def upsert(self, title, body, label) -> str:
            calls.append((title, body, label))
            return "https://github.com/o/r/issues/7"

    monkeypatch.setattr(cli, "IssuePublisher", CapturingPublisher)
    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda *args, **kwargs: pytest.fail("publishing must not collect or generate"),
    )

    exit_code = cli.main(
        [
            "publish",
            "daily",
            "--date",
            "2026-07-20",
            "--root",
            str(tmp_path),
            "--config",
            str(config_path),
        ],
        {"GITHUB_TOKEN": "top-secret", "GITHUB_REPOSITORY": "o/r"},
    )

    assert exit_code == 0
    assert calls == [
        (
            "AI Agent Radar 日报 · 2026-07-20",
            "# durable report\n",
            "radar-daily",
        )
    ]
    assert json.loads(capsys.readouterr().out)["issue_url"].endswith("/7")


def test_news_fetch_stops_streaming_immediately_after_five_megabytes() -> None:
    class CountingStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.yielded = 0

        def __iter__(self):
            for chunk in (b"a" * 4_000_000, b"b" * 1_100_001, b"secret-tail"):
                self.yielded += 1
                yield chunk

    stream = CountingStream()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream)
    )

    with httpx.Client(transport=transport) as client:
        with pytest.raises(ValueError, match="exceeds 5 MB") as error:
            cli._fetch_news(client, "https://example.com/feed", None)

    assert stream.yielded == 2
    assert "secret-tail" not in str(error.value)


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
        now=lambda: FIXED_CURRENT_TIME,
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


def test_empty_model_variables_fall_back_to_defaults(
    tmp_path, config_path, monkeypatch, capsys
) -> None:
    captured: dict[str, str] = {}

    class CapturingSummarizer:
        def __init__(self, api_key, base_url, model, client) -> None:
            captured.update(base_url=base_url, model=model)

        def summarize(self, repo, score):
            raise AssertionError("pipeline is stubbed")

    result = RunResult(
        report_path="report.md",
        snapshot_path="snapshot.json",
        issue_url=None,
        candidates=0,
        filtered=0,
        ranked=0,
        source_statuses=(SourceStatus(name="github:test", ok=True),),
    )
    monkeypatch.setattr(cli, "Summarizer", CapturingSummarizer)
    monkeypatch.setattr(cli, "run_pipeline", lambda *args, **kwargs: result)

    exit_code = cli.main(
        ["daily", "--root", str(tmp_path), "--config", str(config_path)],
        {
            "GITHUB_TOKEN": "token-value",
            "MODEL_BASE_URL": "",
            "MODEL_NAME": "",
        },
    )

    assert exit_code == 0
    assert captured == {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5-mini",
    }
    assert "token-value" not in capsys.readouterr().out


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
        now=lambda: FIXED_CURRENT_TIME,
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert json.loads(output) == {"ok": False, "error": "pipeline failed"}
    assert "top-secret" not in output


@pytest.mark.parametrize(
    "config_text",
    [
        "timezone: [",
        """timezone: Mars/Olympus
queries: {general: [agent]}
feeds: []
weights: {heat: 45, utility: 25, freshness: 20, relevance: 10}
limits: {search_per_query: 20, daily_top: 10, weekly_top: 20}
exclusions: {repositories: [], keywords: []}
""",
    ],
)
def test_cli_returns_two_for_real_invalid_configuration(
    tmp_path, config_text, capsys
) -> None:
    config_path = tmp_path / "radar.yaml"
    config_path.write_text(config_text, encoding="utf-8")

    exit_code = cli.main(
        ["daily", "--date", "2026-07-20", "--config", str(config_path)],
        {"GITHUB_TOKEN": "top-secret"},
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert json.loads(output)["ok"] is False
    assert "top-secret" not in output


def test_cli_returns_two_when_pipeline_configuration_load_fails(
    tmp_path, config_path, monkeypatch, capsys
) -> None:
    def fail_pipeline(*args, **kwargs):
        raise ConfigurationError("invalid radar configuration")

    monkeypatch.setattr(cli, "run_pipeline", fail_pipeline)

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

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


@pytest.mark.parametrize("config_target", ("missing.yaml", "."))
def test_cli_returns_two_for_missing_or_unreadable_config(
    tmp_path, config_target, capsys
) -> None:
    exit_code = cli.main(
        ["daily", "--date", "2026-07-20", "--config", str(tmp_path / config_target)],
        {"GITHUB_TOKEN": "top-secret"},
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_real_cli_returns_two_for_invalid_utf8_config(tmp_path, capsys) -> None:
    config_path = tmp_path / "radar.yaml"
    config_path.write_bytes(b"timezone: Asia/Shanghai\n\xff")

    exit_code = cli.main(
        ["daily", "--date", "2026-07-20", "--config", str(config_path)],
        {"GITHUB_TOKEN": "top-secret"},
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert json.loads(output)["ok"] is False
    assert "top-secret" not in output


def test_real_cli_pipeline_writes_degraded_report_when_all_sources_fail(
    tmp_path, config_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        cli.GitHubClient,
        "collect",
        lambda self, config: GitHubCollection(
            repositories=(),
            statuses=(SourceStatus(name="github:test", ok=False, error="Timeout"),),
            rate_remaining=None,
        ),
    )
    monkeypatch.setattr(
        cli,
        "collect_news",
        lambda feeds, fetch: NewsCollection(
            items=(),
            statuses=(SourceStatus(name="feed:test", ok=False, error="ParseError"),),
        ),
    )

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
        now=lambda: FIXED_CURRENT_TIME,
    )

    payload = json.loads(capsys.readouterr().out)
    report_path = tmp_path / "reports/daily/2026-07-20.md"
    report = report_path.read_text(encoding="utf-8")
    assert exit_code == 1
    assert payload["report_path"] == str(report_path)
    assert payload["ranked"] == 0
    assert "⚠️ github:test: Timeout" in report
    assert "⚠️ feed:test: ParseError" in report
    assert (tmp_path / "data/state/sources.json").exists()


def test_real_cli_pipeline_exits_zero_when_one_source_succeeds(
    tmp_path, config_path, repo_factory, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        cli.GitHubClient,
        "collect",
        lambda self, config: GitHubCollection(
            repositories=(repo_factory(has_skill_md=True),),
            statuses=(SourceStatus(name="github:test", ok=True, item_count=1),),
            rate_remaining=100,
        ),
    )
    monkeypatch.setattr(
        cli,
        "collect_news",
        lambda feeds, fetch: NewsCollection(
            items=(),
            statuses=(SourceStatus(name="feed:test", ok=False, error="Timeout"),),
        ),
    )

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
        now=lambda: FIXED_CURRENT_TIME,
    )

    payload = json.loads(capsys.readouterr().out)
    report = (tmp_path / "reports/daily/2026-07-20.md").read_text(encoding="utf-8")
    assert exit_code == 0
    assert payload["ranked"] == 1
    assert "✅ github:test: 1" in report
    assert "⚠️ feed:test: Timeout" in report
