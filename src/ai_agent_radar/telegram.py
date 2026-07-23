from __future__ import annotations

import time
from collections.abc import Callable

import httpx


class TelegramError(RuntimeError):
    """A Telegram failure safe to expose in command output."""


def mask_chat_id(chat_id: str) -> str:
    sign = "-" if chat_id.startswith("-") else ""
    digits = chat_id.removeprefix("-")
    if len(digits) <= 4:
        return f"{sign}***"
    return f"{sign}***{digits[-4:]}"


class TelegramPublisher:
    def __init__(
        self,
        token: str,
        chat_id: str | None,
        client: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._client = client
        self._sleep = sleep

    def discover_private_start_chat(self) -> str:
        payload = self._request(
            "getUpdates",
            json={"allowed_updates": ["message"]},
        )
        candidates: set[str] = set()
        for update in payload["result"]:
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            text = message.get("text")
            if (
                not isinstance(chat, dict)
                or chat.get("type") != "private"
                or not isinstance(text, str)
                or not _is_start_command(text)
            ):
                continue
            chat_id = chat.get("id")
            if isinstance(chat_id, int):
                candidates.add(str(chat_id))
        if len(candidates) != 1:
            raise TelegramError(
                "expected exactly one private /start chat; "
                f"found {len(candidates)}"
            )
        return candidates.pop()

    def send_alert(self, text: str, *, chat_id: str | None = None) -> int:
        destination = chat_id or self._chat_id
        if not destination:
            raise TelegramError("Telegram chat ID is required")
        payload = self._request(
            "sendMessage",
            json={"chat_id": destination, "text": text},
        )
        return _message_id(payload)

    def send_bootstrap_test(self, chat_id: str) -> int:
        text = (
            "✅ AI Agent Radar 已连接 Telegram。\n\n"
            f"TELEGRAM_CHAT_ID: {chat_id}\n\n"
            "请把上面的数字保存为 GitHub Actions Secret："
            "TELEGRAM_CHAT_ID。"
        )
        return self.send_alert(text, chat_id=chat_id)

    def _request(self, method: str, **kwargs: object) -> dict:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        try:
            response = self._client.post(url, timeout=20, **kwargs)
        except httpx.HTTPError as exc:
            raise TelegramError("Telegram API request failed") from exc
        if response.status_code >= 400:
            raise TelegramError(
                f"Telegram API request failed (HTTP {response.status_code})"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramError("Telegram API returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise TelegramError("Telegram API rejected the request")
        if not isinstance(payload.get("result"), (dict, list)):
            raise TelegramError("Telegram API returned an invalid result")
        return payload


def _is_start_command(text: str) -> bool:
    command = text.strip().split(maxsplit=1)[0]
    return command.split("@", maxsplit=1)[0] == "/start"


def _message_id(payload: dict) -> int:
    result = payload["result"]
    if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
        raise TelegramError("Telegram API returned an invalid message")
    return result["message_id"]
