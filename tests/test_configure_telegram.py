from pathlib import Path

import pytest

import scripts.configure_telegram as configure_telegram
from ai_agent_radar.telegram import TelegramError


class ClientContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


def test_configure_telegram_hides_token_and_sets_both_secrets_over_stdin() -> None:
    prompts: list[str] = []
    commands: list[tuple[list[str], dict]] = []
    output: list[str] = []

    def prompt_token(prompt: str) -> str:
        prompts.append(prompt)
        return "bot-token-secret"

    class Publisher:
        def __init__(self, token, chat_id, client) -> None:
            assert token == "bot-token-secret"
            assert chat_id is None

        def get_bot_username(self) -> str:
            return "agent_radar_bot"

        def discover_private_start_chat(self) -> str:
            return "123456789"

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return object()

    result = configure_telegram.configure(
        "apiiskan/ai-agent-radar",
        prompt_token=prompt_token,
        publisher_factory=Publisher,
        client_factory=lambda **kwargs: ClientContext(),
        find_executable=lambda name: "/opt/homebrew/bin/gh",
        run_command=run,
        write_output=output.append,
    )

    assert result == 0
    assert prompts == ["Telegram Bot Token: "]
    assert commands[0][0] == ["gh", "auth", "status"]
    assert commands[0][1]["check"] is True
    assert commands[1][0] == [
        "gh",
        "secret",
        "set",
        "TELEGRAM_BOT_TOKEN",
        "--repo",
        "apiiskan/ai-agent-radar",
    ]
    assert commands[1][1]["input"] == "bot-token-secret"
    assert commands[2][0] == [
        "gh",
        "secret",
        "set",
        "TELEGRAM_CHAT_ID",
        "--repo",
        "apiiskan/ai-agent-radar",
    ]
    assert commands[2][1]["input"] == "123456789"
    for command, kwargs in commands:
        assert "bot-token-secret" not in command
        assert "123456789" not in command
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
    rendered = "\n".join(output)
    assert "@agent_radar_bot" in rendered
    assert "***6789" in rendered
    assert "bot-token-secret" not in rendered
    assert "123456789" not in rendered


def test_configure_telegram_stops_before_secret_write_when_chat_is_ambiguous() -> None:
    commands: list[list[str]] = []

    class Publisher:
        def __init__(self, token, chat_id, client) -> None:
            pass

        def get_bot_username(self) -> str:
            return "agent_radar_bot"

        def discover_private_start_chat(self) -> str:
            raise TelegramError("expected exactly one private /start chat; found 2")

    def run(command, **kwargs):
        commands.append(command)
        return object()

    with pytest.raises(
        configure_telegram.ConfigurationError,
        match="exactly one private /start",
    ):
        configure_telegram.configure(
            "apiiskan/ai-agent-radar",
            prompt_token=lambda _: "bot-token-secret",
            publisher_factory=Publisher,
            client_factory=lambda **kwargs: ClientContext(),
            find_executable=lambda name: "/opt/homebrew/bin/gh",
            run_command=run,
            write_output=lambda text: None,
        )

    assert commands == [["gh", "auth", "status"]]


def test_readme_documents_telegram_setup_verification_and_recovery() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "TELEGRAM_BOT_TOKEN" in readme
    assert "TELEGRAM_CHAT_ID" in readme
    assert "scripts/configure_telegram.py" in readme
    assert "/start" in readme
    assert "telegram_test" in readme
    assert "Telegram" in readme
    assert "增长最快 Top 10" in readme
    assert "一条纯文本消息" in readme
    assert "完整 Markdown 日报继续保留在 GitHub" in readme
    assert "发送完整的\n`reports/daily" not in readme
    assert "中文功能介绍" in readme
    assert "模型摘要优先" in readme
    assert "中文规则兜底" in readme
    assert "Telegram 阶段不会再次调用模型" in readme
