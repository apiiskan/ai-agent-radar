import json

import httpx
import pytest

from ai_agent_radar.telegram import TelegramError, TelegramPublisher, mask_chat_id


def telegram_response(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/getUpdates"):
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 1,
                        "message": {
                            "text": "/start",
                            "chat": {"id": 123456789, "type": "private"},
                        },
                    },
                    {
                        "update_id": 2,
                        "message": {
                            "text": "ignored",
                            "chat": {"id": 999, "type": "private"},
                        },
                    },
                    {
                        "update_id": 3,
                        "message": {
                            "text": "/start",
                            "chat": {"id": -100123, "type": "group"},
                        },
                    },
                ],
            },
        )
    if request.url.path.endswith("/sendMessage"):
        payload = json.loads(request.content)
        assert payload["chat_id"] == "123456789"
        assert "TELEGRAM_CHAT_ID: 123456789" in payload["text"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
    raise AssertionError(f"unexpected Telegram method: {request.url.path.rsplit('/', 1)[-1]}")


def test_bootstrap_discovers_one_private_start_and_sends_chat_id_to_that_chat() -> None:
    with httpx.Client(transport=httpx.MockTransport(telegram_response)) as client:
        publisher = TelegramPublisher("bot-token", None, client)

        chat_id = publisher.discover_private_start_chat()
        message_id = publisher.send_bootstrap_test(chat_id)

    assert chat_id == "123456789"
    assert message_id == 77


@pytest.mark.parametrize(
    "updates",
    [
        [],
        [
            {"message": {"text": "/start", "chat": {"id": 1111, "type": "private"}}},
            {"message": {"text": "/start", "chat": {"id": 2222, "type": "private"}}},
        ],
    ],
)
def test_bootstrap_refuses_zero_or_ambiguous_private_start_chats(updates) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": updates})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher("super-secret-token", None, client)
        with pytest.raises(TelegramError) as error:
            publisher.discover_private_start_chat()

    message = str(error.value)
    assert "super-secret-token" not in message
    assert "1111" not in message
    assert "2222" not in message


def test_telegram_api_failure_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "ok": False,
                "description": "bad bot-token for chat 123456789",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher("bot-token", None, client)
        with pytest.raises(TelegramError) as error:
            publisher.discover_private_start_chat()

    assert str(error.value) == "Telegram API request failed (HTTP 401)"


@pytest.mark.parametrize(
    ("chat_id", "masked"),
    [
        ("123456789", "***6789"),
        ("-100123456789", "-***6789"),
        ("12", "***"),
    ],
)
def test_chat_id_masking_never_returns_the_full_identifier(chat_id, masked) -> None:
    assert mask_chat_id(chat_id) == masked
    assert mask_chat_id(chat_id) != chat_id
