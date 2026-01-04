"""
Oura Agent Package

Re-exports all public functions for backward compatibility.
Tests and modal_agent.py import functions from here.
"""

# Config and constants
from oura_agent.config import (
    DATA_DIR,
    BRIEFS_DIR,
    RAW_DIR,
    METRICS_DIR,
    INTERVENTIONS_DIR,
    CONVERSATIONS_DIR,
    BASELINES_FILE,
    OURA_API_BASE,
    CLAUDE_MODEL,
    RAW_WINDOW_DAYS,
    BASELINE_WINDOW_DAYS,
    NYC_TZ,
    logger,
)

# Utilities
from oura_agent.utils import (
    now_nyc,
    ensure_directories,
    prune_old_data,
    get_latest_brief,
)

# Prompt loading
from oura_agent.prompts import (
    get_prompts_dir,
    load_prompt,
    SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
)

# Oura API
from oura_agent.api.oura import (
    fetch_oura_data,
    get_oura_daily_data,
    get_oura_sleep_data,
    get_oura_activity_data,
    get_oura_heartrate,
)

# Metrics extraction
from oura_agent.extraction.metrics import (
    extract_metrics,
    extract_sleep_metrics,
    extract_activity_metrics,
    extract_detailed_sleep,
    extract_detailed_workouts,
)

# Storage - baselines
from oura_agent.storage.baselines import (
    get_default_baselines,
    load_baselines,
    update_baselines,
)

# Storage - interventions
from oura_agent.storage.interventions import (
    load_interventions,
    save_interventions,
    load_historical_interventions,
    save_intervention_raw,
    get_today_interventions,
)

# Storage - metrics
from oura_agent.storage.metrics import (
    load_historical_metrics,
    save_daily_metrics,
    load_recent_briefs,
)

# Storage - conversations
from oura_agent.storage.conversations import (
    load_conversation_history,
    save_conversation_message,
    prune_conversation_history,
)

# Telegram client
from oura_agent.telegram.client import (
    send_telegram,
    download_telegram_photo,
)

# Claude handlers
from oura_agent.claude.handlers import (
    generate_brief_with_claude,
    clean_intervention_with_claude,
    handle_message,
    format_intervention_response,
    analyze_photo_with_claude,
    build_chat_context,
)

__all__ = [
    # Config
    "DATA_DIR",
    "BRIEFS_DIR",
    "RAW_DIR",
    "METRICS_DIR",
    "INTERVENTIONS_DIR",
    "CONVERSATIONS_DIR",
    "BASELINES_FILE",
    "OURA_API_BASE",
    "CLAUDE_MODEL",
    "RAW_WINDOW_DAYS",
    "BASELINE_WINDOW_DAYS",
    "NYC_TZ",
    "logger",
    # Utils
    "now_nyc",
    "ensure_directories",
    "prune_old_data",
    "get_latest_brief",
    # Prompts
    "get_prompts_dir",
    "load_prompt",
    "SYSTEM_PROMPT",
    "CHAT_SYSTEM_PROMPT",
    # Oura API
    "fetch_oura_data",
    "get_oura_daily_data",
    "get_oura_sleep_data",
    "get_oura_activity_data",
    "get_oura_heartrate",
    # Extraction
    "extract_metrics",
    "extract_sleep_metrics",
    "extract_activity_metrics",
    "extract_detailed_sleep",
    "extract_detailed_workouts",
    # Storage - baselines
    "get_default_baselines",
    "load_baselines",
    "update_baselines",
    # Storage - interventions
    "load_interventions",
    "save_interventions",
    "load_historical_interventions",
    "save_intervention_raw",
    "get_today_interventions",
    # Storage - metrics
    "load_historical_metrics",
    "save_daily_metrics",
    "load_recent_briefs",
    # Storage - conversations
    "load_conversation_history",
    "save_conversation_message",
    "prune_conversation_history",
    # Telegram
    "send_telegram",
    "download_telegram_photo",
    # Claude
    "generate_brief_with_claude",
    "clean_intervention_with_claude",
    "handle_message",
    "format_intervention_response",
    "analyze_photo_with_claude",
    "build_chat_context",
]
