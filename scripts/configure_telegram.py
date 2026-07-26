from __future__ import annotations

import argparse
import getpass
import shutil
import subprocess
from collections.abc import Callable, Sequence

import httpx

from ai_agent_radar.telegram import TelegramError, TelegramPublisher, mask_chat_id

DEFAULT_REPOSITORY = "apiiskan/ai-agent-radar"


class ConfigurationError(RuntimeError):
    """A safe configuration failure that never includes credentials."""


def configure(
    repository: str,
    *,
    prompt_token: Callable[[str], str] = getpass.getpass,
    publisher_factory=TelegramPublisher,
    client_factory=httpx.Client,
    find_executable: Callable[[str], str | None] = shutil.which,
    run_command: Callable[..., object] = subprocess.run,
    write_output: Callable[[str], None] = print,
) -> int:
    if not find_executable("gh"):
        raise ConfigurationError("GitHub CLI (gh) is required")
    try:
        run_command(
            ["gh", "auth", "status"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfigurationError("GitHub CLI is not authenticated") from exc

    token = prompt_token("Telegram Bot Token: ").strip()
    if not token:
        raise ConfigurationError("Telegram Bot Token is required")
    try:
        with client_factory(follow_redirects=False) as client:
            publisher = publisher_factory(token, None, client)
            username = publisher.get_bot_username()
            chat_id = publisher.discover_private_start_chat()
    except TelegramError as exc:
        message = str(exc)
        if "HTTP 409" in message:
            message = (
                "Telegram getUpdates is unavailable because a webhook is active; "
                "remove the webhook before configuration"
            )
        raise ConfigurationError(message) from exc

    try:
        _set_secret(
            "TELEGRAM_BOT_TOKEN",
            token,
            repository,
            run_command,
        )
        _set_secret(
            "TELEGRAM_CHAT_ID",
            chat_id,
            repository,
            run_command,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfigurationError("failed to set GitHub Actions Secrets") from exc

    write_output(f"Bot: @{username}")
    write_output(f"Private chat: {mask_chat_id(chat_id)}")
    write_output("Configured TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
    return 0


def _set_secret(
    name: str,
    value: str,
    repository: str,
    run_command: Callable[..., object],
) -> None:
    run_command(
        ["gh", "secret", "set", name, "--repo", repository],
        input=value,
        check=True,
        capture_output=True,
        text=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Configure private Telegram delivery without storing credentials."
    )
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY)
    args = parser.parse_args(argv)
    try:
        return configure(args.repo)
    except ConfigurationError as exc:
        print(f"Configuration failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
