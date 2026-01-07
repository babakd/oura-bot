#!/usr/bin/env python3
"""
Oura Health Agent Setup Wizard

Interactive setup for configuring the Oura health agent.
Run with: python scripts/setup.py
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from getpass import getpass
from pathlib import Path
from typing import Dict, Optional, Tuple

# Minimal dependency - requests for API validation
try:
    import requests
except ImportError:
    print("Error: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# ============================================================================
# CONSTANTS
# ============================================================================

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
ENV_FILE = PROJECT_ROOT / ".env"
ENV_PARTIAL = PROJECT_ROOT / ".env.partial"

OURA_API_BASE = "https://api.ouraring.com/v2"
TELEGRAM_API_BASE = "https://api.telegram.org"
ANTHROPIC_API_BASE = "https://api.anthropic.com"

# Terminal colors (disabled if not a TTY or NO_COLOR is set)
COLORS_ENABLED = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
GREEN = "\033[92m" if COLORS_ENABLED else ""
RED = "\033[91m" if COLORS_ENABLED else ""
YELLOW = "\033[93m" if COLORS_ENABLED else ""
BLUE = "\033[94m" if COLORS_ENABLED else ""
BOLD = "\033[1m" if COLORS_ENABLED else ""
DIM = "\033[2m" if COLORS_ENABLED else ""
RESET = "\033[0m" if COLORS_ENABLED else ""


# ============================================================================
# UI HELPERS
# ============================================================================


def print_header():
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}       Oura Health Agent - Setup Wizard{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}\n")


def print_stage(num: int, title: str):
    print(f"\n{BLUE}{BOLD}[Stage {num}] {title}{RESET}")
    print("-" * 50)


def print_success(msg: str):
    print(f"{GREEN}[OK]{RESET} {msg}")


def print_error(msg: str):
    print(f"{RED}[X]{RESET} {msg}")


def print_info(msg: str):
    print(f"{YELLOW}[i]{RESET} {msg}")


def print_dim(msg: str):
    print(f"{DIM}{msg}{RESET}")


def prompt_secret(prompt: str) -> str:
    """Prompt for sensitive input with masked display."""
    return getpass(f"{prompt}: ").strip()


def prompt_input(prompt: str, default: str = None) -> str:
    """Prompt for regular input with optional default."""
    if default:
        result = input(f"{prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"{prompt}: ").strip()


def confirm(prompt: str, default: bool = True) -> bool:
    """Ask for yes/no confirmation."""
    suffix = "[Y/n]" if default else "[y/N]"
    result = input(f"{prompt} {suffix}: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


# ============================================================================
# VALIDATION FUNCTIONS
# ============================================================================


def validate_anthropic_key(api_key: str) -> Tuple[bool, str]:
    """Validate Anthropic API key by listing models."""
    try:
        response = requests.get(
            f"{ANTHROPIC_API_BASE}/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=15,
        )
        if response.status_code == 200:
            return True, "API key validated"
        elif response.status_code == 401:
            return False, "Invalid API key - check your key at console.anthropic.com"
        else:
            return False, f"Unexpected response: {response.status_code}"
    except requests.Timeout:
        return False, "Request timed out - check your internet connection"
    except requests.RequestException as e:
        return False, f"Network error: {e}"


def validate_oura_token(token: str) -> Tuple[bool, str, dict]:
    """Validate Oura access token by fetching personal info."""
    try:
        response = requests.get(
            f"{OURA_API_BASE}/usercollection/personal_info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if response.status_code == 200:
            data = response.json()
            return True, "Token validated", data
        elif response.status_code == 401:
            return (
                False,
                "Invalid or expired token - create a new one at cloud.ouraring.com",
                {},
            )
        else:
            return False, f"Unexpected response: {response.status_code}", {}
    except requests.Timeout:
        return False, "Request timed out - check your internet connection", {}
    except requests.RequestException as e:
        return False, f"Network error: {e}", {}


def validate_telegram_bot(token: str) -> Tuple[bool, str, dict]:
    """Validate Telegram bot token via getMe."""
    try:
        response = requests.get(f"{TELEGRAM_API_BASE}/bot{token}/getMe", timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                return True, "Bot validated", data.get("result", {})
            return False, "Invalid response from Telegram", {}
        elif response.status_code == 401:
            return False, "Invalid bot token - check your token from @BotFather", {}
        else:
            return False, f"Unexpected response: {response.status_code}", {}
    except requests.Timeout:
        return False, "Request timed out - check your internet connection", {}
    except requests.RequestException as e:
        return False, f"Network error: {e}", {}


def validate_chat_id(bot_token: str, chat_id: str) -> Tuple[bool, str]:
    """Validate chat ID by sending a test message."""
    try:
        response = requests.post(
            f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "Setup wizard connected successfully.",
            },
            timeout=15,
        )
        if response.status_code == 200 and response.json().get("ok"):
            return True, "Chat ID validated - check Telegram for confirmation"
        else:
            error = response.json().get("description", "Unknown error")
            return False, f"Could not send message: {error}"
    except requests.RequestException as e:
        return False, f"Network error: {e}"


# ============================================================================
# CHAT ID HELPER
# ============================================================================


def wait_for_telegram_message(bot_token: str, timeout: int = 120) -> Optional[str]:
    """
    Wait for a message to arrive and return the chat ID.
    Clears existing updates first to only catch new messages.
    """
    # Clear existing updates by getting the latest offset
    try:
        clear_response = requests.get(
            f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates",
            params={"offset": -1, "limit": 1},
            timeout=10,
        )
        offset = 0
        if clear_response.ok:
            updates = clear_response.json().get("result", [])
            if updates:
                offset = updates[-1]["update_id"] + 1
    except requests.RequestException:
        offset = 0

    timeout_mins = timeout // 60
    print_info(f"Waiting for your message (timeout: {timeout_mins} min)...")
    print_dim("   Send any message to your bot in Telegram")

    start = time.time()
    dots = 0

    while time.time() - start < timeout:
        try:
            response = requests.get(
                f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates",
                params={"offset": offset, "timeout": 5},
                timeout=15,
            )
            if response.ok:
                updates = response.json().get("result", [])
                for update in updates:
                    if "message" in update:
                        chat = update["message"].get("chat", {})
                        chat_id = str(chat.get("id", ""))
                        first_name = chat.get("first_name", "")
                        if chat_id:
                            print()  # New line after dots
                            print_success(f"Found chat ID: {chat_id}")
                            if first_name:
                                print_info(f"Chat with: {first_name}")
                            return chat_id

            # Show progress
            dots = (dots + 1) % 4
            print(f"\r   {'.' * (dots + 1):<4}", end="", flush=True)
            time.sleep(0.5)

        except requests.RequestException:
            time.sleep(2)

    print()  # New line
    return None


# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================


def load_existing_env(filepath: Path = ENV_FILE) -> dict:
    """Load existing .env file if present."""
    config = {}
    if filepath.exists():
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    config[key.strip()] = value.strip()
    return config


def save_env_file(config: dict, filepath: Path = ENV_FILE):
    """Save configuration to .env file."""
    with open(filepath, "w") as f:
        f.write("# Oura Health Agent Configuration\n")
        f.write("# Generated by setup wizard\n\n")
        for key in [
            "ANTHROPIC_API_KEY",
            "OURA_ACCESS_TOKEN",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "TELEGRAM_WEBHOOK_SECRET",
        ]:
            if key in config:
                f.write(f"{key}={config[key]}\n")
    print_success(f"Saved configuration to {filepath}")


def generate_webhook_secret() -> str:
    """Generate cryptographically secure webhook secret (64 hex chars)."""
    return secrets.token_hex(32)


# ============================================================================
# MODAL INTEGRATION
# ============================================================================


def check_modal_installed() -> Tuple[bool, bool]:
    """Check if Modal CLI is installed and authenticated.

    Returns: (installed, authenticated)
    """
    try:
        result = subprocess.run(
            ["modal", "--version"], capture_output=True, text=True, timeout=10
        )
        installed = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, False

    if not installed:
        return False, False

    # Check if authenticated by trying to list apps
    try:
        result = subprocess.run(
            ["modal", "app", "list"], capture_output=True, text=True, timeout=15
        )
        authenticated = result.returncode == 0
    except subprocess.TimeoutExpired:
        authenticated = False

    return True, authenticated


def create_modal_secrets(config: dict) -> bool:
    """Create Modal secrets via CLI."""
    secrets_config = [
        ("anthropic", {"ANTHROPIC_API_KEY": config["ANTHROPIC_API_KEY"]}),
        ("oura", {"OURA_ACCESS_TOKEN": config["OURA_ACCESS_TOKEN"]}),
        (
            "telegram",
            {
                "TELEGRAM_BOT_TOKEN": config["TELEGRAM_BOT_TOKEN"],
                "TELEGRAM_CHAT_ID": config["TELEGRAM_CHAT_ID"],
                "TELEGRAM_WEBHOOK_SECRET": config["TELEGRAM_WEBHOOK_SECRET"],
            },
        ),
    ]

    for secret_name, values in secrets_config:
        args = ["modal", "secret", "create", secret_name, "--force"]
        for k, v in values.items():
            args.append(f"{k}={v}")

        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                print_error(f"Failed to create secret '{secret_name}'")
                if result.stderr:
                    print_dim(f"   {result.stderr.strip()}")
                return False
            print_success(f"Created Modal secret: {secret_name}")
        except subprocess.TimeoutExpired:
            print_error(f"Timeout creating secret '{secret_name}'")
            return False

    return True


def deploy_to_modal() -> Tuple[bool, Optional[str]]:
    """
    Run modal deploy and extract the webhook URL.

    Returns: (success, webhook_url or None)
    """
    modal_agent = PROJECT_ROOT / "modal_agent.py"
    if not modal_agent.exists():
        print_error(f"modal_agent.py not found at {modal_agent}")
        return False, None

    print_info("Deploying to Modal (this may take a minute)...")

    try:
        result = subprocess.run(
            ["modal", "deploy", str(modal_agent)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=PROJECT_ROOT,
        )

        if result.returncode != 0:
            print_error("Deployment failed")
            if result.stderr:
                print_dim(f"   {result.stderr.strip()}")
            return False, None

        print_success("Deployed to Modal")

        # Parse output for webhook URL
        # Modal outputs something like:
        # Created web function telegram_webhook => https://user--app-telegram-webhook.modal.run
        webhook_url = None
        for line in result.stdout.split("\n"):
            if "telegram_webhook" in line.lower() and "modal.run" in line:
                # Extract URL from line
                parts = line.split()
                for part in parts:
                    if "modal.run" in part:
                        webhook_url = part.strip()
                        if not webhook_url.startswith("http"):
                            webhook_url = "https://" + webhook_url
                        break

        # Alternative: look for any modal.run URL
        if not webhook_url:
            for line in result.stdout.split("\n"):
                if "modal.run" in line:
                    import re

                    urls = re.findall(r"https?://[^\s]+modal\.run[^\s]*", line)
                    if urls:
                        webhook_url = urls[0]
                        break

        if webhook_url:
            print_info(f"Webhook URL: {webhook_url}")

        return True, webhook_url

    except subprocess.TimeoutExpired:
        print_error("Deployment timed out")
        return False, None


def register_telegram_webhook(
    bot_token: str, webhook_url: str, secret: str
) -> bool:
    """Register webhook URL with Telegram."""
    try:
        response = requests.post(
            f"{TELEGRAM_API_BASE}/bot{bot_token}/setWebhook",
            json={"url": webhook_url, "secret_token": secret},
            timeout=15,
        )
        if response.status_code == 200 and response.json().get("ok"):
            print_success("Telegram webhook registered")
            return True
        else:
            error = response.json().get("description", "Unknown error")
            print_error(f"Failed to register webhook: {error}")
            return False
    except requests.RequestException as e:
        print_error(f"Network error: {e}")
        return False


def get_webhook_info(bot_token: str) -> dict:
    """Get current webhook configuration from Telegram."""
    try:
        response = requests.get(
            f"{TELEGRAM_API_BASE}/bot{bot_token}/getWebhookInfo", timeout=15
        )
        if response.ok:
            return response.json().get("result", {})
    except requests.RequestException:
        pass
    return {}


# ============================================================================
# MAIN WIZARD
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Oura Health Agent Setup Wizard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/setup.py              # Full interactive setup
  python scripts/setup.py --update     # Update existing credentials
  python scripts/setup.py --local-only # Only create .env file
  python scripts/setup.py --skip-deploy # Skip Modal deployment
        """,
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update existing configuration (loads current .env)",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Only create .env file, skip Modal secrets",
    )
    parser.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Skip Modal deployment step",
    )
    args = parser.parse_args()

    print_header()

    # Load existing config if updating
    config = load_existing_env() if args.update else {}
    if config and args.update:
        print_info(f"Loaded existing configuration from {ENV_FILE}")
        print_dim("   Press Enter to keep existing values\n")

    # =========================================================================
    # Stage 1: Anthropic API Key
    # =========================================================================
    print_stage(1, "Anthropic API Key")
    print("Get your API key from: https://console.anthropic.com/settings/keys")
    print("  1. Sign in or create an account")
    print("  2. Go to Settings > API Keys")
    print("  3. Click 'Create Key' and copy it\n")

    existing_key = config.get("ANTHROPIC_API_KEY", "")
    if existing_key and args.update:
        print_dim(f"   Current: {existing_key[:12]}...{existing_key[-4:]}")

    while True:
        key = prompt_secret("Anthropic API key (sk-ant-...)")
        if not key and existing_key:
            key = existing_key
            print_info("Using existing key")
            break
        if not key:
            print_error("API key is required")
            continue
        if not key.startswith("sk-ant-"):
            print_error("Invalid format - should start with 'sk-ant-'")
            continue

        print_info("Validating...")
        valid, msg = validate_anthropic_key(key)
        if valid:
            print_success(msg)
            break
        print_error(msg)
        if not confirm("Try again?"):
            print_info("Skipping validation - key saved anyway")
            break

    config["ANTHROPIC_API_KEY"] = key

    # =========================================================================
    # Stage 2: Oura Access Token
    # =========================================================================
    print_stage(2, "Oura Access Token")
    print("Get your token from: https://cloud.ouraring.com/personal-access-tokens\n")
    print_dim("   Create a new token with all scopes enabled\n")

    existing_token = config.get("OURA_ACCESS_TOKEN", "")
    if existing_token and args.update:
        print_dim(f"   Current: {existing_token[:8]}...{existing_token[-4:]}")

    while True:
        token = prompt_secret("Oura access token")
        if not token and existing_token:
            token = existing_token
            print_info("Using existing token")
            break
        if not token:
            print_error("Token is required")
            continue

        print_info("Validating...")
        valid, msg, user_data = validate_oura_token(token)
        if valid:
            print_success(msg)
            if user_data.get("email"):
                print_info(f"Account: {user_data.get('email')}")
            break
        print_error(msg)
        if not confirm("Try again?"):
            print_info("Skipping validation - token saved anyway")
            break

    config["OURA_ACCESS_TOKEN"] = token

    # =========================================================================
    # Stage 3: Telegram Bot Token
    # =========================================================================
    print_stage(3, "Telegram Bot Token")
    print("Create a bot:")
    print("  1. Open Telegram and search for @BotFather")
    print("  2. Send /newbot and follow the prompts")
    print("  3. Copy the token (looks like 123456789:ABC...)\n")

    existing_bot = config.get("TELEGRAM_BOT_TOKEN", "")
    if existing_bot and args.update:
        print_dim(f"   Current: {existing_bot[:15]}...")

    while True:
        bot_token = prompt_secret("Bot token")
        if not bot_token and existing_bot:
            bot_token = existing_bot
            print_info("Using existing token")
            break
        if not bot_token:
            print_error("Bot token is required")
            continue
        if ":" not in bot_token:
            print_error("Invalid format - should contain ':'")
            continue

        print_info("Validating...")
        valid, msg, bot_info = validate_telegram_bot(bot_token)
        if valid:
            print_success(msg)
            if bot_info.get("username"):
                print_info(f"Bot: @{bot_info.get('username')}")
            break
        print_error(msg)
        if not confirm("Try again?"):
            print_info("Skipping validation - token saved anyway")
            break

    config["TELEGRAM_BOT_TOKEN"] = bot_token
    # Save bot username if we got it from validation
    if "bot_info" in dir() and bot_info and bot_info.get("username"):
        config["_BOT_USERNAME"] = bot_info.get("username")

    # =========================================================================
    # Stage 4: Telegram Chat ID
    # =========================================================================
    print_stage(4, "Telegram Chat ID")

    existing_chat = config.get("TELEGRAM_CHAT_ID", "")
    if existing_chat and args.update:
        print_dim(f"   Current: {existing_chat}")
        if confirm("Keep existing chat ID?"):
            chat_id = existing_chat
        else:
            chat_id = None
    else:
        chat_id = None

    if not chat_id:
        print("Options:")
        print("  1. Auto-detect (recommended) - send a message to your bot")
        print("  2. Enter manually\n")

        choice = prompt_input("Choose [1/2]", "1")

        if choice == "1":
            bot_username = bot_info.get("username", "your bot") if "bot_info" in dir() else "your bot"
            print(f"\nOpen Telegram and send any message to @{bot_username}")
            chat_id = wait_for_telegram_message(bot_token, timeout=240)

            if not chat_id:
                print_error("Timed out waiting for message")
                chat_id = prompt_input("Enter chat ID manually")
        else:
            print("\nTo find your chat ID:")
            print(f"  1. Send a message to your bot")
            print(f"  2. Visit: https://api.telegram.org/bot{bot_token}/getUpdates")
            print('  3. Find "chat":{"id":YOUR_ID}\n')
            chat_id = prompt_input("Chat ID")

    if chat_id:
        # Validate by sending a test message
        print_info("Sending test message...")
        valid, msg = validate_chat_id(bot_token, chat_id)
        if valid:
            print_success(msg)
        else:
            print_error(msg)
            if not confirm("Continue anyway?"):
                chat_id = prompt_input("Enter correct chat ID")

    config["TELEGRAM_CHAT_ID"] = chat_id

    # =========================================================================
    # Stage 5: Webhook Secret
    # =========================================================================
    print_stage(5, "Webhook Secret")

    existing_secret = config.get("TELEGRAM_WEBHOOK_SECRET", "")

    if existing_secret and args.update:
        print_dim(f"   Current: {existing_secret[:16]}...")
        if confirm("Keep existing secret?"):
            webhook_secret = existing_secret
        else:
            webhook_secret = generate_webhook_secret()
            print_success(f"Generated new secret: {webhook_secret[:16]}...")
    else:
        webhook_secret = generate_webhook_secret()
        print_success(f"Generated secret: {webhook_secret[:16]}...")

    config["TELEGRAM_WEBHOOK_SECRET"] = webhook_secret

    # =========================================================================
    # Stage 6: Save Configuration
    # =========================================================================
    print_stage(6, "Save Configuration")

    # Always save .env for local development
    save_env_file(config)

    if not args.local_only:
        print()
        modal_installed, modal_authenticated = check_modal_installed()

        # Offer to install Modal if not present
        if not modal_installed:
            print_info("Modal CLI is required for cloud deployment.")
            print()
            print("Modal is a serverless platform that will host your agent.")
            print("It runs your code in the cloud and handles scheduling.\n")

            if confirm("Install Modal CLI now?"):
                print_info("Installing Modal (this may take 30-60 seconds)...")
                try:
                    result = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "modal"],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if result.returncode == 0:
                        print_success("Modal installed successfully")
                        modal_installed = True
                    else:
                        print_error("Installation failed")
                        if result.stderr:
                            print_dim(f"   {result.stderr.strip()[:200]}")
                except subprocess.TimeoutExpired:
                    print_error("Installation timed out")
                except Exception as e:
                    print_error(f"Installation error: {e}")
            else:
                print_info("Skipping Modal. Run 'pip install modal' later to deploy.")

        # Offer to authenticate if Modal is installed but not authenticated
        if modal_installed and not modal_authenticated:
            print()
            print_info("Modal needs to be authenticated.")
            print()
            print("This will open your browser to log in to Modal.")
            print("After logging in, return here to continue.\n")

            if confirm("Authenticate with Modal now?"):
                print_info("Opening browser for Modal authentication...")
                print_dim("   (Complete the login in your browser, then return here)")
                try:
                    result = subprocess.run(
                        ["modal", "setup"],
                        timeout=300,
                    )
                    if result.returncode == 0:
                        print_success("Modal authenticated successfully")
                        modal_authenticated = True
                    else:
                        print_error("Authentication may have failed")
                        print_info("You can run 'modal setup' manually later")
                except subprocess.TimeoutExpired:
                    print_error("Authentication timed out")
                except Exception as e:
                    print_error(f"Authentication error: {e}")
            else:
                print_info("Skipping auth. Run 'modal setup' later to authenticate.")

        # Create secrets if Modal is ready
        if modal_installed and modal_authenticated:
            print()
            print_info("Creating Modal secrets...")
            if create_modal_secrets(config):
                print_success("All Modal secrets created")
            else:
                print_error("Some secrets failed - check errors above")
                print_info("You can re-run with --update to retry")
        elif not args.local_only:
            print()
            print_info("Modal not ready - secrets not created")
            print("Re-run with --update after setting up Modal")

    # =========================================================================
    # Stage 7: Deploy (Optional)
    # =========================================================================
    if not args.local_only and not args.skip_deploy:
        print_stage(7, "Deploy to Modal")

        modal_installed, modal_authenticated = check_modal_installed()
        if modal_installed and modal_authenticated:
            if confirm("Deploy to Modal now?"):
                success, webhook_url = deploy_to_modal()

                if success and webhook_url:
                    print()
                    if confirm("Register webhook with Telegram?"):
                        if register_telegram_webhook(
                            bot_token, webhook_url, webhook_secret
                        ):
                            info = get_webhook_info(bot_token)
                            if info.get("url"):
                                print_success(f"Webhook active: {info.get('url')}")
                elif success:
                    print_info("Deployment succeeded but webhook URL not detected")
                    print("Register webhook manually:")
                    print(f'  curl -X POST "https://api.telegram.org/bot{bot_token}/setWebhook" \\')
                    print('    -H "Content-Type: application/json" \\')
                    print("    -d '{")
                    print('      "url": "YOUR_MODAL_WEBHOOK_URL",')
                    print(f'      "secret_token": "{webhook_secret}"')
                    print("    }'")
        else:
            print_info("Skipping deployment - Modal not available")

    # =========================================================================
    # Done
    # =========================================================================
    print(f"\n{GREEN}{BOLD}Setup complete!{RESET}")

    # Get bot username for instructions (saved from Stage 3)
    bot_username = config.get("_BOT_USERNAME")

    if args.local_only:
        print("\nNext steps:")
        print("  - Your .env file is ready for local development")
        print("  - To deploy to Modal later, run: python scripts/setup.py --update")
    else:
        print("\nNext steps:")
        print("  1. Backfill historical data (recommended):")
        print("     modal run modal_agent.py::backfill_history --days 365")
        print()
        print("  2. Test the morning brief:")
        print("     modal run modal_agent.py")

        # Usage instructions
        print(f"\n{BOLD}How to use your bot:{RESET}")
        if bot_username:
            print(f"  Open Telegram and message @{bot_username}")
        else:
            print("  Open Telegram and message your bot")

        print(f"\n  {BOLD}Daily Briefs:{RESET}")
        print("  Your bot will automatically send a morning brief at 10 AM EST")
        print("  with sleep analysis, HRV trends, and recommendations.")

        print(f"\n  {BOLD}Log Interventions:{RESET}")
        print("  Just message naturally:")
        print("    - took 400mg magnesium")
        print("    - 20 min sauna")
        print("    - had 2 glasses of wine")
        print("  Or send a photo of supplements/food.")

        print(f"\n  {BOLD}Ask Questions:{RESET}")
        print("  Ask anything about your health data:")
        print("    - How did I sleep last night?")
        print("    - What's my HRV trend this week?")
        print("    - Compare this month to last month")

        print(f"\n  {BOLD}Commands:{RESET}")
        print("    /status      - Today's logged interventions")
        print("    /brief       - Show latest morning brief")
        print("    /regen-brief - Regenerate today's brief")
        print("    /help        - All commands")

        print(f"\n  {BOLD}View logs:{RESET}")
        print("    modal app logs oura-agent")

    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Setup cancelled.{RESET}")
        # Save partial progress
        sys.exit(1)
