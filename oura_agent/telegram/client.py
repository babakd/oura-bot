"""
Telegram Bot API client with retry logic.
"""

import time
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from oura_agent.config import logger


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.RequestException, requests.Timeout)),
    reraise=True
)
def _send_telegram_chunk(bot_token: str, chat_id: str, text: str, parse_mode: str = None) -> requests.Response:
    """Send a single message chunk to Telegram with retry logic."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    return requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json=payload,
        timeout=30
    )


def send_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    """Send message to Telegram with automatic retry. Returns success status."""
    try:
        # Telegram has 4096 char limit
        chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]

        for chunk in chunks:
            # Try Markdown first, fall back to plain text if parsing fails
            response = _send_telegram_chunk(bot_token, chat_id, chunk, parse_mode="Markdown")

            # If Markdown parsing fails, retry without parse_mode
            if not response.ok and "can't parse entities" in response.text:
                logger.info("Markdown parsing failed, sending as plain text...")
                response = _send_telegram_chunk(bot_token, chat_id, chunk)

            if not response.ok:
                logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                return False

        return True

    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False


def download_telegram_photo(bot_token: str, file_id: str) -> bytes:
    """Download a photo from Telegram using the getFile API."""
    # Step 1: Get file path from Telegram
    response = requests.get(
        f"https://api.telegram.org/bot{bot_token}/getFile",
        params={"file_id": file_id},
        timeout=30
    )
    response.raise_for_status()

    # Validate Telegram API response
    result = response.json()
    if not result.get("ok"):
        raise ValueError(f"Telegram API error: {result.get('description', 'unknown error')}")

    file_path = result.get("result", {}).get("file_path")
    if not file_path:
        raise ValueError("No file_path in Telegram getFile response")

    # Step 2: Download the actual file
    file_response = requests.get(
        f"https://api.telegram.org/file/bot{bot_token}/{file_path}",
        timeout=30
    )
    file_response.raise_for_status()
    return file_response.content


def send_telegram_message(text: str, bot_token: str, chat_id: str) -> Optional[int]:
    """Send a Telegram message and return the message_id (or None on failure).

    Unlike send_telegram(), this does not chunk — the caller is responsible for
    staying under Telegram's 4096-char cap. Used by the streaming path where we
    need the message_id to edit later.
    """
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if not resp.ok:
            # Fall back to plain text if markdown failed
            if "can't parse entities" in resp.text:
                resp = requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                    timeout=30,
                )
                if not resp.ok:
                    logger.error(f"Telegram sendMessage failed: {resp.status_code} {resp.text}")
                    return None
            else:
                logger.error(f"Telegram sendMessage failed: {resp.status_code} {resp.text}")
                return None
        data = resp.json()
        return data.get("result", {}).get("message_id")
    except Exception as e:
        logger.error(f"send_telegram_message error: {e}")
        return None


def edit_telegram_message(
    text: str,
    message_id: int,
    bot_token: str,
    chat_id: str,
) -> bool:
    """Edit a previously-sent Telegram message. Returns True on success."""
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if not resp.ok:
            if "can't parse entities" in resp.text:
                resp = requests.post(
                    f"https://api.telegram.org/bot{bot_token}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                    timeout=30,
                )
                if not resp.ok:
                    # "message is not modified" is benign
                    if "not modified" in resp.text:
                        return True
                    logger.warning(f"Telegram editMessageText failed: {resp.status_code} {resp.text}")
                    return False
            else:
                if "not modified" in resp.text:
                    return True
                logger.warning(f"Telegram editMessageText failed: {resp.status_code} {resp.text}")
                return False
        return True
    except Exception as e:
        logger.error(f"edit_telegram_message error: {e}")
        return False


class TelegramStreamer:
    """Buffer text updates and edit a single Telegram message, rate-limited.

    Telegram allows ~1 edit/sec per chat. This class batches `replace_text`
    calls and only hits the API when enough time has elapsed. `finalize()`
    forces a final edit regardless of timing.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        min_edit_interval: float = 0.9,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.min_edit_interval = min_edit_interval
        self.message_id: Optional[int] = None
        self._buffer = ""
        self._last_edit_time = 0.0
        self._last_sent_text = ""

    def start(self, initial_text: str) -> None:
        """Send the initial message. Stores message_id for subsequent edits."""
        self.message_id = send_telegram_message(initial_text, self.bot_token, self.chat_id)
        self._buffer = initial_text
        self._last_sent_text = initial_text
        self._last_edit_time = time.monotonic()

    def replace_text(self, text: str) -> None:
        """Replace the current buffer; edit if rate limit allows."""
        self._buffer = text
        self._maybe_edit()

    def append_delta(self, delta: str) -> None:
        """Append to the buffer; edit if rate limit allows."""
        self._buffer += delta
        self._maybe_edit()

    def finalize(self, text: Optional[str] = None) -> None:
        """Force a final edit with the given text (or current buffer)."""
        if text is not None:
            self._buffer = text
        if self.message_id is None:
            return
        if self._buffer == self._last_sent_text:
            return
        edit_telegram_message(self._buffer, self.message_id, self.bot_token, self.chat_id)
        self._last_sent_text = self._buffer
        self._last_edit_time = time.monotonic()

    def _maybe_edit(self) -> None:
        if self.message_id is None:
            return
        now = time.monotonic()
        if now - self._last_edit_time < self.min_edit_interval:
            return
        if self._buffer == self._last_sent_text:
            return
        edit_telegram_message(self._buffer, self.message_id, self.bot_token, self.chat_id)
        self._last_sent_text = self._buffer
        self._last_edit_time = now


def _detect_image_mime_type(image_data: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if image_data[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    elif image_data[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    elif image_data[:6] in (b'GIF87a', b'GIF89a'):
        return "image/gif"
    elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
        return "image/webp"
    # Default to JPEG if unknown (most common from Telegram)
    return "image/jpeg"
