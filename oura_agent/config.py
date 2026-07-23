"""
Configuration constants and logging setup for Oura Agent.
"""

import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def _load_local_env():
    """Load .env file for local development (skipped on Modal)."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return

    try:
        from dotenv import load_dotenv

        load_dotenv(env_file)
    except ImportError:
        # Manual parsing fallback if python-dotenv not installed
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())


_load_local_env()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("oura_agent")

# Timezone for all timestamps
NYC_TZ = ZoneInfo("America/New_York")

# Data directories (Modal volume paths)
DATA_DIR = Path("/data")
BRIEFS_DIR = DATA_DIR / "briefs"
RAW_DIR = DATA_DIR / "raw"
METRICS_DIR = DATA_DIR / "metrics"
INTERVENTIONS_DIR = DATA_DIR / "interventions"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
RECOMMENDATIONS_DIR = DATA_DIR / "recommendations"
RUNS_DIR = DATA_DIR / "runs"
BASELINES_FILE = DATA_DIR / "baselines.json"
PROFILE_FILE = DATA_DIR / "profile.json"

# API configuration
OURA_API_BASE = "https://api.ouraring.com/v2/usercollection"

# Claude models. Fable 5 can be enabled explicitly with ANTHROPIC_MODEL after
# accepting its mandatory 30-day provider-retention requirement. Opus 4.8 is
# the privacy-preserving default migration target and the refusal fallback.
CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
CLAUDE_FALLBACK_MODEL = os.environ.get("ANTHROPIC_FALLBACK_MODEL", "claude-opus-4-8")
CLAUDE_FABLE_MODEL = "claude-fable-5"

# Data retention windows
RAW_WINDOW_DAYS = 28  # Only used for raw API response pruning
BASELINE_WINDOW_DAYS = 60
BRIEF_HISTORY_DAYS = 28  # Days of history to include in morning briefs
CONVERSATION_WINDOW_DAYS = 365  # Conversation history retention
