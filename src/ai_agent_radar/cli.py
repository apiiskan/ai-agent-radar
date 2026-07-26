from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from .config import ConfigurationError, load_config
from .github import GitHubClient
from .news import collect_news
from .notifications import (
    NotificationError,
    send_daily_notification,
    send_failure_notification,
)
from .pipeline import PipelineDependencies, run_pipeline
from .publish import IssuePublisher
from .summarize import Summarizer
from .telegram import TelegramError, TelegramPublisher, mask_chat_id

DEFAULT_MODEL_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL_NAME = "gpt-5-mini"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-agent-radar")
    commands = parser.add_subparsers(dest="command", required=True)
    for mode in ("daily", "weekly"):
        command = commands.add_parser(mode)
        _add_common_arguments(command)
        command.add_argument(
            "--publish",
            action="store_true",
            help="publish an already-generated report (legacy alias)",
        )
        command.set_defaults(mode=mode)
    publish = commands.add_parser("publish")
    publish.add_argument("mode", choices=("daily", "weekly"))
    _add_common_arguments(publish)
    publish.set_defaults(publish=True)
    commands.add_parser("telegram-test")
    notify = commands.add_parser("notify")
    notification_kinds = notify.add_subparsers(dest="notify_kind", required=True)
    notify_daily = notification_kinds.add_parser("daily")
    _add_common_arguments(notify_daily)
    notify_failure = notification_kinds.add_parser("failure")
    notify_failure.add_argument("--generation-exit-code", type=int)
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config/radar.yaml"))


def _print_error(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))


def _news_headers(url: str, token: str | None) -> dict[str, str]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return {}
    if (
        parts.scheme.casefold() != "https"
        or parts.hostname != "api.github.com"
        or port not in {None, 443}
    ):
        return {}
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    if args.command == "telegram-test":
        return _run_telegram_test(environment)
    if args.command == "notify":
        return _run_notification(args, environment, now)
    token = environment.get("GITHUB_TOKEN")
    repository = environment.get("GITHUB_REPOSITORY")
    if args.publish and not repository:
        _print_error("--publish requires GITHUB_REPOSITORY")
        return 2
    if not token:
        _print_error("GITHUB_TOKEN is required")
        return 2

    root = args.root.resolve()
    config_path = args.config if args.config.is_absolute() else root / args.config
    try:
        config = load_config(config_path)
    except ConfigurationError as exc:
        _print_error(str(exc))
        return 2
    zone = ZoneInfo(config.timezone)
    current_time = now() if now is not None else datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_day = current_time.astimezone(zone).date()
    day = args.date or current_day
    publishing_existing = args.command == "publish" or args.publish
    if not publishing_existing and day != current_day:
        _print_error(
            f"live collection date must equal current date in {config.timezone}: {current_day}"
        )
        return 2

    try:
        with httpx.Client(follow_redirects=True) as client:
            if publishing_existing:
                assert repository is not None
                result = _publish_existing_report(
                    args.mode,
                    day,
                    root,
                    IssuePublisher(token, repository, client),
                )
                print(json.dumps(result, ensure_ascii=False))
                return 0
            github = GitHubClient(token, client)
            summarizer = Summarizer(
                api_key=environment.get("MODEL_API_KEY"),
                base_url=environment.get("MODEL_BASE_URL") or DEFAULT_MODEL_BASE_URL,
                model=environment.get("MODEL_NAME") or DEFAULT_MODEL_NAME,
                client=client,
            )
            def fetch(url: str) -> bytes:
                return _fetch_news(client, url, token)

            dependencies = PipelineDependencies(
                collect_github=github.collect,
                collect_news=lambda feeds: collect_news(feeds, fetch),
                summarize=summarizer.summarize,
            )
            result = run_pipeline(
                args.mode,
                day,
                root,
                config_path,
                dependencies,
            )
    except ConfigurationError as exc:
        _print_error(str(exc))
        return 2
    except Exception:
        _print_error("pipeline failed")
        return 1

    print(result.model_dump_json())
    return (
        0
        if result.github_discovery_complete
        and any(status.ok for status in result.source_statuses)
        else 1
    )


def _run_telegram_test(environment: Mapping[str, str]) -> int:
    token = environment.get("TELEGRAM_BOT_TOKEN")
    if not token:
        _print_error("TELEGRAM_BOT_TOKEN is required")
        return 2
    try:
        with httpx.Client(follow_redirects=False) as client:
            publisher = TelegramPublisher(token, None, client)
            chat_id = publisher.discover_private_start_chat()
            message_id = publisher.send_bootstrap_test(chat_id)
    except TelegramError as exc:
        _print_error(str(exc))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "kind": "telegram-test",
                "message_id": message_id,
                "chat_id": mask_chat_id(chat_id),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _run_notification(
    args: argparse.Namespace,
    environment: Mapping[str, str],
    now: Callable[[], datetime] | None,
) -> int:
    required = (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "GITHUB_REPOSITORY",
    )
    for name in required:
        if not environment.get(name):
            _print_error(f"{name} is required")
            return 2
    token = environment["TELEGRAM_BOT_TOKEN"]
    chat_id = environment["TELEGRAM_CHAT_ID"]
    repository = environment["GITHUB_REPOSITORY"]
    try:
        with httpx.Client(follow_redirects=False) as client:
            publisher = TelegramPublisher(token, chat_id, client)
            if args.notify_kind == "failure":
                result = send_failure_notification(
                    environment,
                    args.generation_exit_code,
                    publisher,
                )
            else:
                root = args.root.resolve()
                config_path = (
                    args.config if args.config.is_absolute() else root / args.config
                )
                config = load_config(config_path)
                current_time = now() if now is not None else datetime.now(timezone.utc)
                if current_time.tzinfo is None:
                    current_time = current_time.replace(tzinfo=timezone.utc)
                day = args.date or current_time.astimezone(
                    ZoneInfo(config.timezone)
                ).date()
                result = send_daily_notification(day, root, repository, publisher)
    except (ConfigurationError, NotificationError) as exc:
        _print_error(str(exc))
        return 2
    except TelegramError as exc:
        _print_error(str(exc))
        return 1
    except Exception:
        _print_error("notification failed")
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _fetch_news(
    client: httpx.Client,
    url: str,
    token: str | None,
    max_bytes: int = 5_000_000,
) -> bytes:
    chunks = bytearray()
    with client.stream(
        "GET",
        url,
        headers=_news_headers(url, token),
        timeout=20,
    ) as response:
        response.raise_for_status()
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_bytes:
            raise ValueError("news source response exceeds 5 MB")
        for chunk in response.iter_bytes():
            if len(chunks) + len(chunk) > max_bytes:
                raise ValueError("news source response exceeds 5 MB")
            chunks.extend(chunk)
    return bytes(chunks)


def _publish_existing_report(
    mode: str,
    day: date,
    root: Path,
    publisher: IssuePublisher,
) -> dict[str, str]:
    iso_year, iso_week, _ = day.isocalendar()
    period = day.isoformat() if mode == "daily" else f"{iso_year}-W{iso_week:02d}"
    report_path = root / "reports" / mode / f"{period}.md"
    markdown = report_path.read_text(encoding="utf-8")
    title = f"AI Agent Radar {'日报' if mode == 'daily' else '周榜'} · {period}"
    label = "radar-daily" if mode == "daily" else "radar-weekly"
    relative_report = report_path.relative_to(root)
    issue_body = (
        markdown
        if len(markdown) <= 60_000
        else markdown[:58_000] + f"\n\n完整报告：`{relative_report}`"
    )
    issue_url = publisher.upsert(title, issue_body, label)
    return {"report_path": str(report_path), "issue_url": issue_url}


if __name__ == "__main__":
    raise SystemExit(main())
