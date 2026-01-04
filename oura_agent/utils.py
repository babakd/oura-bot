"""
Utility functions for Oura Agent.
"""

from datetime import datetime, timedelta

from oura_agent.config import (
    NYC_TZ,
    BRIEFS_DIR,
    RAW_DIR,
    METRICS_DIR,
    INTERVENTIONS_DIR,
    CONVERSATIONS_DIR,
    RAW_WINDOW_DAYS,
    logger,
)


def now_nyc() -> datetime:
    """Get current time in NYC timezone."""
    return datetime.now(NYC_TZ)


def ensure_directories():
    """Create data directories if they don't exist."""
    for dir_path in [BRIEFS_DIR, RAW_DIR, METRICS_DIR, INTERVENTIONS_DIR, CONVERSATIONS_DIR]:
        dir_path.mkdir(parents=True, exist_ok=True)


def prune_old_data():
    """Remove data older than retention windows."""
    # Lazy import to avoid circular dependency
    from oura_agent.storage.conversations import prune_conversation_history

    cutoff_date = now_nyc() - timedelta(days=RAW_WINDOW_DAYS)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")

    pruned_count = 0

    # Prune raw data
    for raw_file in RAW_DIR.glob("*.json"):
        file_date = raw_file.stem
        if file_date < cutoff_str:
            raw_file.unlink()
            pruned_count += 1

    # Prune metrics
    for metrics_file in METRICS_DIR.glob("*.json"):
        file_date = metrics_file.stem
        if file_date < cutoff_str:
            metrics_file.unlink()

    # Prune briefs
    for brief_file in BRIEFS_DIR.glob("*.md"):
        file_date = brief_file.stem
        if file_date < cutoff_str:
            brief_file.unlink()

    # Prune interventions (both JSONL and legacy JSON)
    for intervention_file in INTERVENTIONS_DIR.glob("*.json*"):
        file_date = intervention_file.stem
        if file_date < cutoff_str:
            intervention_file.unlink()

    # Prune conversation history
    prune_conversation_history()

    if pruned_count > 0:
        logger.info(f"Pruned {pruned_count} files older than {cutoff_str}")


def get_latest_brief() -> str:
    """Get the most recent morning brief."""
    ensure_directories()
    # Only return morning briefs (exclude -evening suffix)
    briefs = [b for b in BRIEFS_DIR.glob("*.md") if "-evening" not in b.name]
    if briefs:
        # Sort by modification time, most recent first
        briefs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        latest = briefs[0]
        with open(latest) as f:
            return f.read()
    return "No briefs available yet."
