#!/usr/bin/env python3
"""
Non-destructive production health checks for the Oura Telegram agent.

Checks:
- required local environment keys are present
- Telegram has a webhook registered
- the registered Modal webhook URL is reachable

This script never sends Telegram chat messages, never calls /clear, and never
touches the Modal volume.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: requests is required. Install dependencies with: pip install -r requirements.txt")
    sys.exit(2)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
TELEGRAM_API_BASE = "https://api.telegram.org"


def load_local_env() -> None:
    if not ENV_FILE.exists():
        return

    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def ok(message: str) -> None:
    print(f"OK: {message}")


def warn(message: str) -> None:
    print(f"WARN: {message}")


def fail(message: str) -> None:
    print(f"FAIL: {message}")


def redact_modal_url(url: str) -> str:
    if not url:
        return ""
    if ".modal.run" not in url:
        return "<redacted>"
    scheme, rest = url.split("://", 1) if "://" in url else ("https", url)
    suffix = rest.split(".modal.run", 1)[1]
    return f"{scheme}://<redacted>.modal.run{suffix}"


def get_webhook_info(bot_token: str) -> dict[str, Any]:
    response = requests.get(f"{TELEGRAM_API_BASE}/bot{bot_token}/getWebhookInfo", timeout=20)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram getWebhookInfo failed"))
    return data.get("result", {})


def check_webhook_reachability(url: str, secret: str | None) -> tuple[bool, str]:
    dummy_update = {
        "update_id": 999999999,
        "message": {
            "message_id": 1,
            "date": 0,
            "chat": {"id": "codex-health-check"},
            "text": "/status",
        },
    }

    headers = {}
    if secret:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret

    response = requests.post(url, json=dummy_update, headers=headers, timeout=30)
    body = response.text[:500]

    if "workspace" in body.lower() and "disabled" in body.lower():
        return False, "Modal HTTP endpoint reports workspace disabled"
    if "spend limit" in body.lower() or "resource exhausted" in body.lower():
        return False, "Modal reports resource exhaustion or billing spend limit"
    if response.status_code == 401:
        return False, "Webhook reached Modal but rejected the supplied/missing secret"
    if response.status_code >= 500:
        return False, f"Webhook reached Modal but returned HTTP {response.status_code}: {body}"
    if response.status_code >= 400:
        return False, f"Webhook returned HTTP {response.status_code}: {body}"

    return True, f"Webhook responded HTTP {response.status_code}"


def main() -> int:
    load_local_env()

    required = [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "ANTHROPIC_API_KEY",
        "OURA_ACCESS_TOKEN",
    ]
    missing = [key for key in required if not os.environ.get(key)]
    for key in required:
        if key in missing:
            fail(f"{key} missing locally")
        else:
            ok(f"{key} present locally")

    webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if webhook_secret:
        ok("TELEGRAM_WEBHOOK_SECRET present locally")
    else:
        warn("TELEGRAM_WEBHOOK_SECRET missing locally; reachability check can only verify unauthenticated behavior")

    if missing:
        return 1

    try:
        info = get_webhook_info(os.environ["TELEGRAM_BOT_TOKEN"])
    except Exception as exc:
        fail(f"Could not read Telegram webhook info: {exc}")
        return 1

    url = info.get("url", "")
    if not url:
        fail("Telegram has no webhook URL registered")
        return 1

    ok(f"Telegram webhook registered: {redact_modal_url(url)}")
    pending = info.get("pending_update_count", 0)
    if pending:
        warn(f"Telegram has {pending} pending update(s)")
    else:
        ok("Telegram pending_update_count is 0")

    if info.get("last_error_message"):
        warn(f"Telegram last error: {info.get('last_error_message')}")
    if info.get("last_error_date"):
        warn(f"Telegram last_error_date: {info.get('last_error_date')}")

    reachable, message = check_webhook_reachability(url, webhook_secret)
    if reachable:
        ok(message)
        return 0

    fail(message)
    if "workspace disabled" in message.lower() or "spend limit" in message.lower():
        print("Action: re-enable the Modal workspace or raise the billing cycle spend limit, then redeploy if needed.")
    elif "secret" in message.lower():
        print("Action: ensure Telegram's secret_token matches TELEGRAM_WEBHOOK_SECRET in the Modal telegram secret.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
