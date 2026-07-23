"""Shared, read-only user profile access for every product surface."""

from __future__ import annotations

import json

from oura_agent.config import PROFILE_FILE, logger
from oura_agent.utils import atomic_write_json


def load_profile() -> dict:
    if not PROFILE_FILE.exists():
        return {}
    try:
        with open(PROFILE_FILE) as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load profile: %s", exc)
        return {}


def save_profile(profile: dict) -> None:
    atomic_write_json(PROFILE_FILE, profile, indent=2)
