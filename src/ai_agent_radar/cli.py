from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .config import load_config
from .github import GitHubClient
from .news import collect_news
from .pipeline import PipelineDependencies, run_pipeline
from .publish import IssuePublisher
from .summarize import Summarizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-agent-radar")
    parser.add_argument("mode", choices=("daily", "weekly"))
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config/radar.yaml"))
    parser.add_argument("--publish", action="store_true", help="create or update the GitHub Issue")
    return parser


def _print_error(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    environment = os.environ if environ is None else environ
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
    except (OSError, ValueError) as exc:
        _print_error(str(exc))
        return 2
    day = args.date or datetime.now(ZoneInfo(config.timezone)).date()

    try:
        with httpx.Client(follow_redirects=True) as client:
            github = GitHubClient(token, client)
            summarizer = Summarizer(
                api_key=environment.get("MODEL_API_KEY"),
                base_url=environment.get("MODEL_BASE_URL", "https://api.openai.com/v1"),
                model=environment.get("MODEL_NAME", "gpt-5-mini"),
                client=client,
            )
            issue_publisher = (
                IssuePublisher(token, repository or "", client) if args.publish else None
            )

            def fetch(url: str) -> bytes:
                response = client.get(url, timeout=20)
                response.raise_for_status()
                if len(response.content) > 5_000_000:
                    raise ValueError("news source response exceeds 5 MB")
                return response.content

            dependencies = PipelineDependencies(
                collect_github=github.collect,
                collect_news=lambda feeds: collect_news(feeds, fetch),
                summarize=summarizer.summarize,
                publish_issue=issue_publisher.upsert if issue_publisher else None,
            )
            result = run_pipeline(
                args.mode,
                day,
                root,
                config_path,
                dependencies,
                publish=args.publish,
            )
    except Exception:
        _print_error("pipeline failed")
        return 1

    print(result.model_dump_json())
    return 0 if any(status.ok for status in result.source_statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
