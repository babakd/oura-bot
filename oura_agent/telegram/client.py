"""
Telegram Bot API client with retry logic.
"""

import re
import time
from typing import Any, Optional

import requests

from oura_agent.config import logger


TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_MAX_ATTEMPTS = 3
_TELEGRAM_URL_TOKEN_RE = re.compile(
    r"(https?://api\.telegram\.org/(?:file/)?bot)[^/\s?]+",
    re.IGNORECASE,
)
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")


class TelegramDeliveryUncertain(RuntimeError):
    """A send may have reached Telegram but no response was received."""


def _safe_telegram_diagnostic(value: Any, bot_token: str = None) -> str:
    """Bound and redact Telegram diagnostics before they reach shared logs."""
    diagnostic = str(value or "")
    if bot_token:
        diagnostic = diagnostic.replace(bot_token, "[REDACTED]")
    diagnostic = _TELEGRAM_URL_TOKEN_RE.sub(r"\1[REDACTED]", diagnostic)
    diagnostic = _TELEGRAM_TOKEN_RE.sub("[REDACTED]", diagnostic)
    return diagnostic[:500]


def _safe_exception_label(exc: BaseException, bot_token: str = None) -> str:
    """Keep an exception useful without allowing request URLs to leak tokens."""
    detail = _safe_telegram_diagnostic(exc, bot_token)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


# Telegram's legacy Markdown entities are kept atomic while messages are split.
# If a single entity is itself too long, it is closed and reopened in valid
# chunks (or converted to plain text for links) rather than sliced mid-markup.
_MARKDOWN_ENTITY_RE = re.compile(
    r"```[\s\S]*?```"
    r"|`[^`\n]*`"
    r"|\[[^\]\n]*\]\([^\)\n]*\)"
    r"|\*[^*\n]+\*"
    r"|_[^_\n]+_"
)


def build_inline_keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    """Build Telegram reply_markup for an inline keyboard."""
    return {"inline_keyboard": rows}


def _normalize_reply_markup(reply_markup: Any) -> Optional[dict[str, Any]]:
    if reply_markup is None:
        return None
    if isinstance(reply_markup, list):
        return build_inline_keyboard(reply_markup)
    if isinstance(reply_markup, dict):
        return reply_markup
    raise TypeError("reply_markup must be a dict, list of button rows, or None")


def _semantic_cut(text: str, limit: int) -> int:
    """Choose a readable split point in plain (non-Markdown-entity) text."""
    if len(text) <= limit:
        return len(text)
    if limit <= 0:
        return 0

    # Prefer a high-level boundary when it is reasonably close to the limit.
    near_boundary = max(1, limit // 2)
    for separator in ("\n\n", "\n", " "):
        position = text.rfind(separator, 0, limit)
        if position >= near_boundary:
            return position + len(separator)

    # A more distant whitespace boundary is still better than splitting a word.
    whitespace = max(
        text.rfind("\n", 0, limit),
        text.rfind(" ", 0, limit),
        text.rfind("\t", 0, limit),
    )
    if whitespace >= 0:
        return whitespace + 1

    # An overlong unformatted token has no semantic boundary available.
    return limit


def _split_plain_text(text: str, limit: int) -> list[str]:
    """Split unformatted text at paragraph, line, or word boundaries."""
    if not text:
        return []
    parts = []
    remaining = text
    while len(remaining) > limit:
        cut = _semantic_cut(remaining, limit)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        parts.append(remaining)
    return parts


def _expand_overlong_entity(entity: str, limit: int) -> list[tuple[str, bool]]:
    """Turn one overlong Markdown entity into independently valid chunks."""
    if entity.startswith("```") and entity.endswith("```"):
        inner = entity[3:-3]
        return [
            (f"```{part}```", True)
            for part in _split_plain_text(inner, max(1, limit - 6))
        ]

    if entity.startswith("`") and entity.endswith("`"):
        inner = entity[1:-1]
        return [
            (f"`{part}`", True)
            for part in _split_plain_text(inner, max(1, limit - 2))
        ]

    if entity.startswith("*") and entity.endswith("*"):
        inner = entity[1:-1]
        return [
            (f"*{part}*", True)
            for part in _split_plain_text(inner, max(1, limit - 2))
        ]

    if entity.startswith("_") and entity.endswith("_"):
        inner = entity[1:-1]
        return [
            (f"_{part}_", True)
            for part in _split_plain_text(inner, max(1, limit - 2))
        ]

    link = re.fullmatch(r"\[([^\]]*)\]\(([^)]*)\)", entity)
    if link:
        # Telegram links cannot be safely closed/reopened across messages.
        # Downgrade only this exceptional overlong link to readable plain text.
        plain = f"{link.group(1)} ({link.group(2)})"
        return [(part, False) for part in _split_plain_text(plain, limit)]

    return [(part, False) for part in _split_plain_text(entity, limit)]


def _markdown_tokens(text: str, limit: int) -> list[tuple[str, bool]]:
    """Tokenize text into plain spans and atomic legacy-Markdown entities."""
    tokens: list[tuple[str, bool]] = []
    cursor = 0
    for match in _MARKDOWN_ENTITY_RE.finditer(text):
        if match.start() > cursor:
            tokens.append((text[cursor:match.start()], False))
        entity = match.group(0)
        if len(entity) <= limit:
            tokens.append((entity, True))
        else:
            tokens.extend(_expand_overlong_entity(entity, limit))
        cursor = match.end()
    if cursor < len(text):
        tokens.append((text[cursor:], False))
    return tokens


def split_telegram_message(
    message: str,
    limit: int = TELEGRAM_MESSAGE_LIMIT,
) -> list[str]:
    """Split a Telegram message without cutting through Markdown entities.

    Paragraphs, then lines, then words are preferred. Markdown entities are
    atomic. An entity longer than Telegram's entire message limit is converted
    into independently balanced entities rather than raw-sliced.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not message:
        return []
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            # Boundary whitespace has no value between separate Telegram
            # messages and can otherwise create an invalid blank message.
            cleaned = current.strip()
            if cleaned:
                chunks.append(cleaned)
        current = ""

    for token, is_entity in _markdown_tokens(message, limit):
        if is_entity:
            if current and len(current) + len(token) > limit:
                flush()
            current += token
            if len(current) == limit:
                flush()
            continue

        remaining = token
        while remaining:
            available = limit - len(current)
            if available <= 0:
                flush()
                available = limit

            if len(remaining) <= available:
                current += remaining
                break

            cut = _semantic_cut(remaining, available)
            # If the current chunk leaves too little room to split plain text
            # semantically, flush it and retry with the full message limit.
            if current and cut == available and not any(
                boundary in remaining[:available] for boundary in ("\n", " ", "\t")
            ):
                flush()
                continue

            if cut <= 0:
                flush()
                continue
            current += remaining[:cut]
            remaining = remaining[cut:]
            flush()

    flush()
    return chunks


# Backward-friendly private alias for callers/tests that conventionally use it.
_split_telegram_message = split_telegram_message


def _retry_after_seconds(response: requests.Response, attempt: int) -> float:
    """Read Telegram Retry-After, falling back to bounded exponential delay."""
    retry_after = None
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            retry_after = headers.get("Retry-After")
        except (AttributeError, TypeError):
            retry_after = None

    if retry_after is None:
        try:
            data = response.json()
            if isinstance(data, dict):
                retry_after = data.get("parameters", {}).get("retry_after")
        except (TypeError, ValueError, AttributeError):
            retry_after = None

    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    return float(min(2 ** attempt, 10))


def _telegram_post(
    url: str,
    payload: dict[str, Any],
    max_attempts: int = TELEGRAM_MAX_ATTEMPTS,
    retry_transport: bool = True,
) -> requests.Response:
    """POST with retries for transport errors, HTTP 429, and HTTP 5xx only."""
    last_error = None
    for attempt in range(max_attempts):
        try:
            response = requests.post(url, json=payload, timeout=30)
        except (requests.RequestException, requests.Timeout) as exc:
            if not retry_transport:
                raise TelegramDeliveryUncertain(
                    "Telegram send result is unknown after a transport failure"
                ) from None
            last_error = exc
            if attempt == max_attempts - 1:
                raise
            time.sleep(float(min(2 ** attempt, 10)))
            continue

        status = response.status_code
        retryable = status == 429 or 500 <= status <= 599
        if not retryable or attempt == max_attempts - 1:
            return response

        time.sleep(_retry_after_seconds(response, attempt))

    # The loop always returns or raises. This keeps type-checkers satisfied.
    if last_error is not None:
        raise last_error
    raise RuntimeError("Telegram request failed without a response")


def _send_telegram_chunk(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = None,
    reply_markup: Any = None,
) -> requests.Response:
    """Send a single message chunk to Telegram with retry logic."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    normalized_markup = _normalize_reply_markup(reply_markup)
    if normalized_markup is not None:
        payload["reply_markup"] = normalized_markup

    return _telegram_post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        payload,
    )


def send_telegram(
    message: str,
    bot_token: str,
    chat_id: str,
    reply_markup: Any = None,
) -> bool:
    """Send message to Telegram with automatic retry. Returns success status."""
    try:
        chunks = split_telegram_message(message)

        for index, chunk in enumerate(chunks):
            chunk_markup = reply_markup if index == len(chunks) - 1 else None
            # Try Markdown first, fall back to plain text if parsing fails
            response = _send_telegram_chunk(
                bot_token,
                chat_id,
                chunk,
                parse_mode="Markdown",
                reply_markup=chunk_markup,
            )

            # If Markdown parsing fails, retry without parse_mode
            if not response.ok and "can't parse entities" in response.text:
                logger.info("Markdown parsing failed, sending as plain text...")
                response = _send_telegram_chunk(
                    bot_token,
                    chat_id,
                    chunk,
                    reply_markup=chunk_markup,
                )

            if not response.ok:
                logger.error(
                    "Telegram API error: %s - %s",
                    response.status_code,
                    _safe_telegram_diagnostic(response.text, bot_token),
                )
                return False

        return True

    except Exception as exc:
        logger.error(
            "Telegram send error (%s)",
            _safe_exception_label(exc, bot_token),
        )
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


def send_telegram_message(
    text: str,
    bot_token: str,
    chat_id: str,
    reply_markup: Any = None,
    raise_on_uncertain: bool = False,
) -> Optional[int]:
    """Send a Telegram message and return the message_id (or None on failure).

    Long text is split safely; the first message_id is returned. Inline keyboard
    markup is attached to the final chunk.
    """
    try:
        chunks = split_telegram_message(text)
        if not chunks:
            return None

        first_message_id = None
        for index, chunk in enumerate(chunks):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            if index == len(chunks) - 1:
                normalized_markup = _normalize_reply_markup(reply_markup)
                if normalized_markup is not None:
                    payload["reply_markup"] = normalized_markup

            resp = _telegram_post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                payload,
                retry_transport=not raise_on_uncertain,
            )
            if not resp.ok and "can't parse entities" in resp.text:
                payload.pop("parse_mode", None)
                resp = _telegram_post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    payload,
                    retry_transport=not raise_on_uncertain,
                )
            if not resp.ok:
                logger.error(
                    "Telegram sendMessage failed: %s %s",
                    resp.status_code,
                    _safe_telegram_diagnostic(resp.text, bot_token),
                )
                return None

            message_id = resp.json().get("result", {}).get("message_id")
            if first_message_id is None:
                first_message_id = message_id
        return first_message_id
    except TelegramDeliveryUncertain:
        if raise_on_uncertain:
            raise
        logger.error("send_telegram_message delivery result is uncertain")
        return None
    except Exception as exc:
        logger.error(
            "send_telegram_message error (%s)",
            _safe_exception_label(exc, bot_token),
        )
        return None


def edit_telegram_message(
    text: str,
    message_id: int,
    bot_token: str,
    chat_id: str,
    reply_markup: Any = None,
) -> bool:
    """Edit a previously-sent Telegram message. Returns True on success."""
    try:
        chunks = split_telegram_message(text)
        if len(chunks) != 1:
            logger.warning("Refusing to edit Telegram message with over-limit text")
            return False

        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": chunks[0],
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        normalized_markup = _normalize_reply_markup(reply_markup)
        if normalized_markup is not None:
            payload["reply_markup"] = normalized_markup

        resp = _telegram_post(
            f"https://api.telegram.org/bot{bot_token}/editMessageText",
            payload,
        )
        if not resp.ok:
            if "can't parse entities" in resp.text:
                payload.pop("parse_mode", None)
                resp = _telegram_post(
                    f"https://api.telegram.org/bot{bot_token}/editMessageText",
                    payload,
                )
                if not resp.ok:
                    # "message is not modified" is benign
                    if "not modified" in resp.text:
                        return True
                    logger.warning(
                        "Telegram editMessageText failed: %s %s",
                        resp.status_code,
                        _safe_telegram_diagnostic(resp.text, bot_token),
                    )
                    return False
            else:
                if "not modified" in resp.text:
                    return True
                logger.warning(
                    "Telegram editMessageText failed: %s %s",
                    resp.status_code,
                    _safe_telegram_diagnostic(resp.text, bot_token),
                )
                return False
        return True
    except Exception as exc:
        logger.error(
            "edit_telegram_message error (%s)",
            _safe_exception_label(exc, bot_token),
        )
        return False


def answer_callback_query(
    callback_query_id: str,
    bot_token: str,
    text: str = None,
    show_alert: bool = False,
    cache_time: int = 0,
) -> bool:
    """Acknowledge a Telegram inline-keyboard callback."""
    payload: dict[str, Any] = {
        "callback_query_id": callback_query_id,
        "show_alert": show_alert,
        "cache_time": cache_time,
    }
    if text:
        payload["text"] = text

    try:
        response = _telegram_post(
            f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
            payload,
        )
        if response.ok:
            return True
        logger.error(
            "Telegram answerCallbackQuery failed: %s %s",
            response.status_code,
            _safe_telegram_diagnostic(response.text, bot_token),
        )
        return False
    except Exception as exc:
        logger.error(
            "answer_callback_query error (%s)",
            _safe_exception_label(exc, bot_token),
        )
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

    def start(self, initial_text: str, reply_markup: Any = None) -> None:
        """Send the initial message. Stores message_id for subsequent edits."""
        if reply_markup is None:
            self.message_id = send_telegram_message(
                initial_text,
                self.bot_token,
                self.chat_id,
            )
        else:
            self.message_id = send_telegram_message(
                initial_text,
                self.bot_token,
                self.chat_id,
                reply_markup=reply_markup,
            )
        self._buffer = initial_text
        self._last_sent_text = initial_text if self.message_id is not None else ""
        self._last_edit_time = time.monotonic()

    def replace_text(self, text: str) -> None:
        """Replace the current buffer; edit if rate limit allows."""
        self._buffer = text
        self._maybe_edit()

    def append_delta(self, delta: str) -> None:
        """Append to the buffer; edit if rate limit allows."""
        self._buffer += delta
        self._maybe_edit()

    def finalize(self, text: Optional[str] = None, reply_markup: Any = None) -> bool:
        """Replace the placeholder, then send continuation chunks if needed."""
        if text is not None:
            self._buffer = text
        chunks = split_telegram_message(self._buffer)
        if not chunks:
            return False

        if self.message_id is None:
            delivered = send_telegram(
                self._buffer,
                self.bot_token,
                self.chat_id,
                reply_markup=reply_markup,
            )
            if delivered:
                self._last_sent_text = self._buffer
            return delivered

        if len(chunks) == 1 and chunks[0] == self._last_sent_text:
            return True

        first_markup = reply_markup if len(chunks) == 1 else None
        if first_markup is None:
            edited = edit_telegram_message(
                chunks[0],
                self.message_id,
                self.bot_token,
                self.chat_id,
            )
        else:
            edited = edit_telegram_message(
                chunks[0],
                self.message_id,
                self.bot_token,
                self.chat_id,
                reply_markup=first_markup,
            )
        if not edited:
            # Do not mark a failed edit as sent. Best-effort delivery as new
            # messages still prevents the final answer from being lost.
            send_telegram(
                self._buffer,
                self.bot_token,
                self.chat_id,
                reply_markup=reply_markup,
            )
            return False

        self._last_sent_text = chunks[0]
        self._last_edit_time = time.monotonic()
        for index, chunk in enumerate(chunks[1:], start=1):
            chunk_markup = reply_markup if index == len(chunks) - 1 else None
            if chunk_markup is None:
                message_id = send_telegram_message(
                    chunk,
                    self.bot_token,
                    self.chat_id,
                )
            else:
                message_id = send_telegram_message(
                    chunk,
                    self.bot_token,
                    self.chat_id,
                    reply_markup=chunk_markup,
                )
            if message_id is None:
                return False

        self._last_sent_text = self._buffer
        return True

    def _maybe_edit(self) -> None:
        if self.message_id is None:
            return
        now = time.monotonic()
        if now - self._last_edit_time < self.min_edit_interval:
            return
        chunks = split_telegram_message(self._buffer)
        if not chunks:
            return
        editable_text = chunks[0]
        if editable_text == self._last_sent_text:
            return
        edited = edit_telegram_message(
            editable_text,
            self.message_id,
            self.bot_token,
            self.chat_id,
        )
        if edited:
            self._last_sent_text = editable_text
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
