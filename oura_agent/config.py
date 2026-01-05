"""
Configuration constants and logging setup for Oura Agent.
"""

import logging
from pathlib import Path
from zoneinfo import ZoneInfo

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
BASELINES_FILE = DATA_DIR / "baselines.json"

# API configuration
OURA_API_BASE = "https://api.ouraring.com/v2/usercollection"

# Claude model
CLAUDE_MODEL = "claude-opus-4-5-20251101"

# Data retention windows
RAW_WINDOW_DAYS = 28  # Only used for raw API response pruning
BASELINE_WINDOW_DAYS = 60
BRIEF_HISTORY_DAYS = 28  # Days of history to include in morning briefs
CONVERSATION_WINDOW_DAYS = 365  # Conversation history retention
