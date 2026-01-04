"""
Telegram Bot API client with retry logic.
"""

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
    file_path = response.json()["result"]["file_path"]

    # Step 2: Download the actual file
    file_response = requests.get(
        f"https://api.telegram.org/file/bot{bot_token}/{file_path}",
        timeout=30
    )
    file_response.raise_for_status()
    return file_response.content


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
