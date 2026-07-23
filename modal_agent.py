"""
Oura Daily Optimization Agent
Runs daily via Modal cron, analyzes Oura data, sends brief to Telegram.
Uses a deterministic insight packet plus a configurable Claude model to select
one concise, numerically grounded daily card.

This is the Modal entrypoint. All logic is in the oura_agent package.
"""

import modal
import os
import json
from datetime import datetime, timedelta

# ============================================================================
# MODAL CONFIGURATION
# ============================================================================

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "anthropic>=0.118.0",
        "requests>=2.28.0",
        "fastapi>=0.100.0",
        "tenacity>=8.2.0",
    )
    .add_local_dir("prompts", "/root/prompts")
    .add_local_dir("oura_agent", "/root/oura_agent")
)

app = modal.App("oura-agent", image=image)

# Persistent encrypted volume for health data
volume = modal.Volume.from_name("oura-health-data", create_if_missing=True)
# Small distributed coordination keys prevent duplicate Telegram updates and
# overlapping brief runs across separate Modal containers. Health records stay
# on the encrypted Volume; this Dict stores only opaque IDs and timestamps.
coordination = modal.Dict.from_name(
    "oura-agent-coordination",
    create_if_missing=True,
)

# ============================================================================
# RE-EXPORTS FOR TEST BACKWARD COMPATIBILITY
# ============================================================================
# Tests use monkeypatch.setattr(modal_agent, "X", ...) so we need to re-export
# all functions and constants that tests mock.

from oura_agent.config import (
    DATA_DIR,
    BRIEFS_DIR,
    RAW_DIR,
    METRICS_DIR,
    INTERVENTIONS_DIR,
    CONVERSATIONS_DIR,
    RECOMMENDATIONS_DIR,
    RUNS_DIR,
    BASELINES_FILE,
    PROFILE_FILE,
    OURA_API_BASE,
    CLAUDE_MODEL,
    RAW_WINDOW_DAYS,
    BASELINE_WINDOW_DAYS,
    BRIEF_HISTORY_DAYS,
    NYC_TZ,
    logger,
)

from oura_agent.utils import (
    now_nyc,
    ensure_directories,
    prune_old_data,
    get_latest_brief,
    atomic_write_json,
    atomic_write_text,
)

from oura_agent.prompts import (
    get_prompts_dir as _get_prompts_dir,
    load_prompt as _load_prompt,
    SYSTEM_PROMPT,
)

from oura_agent.api.oura import (
    OuraAPIError,
    OuraAuthenticationError,
    fetch_oura_data,
    get_oura_daily_data,
    get_oura_sleep_data,
    get_oura_activity_data,
    get_oura_heartrate,
)

from oura_agent.extraction.metrics import (
    extract_metrics,
    extract_sleep_metrics,
    extract_activity_metrics,
    extract_detailed_sleep,
    extract_detailed_workouts,
    _workout_duration_minutes,
)

from oura_agent.storage.baselines import (
    get_default_baselines,
    load_baselines,
    save_baselines,
    update_baselines,
)

from oura_agent.storage.interventions import (
    load_interventions,
    save_interventions,
    load_historical_interventions,
    save_intervention_raw,
    get_today_interventions,
    soft_clear_interventions,
    undo_clear_interventions,
    _migrate_json_to_jsonl,
)

from oura_agent.storage.metrics import (
    load_historical_metrics,
    save_daily_metrics,
    load_recent_briefs,
)

from oura_agent.storage.conversations import (
    load_conversation_history,
    save_conversation_message,
    prune_conversation_history,
)

from oura_agent.telegram.client import (
    answer_callback_query,
    send_telegram,
    send_telegram_message,
    edit_telegram_message,
    TelegramStreamer,
    download_telegram_photo,
    _detect_image_mime_type,
    _send_telegram_chunk,
    TelegramDeliveryUncertain,
)

from oura_agent.claude.handlers import (
    generate_brief_with_agent,
    generate_daily_card,
    clean_intervention_with_claude,
    analyze_photo_with_claude,
)

from oura_agent.claude.agent import handle_message_with_agent
from oura_agent.insights import build_daily_insight_packet
from oura_agent.storage.profile import (
    load_profile as _load_profile_from_storage,
    save_profile,
)
from oura_agent.storage.recommendations import (
    build_feedback_keyboard,
    card_was_delivered,
    get_latest_card,
    record_delivery,
    record_feedback,
    record_next_day_outcome,
    save_daily_card,
    summarize_feedback,
)
from oura_agent.storage.runs import (
    acquire_daily_lock,
    claim_update,
    complete_update,
    fail_update,
    mark_update_processed,
    new_run_id,
    record_run_event,
    release_daily_lock,
)

# ============================================================================
# HELPER FUNCTION FOR VOLUME RELOAD
# ============================================================================

def _reload_volume():
    """Reload volume to see latest commits from other containers."""
    try:
        volume.reload()
    except RuntimeError:
        pass  # Running locally, not in Modal


# ============================================================================
# PROFILE MANAGEMENT
# ============================================================================

def load_profile() -> dict:
    """Backward-compatible wrapper around shared profile storage."""
    return _load_profile_from_storage()


@app.function(volumes={"/data": volume})
def upload_profile(profile_json: str):
    """
    Upload user profile to Modal volume.

    Called by setup.py after collecting personalization preferences.
    Args:
        profile_json: JSON string of the profile data
    Returns:
        dict with status and path
    """
    try:
        profile = json.loads(profile_json)

        ensure_directories()

        save_profile(profile)

        volume.commit()

        logger.info(f"Profile saved to {PROFILE_FILE}")
        return {"status": "ok", "path": str(PROFILE_FILE)}
    except Exception as e:
        logger.error(f"Failed to save profile: {e}")
        return {"status": "error", "message": str(e)}


# ============================================================================
# MAIN AGENT FUNCTION
# ============================================================================

@app.function(
    secrets=[
        modal.Secret.from_name("anthropic"),
        modal.Secret.from_name("oura"),
        modal.Secret.from_name("telegram"),
    ],
    volumes={"/data": volume},
    timeout=300,
    max_containers=1,
    schedule=modal.Cron("0 10 * * *", timezone="America/New_York"),
)
def morning_brief(force: bool = False):
    """
    Main agent function. Runs daily to:
    1. Fetch Oura data
    2. Analyze against baselines
    3. Build a deterministic insight packet
    4. Select and render one grounded decision card
    5. Persist the recommendation/feedback handle and send it to Telegram
    """
    today = now_nyc().strftime("%Y-%m-%d")
    run_id = new_run_id()

    oura_token = os.environ.get("OURA_ACCESS_TOKEN")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    logger.info("Starting morning brief run=%s date=%s force=%s", run_id, today, force)

    _reload_volume()
    ensure_directories()

    latest_card = get_latest_card(today)
    if not force and latest_card:
        delivery_key = f"telegram-delivery:{latest_card['id']}"
        distributed_delivery = coordination.get(delivery_key)
        delivered = card_was_delivered(latest_card["id"])
        delivery_state = (
            distributed_delivery.get("state")
            if isinstance(distributed_delivery, dict)
            else None
        )
        if delivered or delivery_state in {"sent", "attempting"}:
            status = (
                "already_sent"
                if delivered or delivery_state == "sent"
                else "delivery_uncertain"
            )
            logger.info(
                "Skipping duplicate brief for %s; delivery_state=%s",
                today,
                delivery_state or "volume-sent",
            )
            if not delivered and delivery_state == "sent":
                record_delivery(
                    latest_card["id"],
                    "sent",
                    message_id=distributed_delivery.get("message_id"),
                    detail="reconciled from distributed delivery state",
                )
                volume.commit()
            return {
                "status": status,
                "date": today,
                "card_id": latest_card["id"],
                "run_id": run_id,
            }

    if not acquire_daily_lock(
        today,
        run_id,
        coordination=coordination,
    ):
        logger.warning("Another morning brief is already running for %s", today)
        return {"status": "already_running", "date": today, "run_id": run_id}

    try:
        # Make the cross-container lock visible before doing paid or mutating work.
        volume.commit()
    except RuntimeError:
        pass

    try:
        missing = [
            name
            for name, value in (
                ("OURA_ACCESS_TOKEN", oura_token),
                ("ANTHROPIC_API_KEY", anthropic_key),
                ("TELEGRAM_BOT_TOKEN", bot_token),
                ("TELEGRAM_CHAT_ID", chat_id),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("Missing required configuration: " + ", ".join(missing))

        record_run_event(run_id, "started", date=today, force=force)

        yesterday = (now_nyc() - timedelta(days=1)).strftime("%Y-%m-%d")

        # === FETCH AND SAVE SLEEP DATA (today's file) ===
        logger.info(f"Fetching sleep data for {today} (wake-date)...")
        sleep_data = get_oura_sleep_data(oura_token, today)

        # Check if we got a valid main sleep session. A successful empty
        # response is different from an API failure: typed request failures
        # have already propagated out of get_oura_sleep_data().
        no_valid_sleep = not sleep_data.get("sleep")

        if no_valid_sleep:
            logger.warning("No complete main sleep session is available for %s", today)
            existing_file = METRICS_DIR / f"{today}.json"
            existing = {}
            if existing_file.exists():
                try:
                    existing = json.loads(existing_file.read_text())
                except (json.JSONDecodeError, OSError):
                    existing = {}

            existing_summary = existing.get("summary", {})
            if existing_summary.get("sleep_recorded") is True:
                # A later empty sync must not erase a previously successful
                # session. Reuse the canonical record and mark freshness below.
                sleep_metrics = {
                    key: value
                    for key, value in existing_summary.items()
                    if key in {
                        "sleep_score",
                        "deep_sleep_minutes",
                        "light_sleep_minutes",
                        "rem_sleep_minutes",
                        "total_sleep_minutes",
                        "sleep_efficiency",
                        "hrv",
                        "avg_hr",
                        "avg_breath",
                        "latency_minutes",
                        "restless_periods",
                        "resting_hr",
                        "readiness",
                        "temperature_deviation",
                        "sleep_recorded",
                    }
                }
                detailed_sleep = existing.get("detailed_sleep", {})
                logger.info("Reusing previously synced sleep record for %s", today)
            else:
                sleep_metrics = {
                    "sleep_recorded": False,
                    "sleep_note": "No complete sleep session available from Oura",
                }
                detailed_sleep = {}
                save_daily_metrics(today, sleep_metrics, {}, None, merge=True)
        else:
            sleep_metrics = extract_sleep_metrics(sleep_data)
            sleep_metrics["sleep_recorded"] = True
            detailed_sleep = extract_detailed_sleep(sleep_data)
            logger.info(f"Extracted sleep metrics: {len(sleep_metrics)} fields, detailed: {len(detailed_sleep)} fields")
            save_daily_metrics(today, sleep_metrics, detailed_sleep, None, merge=True)
        logger.info(f"Saved sleep data to metrics/{today}.json")

        # === FETCH AND SAVE ACTIVITY DATA (yesterday's file) ===
        logger.info(f"Fetching activity data for {yesterday} (calendar date)...")
        activity_data = get_oura_activity_data(oura_token, yesterday)

        activity_metrics = extract_activity_metrics(activity_data)
        detailed_workouts = extract_detailed_workouts(activity_data)
        logger.info(f"Extracted activity metrics: {len(activity_metrics)} fields, workouts: {len(detailed_workouts)}")

        save_daily_metrics(yesterday, activity_metrics, None, detailed_workouts, merge=True)
        logger.info(f"Saved activity data to metrics/{yesterday}.json")

        # === SAVE RAW DATA ===
        oura_data = {
            **{key: value for key, value in sleep_data.items() if key != "_fetch_status"},
            **{key: value for key, value in activity_data.items() if key != "_fetch_status"},
            "_fetch_status": {
                **sleep_data.get("_fetch_status", {}),
                **activity_data.get("_fetch_status", {}),
            },
        }
        raw_file = RAW_DIR / f"{today}.json"
        atomic_write_json(raw_file, oura_data, indent=2)
        logger.info(f"Saved raw data to {raw_file}")

        # === PREPARE COMBINED METRICS ===
        metrics = {**sleep_metrics, **activity_metrics}
        logger.info("Prepared %s combined metric fields", len(metrics))

        if not metrics:
            raise ValueError("No metrics extracted from Oura data")

        # Close yesterday's loop with a factual next-day observation. This is
        # deliberately stored as "observed after", never as a causal effect.
        outcome_event = record_next_day_outcome(today, sleep_metrics)
        if outcome_event:
            logger.info(
                "Linked next-day outcome to card=%s",
                outcome_event["card_id"],
            )

        # Update per-metric dated observations. Activity belongs to yesterday,
        # while sleep/readiness belongs to today's wake-date.
        baselines = load_baselines()
        if sleep_metrics.get("sleep_recorded") is True:
            baselines = update_baselines(baselines, sleep_metrics, today)
        baselines = update_baselines(baselines, activity_metrics, yesterday)
        save_baselines(baselines)
        logger.info("Updated correction-safe baselines")

        profile = load_profile()
        if profile:
            logger.info("Loaded shared user profile")

        fetch_status = {}
        required_endpoints = {"daily_sleep", "daily_readiness", "sleep"}
        for endpoint, status in oura_data.get("_fetch_status", {}).items():
            fetch_status[endpoint] = {
                **status,
                "required": endpoint in required_endpoints,
            }
        if no_valid_sleep:
            fetch_status["sleep"] = {
                **fetch_status.get("sleep", {"ok": True, "count": 0}),
                "required": True,
                "stale": sleep_metrics.get("sleep_recorded") is True,
            }

        packet = build_daily_insight_packet(
            today,
            metrics,
            detailed_sleep,
            load_historical_metrics(BRIEF_HISTORY_DAYS),
            baselines,
            profile=profile,
            feedback_summary=summarize_feedback(),
            fetch_status=fetch_status,
            generated_at=now_nyc(),
            metric_provenance={
                **{
                    key: {"source_date": today, "source": "sleep"}
                    for key in sleep_metrics
                },
                **{
                    key: {"source_date": yesterday, "source": "activity"}
                    for key in activity_metrics
                },
            },
        )
        logger.info("Generating decision-first daily card with %s", CLAUDE_MODEL)
        generated = generate_daily_card(anthropic_key, packet)
        brief_content = generated.text

        brief_file = BRIEFS_DIR / f"{today}.md"
        atomic_write_text(brief_file, brief_content)
        logger.info(f"Saved brief to {brief_file}")

        card_entry = save_daily_card(
            today,
            generated.card,
            brief_content,
            packet,
            model=generated.model,
            stop_reason=generated.stop_reason,
            fallback_used=generated.fallback_used,
        )

        # Prune old data
        prune_old_data()

        # Commit volume changes
        volume.commit()

        header_date = now_nyc().strftime("%b %-d")  # e.g. "Apr 19"
        telegram_message = (
            f"☀️ *Morning Brief · {header_date}*\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"{brief_content}"
        )

        delivery_key = f"telegram-delivery:{card_entry['id']}"
        if not coordination.put(
            delivery_key,
            {
                "state": "attempting",
                "run_id": run_id,
                "started_at": now_nyc().isoformat(),
            },
            skip_if_exists=True,
        ):
            raise RuntimeError("Telegram delivery was already attempted for this card")

        message_id = send_telegram_message(
            telegram_message,
            bot_token,
            chat_id,
            reply_markup=build_feedback_keyboard(card_entry["id"]),
            raise_on_uncertain=True,
        )
        if message_id is None:
            coordination.put(
                delivery_key,
                {
                    "state": "failed",
                    "run_id": run_id,
                    "finished_at": now_nyc().isoformat(),
                },
            )
            record_delivery(card_entry["id"], "failed", detail="sendMessage failed")
            volume.commit()
            raise RuntimeError("Telegram delivery failed after the card was saved")

        # Telegram offers no idempotency key. Record an at-most-once marker
        # immediately after acceptance so a crash before the Volume commit
        # cannot cause an automatic duplicate on the next scheduled run.
        coordination.put(
            delivery_key,
            {
                "state": "sent",
                "run_id": run_id,
                "message_id": message_id,
                "finished_at": now_nyc().isoformat(),
            },
        )
        record_delivery(card_entry["id"], "sent", message_id=message_id)
        record_run_event(
            run_id,
            "completed",
            date=today,
            card_id=card_entry["id"],
            model=generated.model,
            fallback_used=generated.fallback_used,
            data_quality=packet["freshness"]["data_quality"],
        )
        volume.commit()
        logger.info("Brief sent to Telegram message_id=%s", message_id)

        return {
            "status": "success",
            "date": today,
            "card_id": card_entry["id"],
            "run_id": run_id,
            "model": generated.model,
            "data_quality": packet["freshness"]["data_quality"],
        }

    except Exception as e:
        error_msg = f"Morning brief failed: {str(e)}"
        logger.error("run=%s %s", run_id, error_msg)
        try:
            record_run_event(
                run_id,
                "failed",
                date=today,
                error_type=type(e).__name__,
                detail=str(e)[:300],
            )
            volume.commit()
        except Exception:
            logger.exception("Could not persist failed run event")

        if bot_token and chat_id and not isinstance(e, TelegramDeliveryUncertain):
            if isinstance(e, OuraAuthenticationError):
                user_error = (
                    "*Oura sync needs attention*\n\n"
                    "The Oura credential was rejected. No brief or baseline update was made."
                )
            elif isinstance(e, OuraAPIError):
                user_error = (
                    "*Oura sync delayed*\n\n"
                    "Oura could not be reached reliably. No health conclusion was generated."
                )
            else:
                user_error = "*Morning brief failed*\n\nNo health conclusion was sent."
            send_telegram(user_error, bot_token, chat_id)

        raise
    finally:
        try:
            release_daily_lock(today, run_id, coordination=coordination)
            volume.commit()
        except Exception:
            logger.exception("Could not release morning-brief lock run=%s", run_id)


# ============================================================================
# MANUAL TRIGGERS & UTILITIES
# ============================================================================

@app.function(
    secrets=[
        modal.Secret.from_name("anthropic"),
        modal.Secret.from_name("oura"),
        modal.Secret.from_name("telegram"),
    ],
    volumes={"/data": volume},
    timeout=300,
)
def run_now(force: bool = True):
    """Manual trigger for testing."""
    return morning_brief.local(force=force)


@app.function(
    secrets=[
        modal.Secret.from_name("anthropic"),
        modal.Secret.from_name("oura"),
        modal.Secret.from_name("telegram"),
    ],
    volumes={"/data": volume},
    timeout=300,
    max_containers=1,
)
def regenerate_brief_request(update_id: str):
    """Run at most one forced regeneration for a Telegram update."""
    key = f"regen-request:{update_id}"
    if not coordination.put(
        key,
        {"state": "running", "started_at": now_nyc().isoformat()},
        skip_if_exists=True,
    ):
        return {"status": "already_dispatched", "update_id": update_id}
    try:
        result = morning_brief.local(force=True)
        coordination.put(
            key,
            {
                "state": "completed",
                "finished_at": now_nyc().isoformat(),
                "run_id": result.get("run_id"),
            },
        )
        return result
    except Exception:
        coordination.put(
            key,
            {"state": "failed", "finished_at": now_nyc().isoformat()},
        )
        raise


@app.function(
    secrets=[modal.Secret.from_name("telegram"), modal.Secret.from_name("anthropic")],
    volumes={"/data": volume},
    max_containers=1,
)
def log_intervention(raw_text: str):
    """Log an intervention for correlation tracking."""
    _reload_volume()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    cleaned_text = clean_intervention_with_claude(anthropic_key, raw_text)
    entry = save_intervention_raw(raw_text, cleaned_text)
    volume.commit()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        send_telegram(f"✓ Logged: {cleaned_text}", bot_token, chat_id)

    logger.info("Logged intervention event")
    return entry


@app.function(
    secrets=[
        modal.Secret.from_name("telegram"),
        modal.Secret.from_name("anthropic"),
    ],
    volumes={"/data": volume},
    timeout=300,
    max_containers=1,
)
def process_chat_message(text: str, image_data: bytes = None):
    """
    Process a chat message asynchronously with streaming to Telegram.

    The bot posts an initial placeholder message, then edits it incrementally
    as Claude produces tokens. Spawned from the webhook to avoid Telegram's
    60s timeout. Supports photo messages via image_data.
    """
    _reload_volume()
    ensure_directories()

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    streamer = TelegramStreamer(bot_token, chat_id)
    streamer.start("💭 Thinking...")

    try:
        handle_message_with_agent(
            anthropic_key,
            text or "",
            streamer=streamer,
            image_data=image_data,
        )
        volume.commit()
    except Exception as e:
        logger.error(f"Chat processing error: {e}")
        streamer.finalize("Sorry, I encountered an error processing your message.")


@app.function(volumes={"/data": volume})
def reset_baselines(confirm: str = ""):
    """Reset baselines only with an explicit destructive-operation phrase."""
    if confirm != "RESET_BASELINES":
        logger.warning("Refused baseline reset without explicit confirmation")
        return {
            "status": "refused",
            "message": "Pass confirm=RESET_BASELINES to perform this destructive reset.",
        }
    ensure_directories()
    baselines = get_default_baselines()
    save_baselines(baselines)
    volume.commit()
    logger.info("Baselines reset to defaults")
    return {"status": "reset", "baselines": baselines}


@app.function(volumes={"/data": volume})
def clear_today_interventions():
    """Soft-clear today's interventions, retaining an undo snapshot."""
    ensure_directories()
    today = now_nyc().strftime("%Y-%m-%d")
    result = soft_clear_interventions(today)
    if result["status"] == "cleared":
        volume.commit()
        logger.info("Soft-cleared interventions for %s", today)
    else:
        logger.info(f"No interventions file for {today}")
    return result


@app.function(secrets=[modal.Secret.from_name("oura")])
def debug_workouts(date: str = None, days_back: int = 7):
    """Debug: Check Oura API for workouts."""
    import requests

    if date is None:
        date = now_nyc().strftime("%Y-%m-%d")

    token = os.environ.get("OURA_ACCESS_TOKEN")

    end_dt = now_nyc()
    start_dt = end_dt - timedelta(days=days_back)
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(f"Fetching workouts from {start_date} to {end_date} (exclusive) from Oura API...")

    url = f"{OURA_API_BASE}/workout"
    params = {"start_date": start_date, "end_date": end_date}
    logger.info(f"URL: {url}")
    logger.info(f"Params: {params}")

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30
    )
    logger.info(f"Response status: {response.status_code}")
    response.raise_for_status()
    data = response.json()

    workouts = data.get("data", [])
    logger.info(f"Found {len(workouts)} workout(s) in range")

    for w in workouts:
        logger.info(f"  - {w.get('day')}: {w.get('activity')}, {w.get('start_datetime')} to {w.get('end_datetime')}")
        logger.info(f"    calories={w.get('calories')}, intensity={w.get('intensity')}, source={w.get('source')}")

    return data


@app.function(volumes={"/data": volume})
def view_history(days: int = 7):
    """View recent briefs and baselines."""
    ensure_directories()

    result = {"baselines": None, "recent_briefs": []}

    if (
        BASELINES_FILE.exists()
        or BASELINES_FILE.with_suffix(BASELINES_FILE.suffix + ".backup").exists()
    ):
        result["baselines"] = load_baselines()
        logger.info("\nCurrent Baselines:")
        for metric, values in result["baselines"].get("metrics", {}).items():
            mean = values.get("mean")
            std = values.get("std")
            if mean is not None and std is not None:
                logger.info(f"  {metric}: {mean:.1f} +/- {std:.1f}")

    logger.info(f"\nRecent Briefs (last {days} days):")
    briefs = sorted(BRIEFS_DIR.glob("*.md"), reverse=True)[:days]
    for brief in briefs:
        logger.info(f"  - {brief.name}")
        result["recent_briefs"].append(str(brief))

    return result


@app.function(
    secrets=[modal.Secret.from_name("oura")],
    volumes={"/data": volume},
    timeout=600,
)
def backfill_history(days: int = 90):
    """Backfill historical Oura data to bootstrap baselines."""
    import statistics
    import requests

    oura_token = os.environ.get("OURA_ACCESS_TOKEN")
    today = now_nyc()

    logger.info(f"Starting backfill for {days} days of history...")
    ensure_directories()

    start_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(f"Fetching data from {start_date} to {end_date} (exclusive)")

    all_daily_sleep = {}
    all_daily_readiness = {}
    all_sleep = {}

    # Fetch daily_sleep
    logger.info("\n1. Fetching daily_sleep...")
    try:
        result = fetch_oura_data(oura_token, "daily_sleep", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                all_daily_sleep[day] = item
        logger.info(f"   Got {len(all_daily_sleep)} days")
    except Exception as e:
        logger.info(f"   Error: {e}")

    # Fetch daily_readiness
    logger.info("2. Fetching daily_readiness...")
    try:
        result = fetch_oura_data(oura_token, "daily_readiness", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                all_daily_readiness[day] = item
        logger.info(f"   Got {len(all_daily_readiness)} days")
    except Exception as e:
        logger.info(f"   Error: {e}")

    # Fetch sleep (detailed)
    logger.info("3. Fetching detailed sleep...")
    sleep_start = (today - timedelta(days=days+1)).strftime("%Y-%m-%d")
    try:
        result = fetch_oura_data(oura_token, "sleep", sleep_start, end_date)
        for item in result.get("data", []):
            bedtime_end = item.get("bedtime_end", "")
            if bedtime_end:
                wake_date = bedtime_end.split("T")[0]
                if wake_date not in all_sleep or bedtime_end > all_sleep[wake_date].get("bedtime_end", ""):
                    all_sleep[wake_date] = item
        logger.info(f"   Got {len(all_sleep)} days")
    except Exception as e:
        logger.info(f"   Error: {e}")

    # Fetch daily_stress
    all_daily_stress = {}
    logger.info("4. Fetching daily_stress...")
    try:
        result = fetch_oura_data(oura_token, "daily_stress", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                all_daily_stress[day] = item
        logger.info(f"   Got {len(all_daily_stress)} days")
    except Exception as e:
        logger.info(f"   Error (stress may not be available): {e}")

    # Fetch workouts
    all_workouts = {}
    logger.info("5. Fetching workouts...")
    try:
        result = fetch_oura_data(oura_token, "workout", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                if day not in all_workouts:
                    all_workouts[day] = []
                all_workouts[day].append(item)
        logger.info(f"   Got workouts for {len(all_workouts)} days")
    except Exception as e:
        logger.info(f"   Error: {e}")

    # Fetch daytime heart rate
    all_daytime_hr = {}
    logger.info("6. Fetching daytime heart rate...")
    hr_success_count = 0
    for i in range(min(days, RAW_WINDOW_DAYS)):
        date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            readings = get_oura_heartrate(oura_token, date)
            if readings:
                all_daytime_hr[date] = readings
                hr_success_count += 1
        except Exception:
            pass
        if i > 0 and i % 7 == 0:
            logger.info(f"   Processed {i}/{min(days, RAW_WINDOW_DAYS)} days...")
    logger.info(f"   Got heart rate data for {hr_success_count} days")

    # Process each day
    logger.info("\n7. Processing daily metrics...")
    all_metrics = {}

    for i in range(days):
        date = (today - timedelta(days=i)).strftime("%Y-%m-%d")

        oura_data = {
            "daily_sleep": [all_daily_sleep[date]] if date in all_daily_sleep else [],
            "daily_readiness": [all_daily_readiness[date]] if date in all_daily_readiness else [],
            "sleep": [all_sleep[date]] if date in all_sleep else [],
            "daily_stress": [all_daily_stress[date]] if date in all_daily_stress else [],
            "workouts": all_workouts.get(date, []),
            "daytime_hr": all_daytime_hr.get(date, []),
        }

        metrics = extract_metrics(oura_data)

        if metrics and any(v is not None for v in metrics.values()):
            all_metrics[date] = metrics

            # Save ALL metrics to disk (no longer limited to RAW_WINDOW_DAYS)
            detailed_sleep_data = extract_detailed_sleep(oura_data)
            detailed_workouts_data = extract_detailed_workouts(oura_data)
            save_daily_metrics(date, metrics, detailed_sleep_data, detailed_workouts_data)

    logger.info(f"   Extracted metrics for {len(all_metrics)} days")

    # Build baselines
    logger.info("\n8. Building baselines...")

    sorted_dates = sorted(all_metrics.keys())

    baselines = {
        "last_updated": now_nyc().isoformat(),
        "dates": [],
        "data_points": 0,
        "window_days": BASELINE_WINDOW_DAYS,
        "metrics": {
            "sleep_score": {"mean": 0, "std": 0, "values": []},
            "hrv": {"mean": 0, "std": 0, "values": []},
            "deep_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "light_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "rem_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "sleep_efficiency": {"mean": 0, "std": 0, "values": []},
            "latency_minutes": {"mean": 0, "std": 0, "values": []},
            "total_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "resting_hr": {"mean": 0, "std": 0, "values": []},
            "daytime_hr_avg": {"mean": 0, "std": 0, "values": []},
            "readiness": {"mean": 0, "std": 0, "values": []},
            "stress_high": {"mean": 0, "std": 0, "values": []},
            "recovery_high": {"mean": 0, "std": 0, "values": []},
            "workout_minutes": {"mean": 0, "std": 0, "values": []},
            "workout_calories": {"mean": 0, "std": 0, "values": []},
        }
    }

    for date in sorted_dates:
        metrics = all_metrics[date]
        baselines["dates"].append(date)
        baselines["dates"] = baselines["dates"][-BASELINE_WINDOW_DAYS:]

        for metric, value in metrics.items():
            if metric in baselines["metrics"] and value is not None:
                values = baselines["metrics"][metric]["values"]
                values.append(value)
                values = values[-BASELINE_WINDOW_DAYS:]
                baselines["metrics"][metric]["values"] = values

    for metric, data in baselines["metrics"].items():
        values = data["values"]
        if len(values) >= 2:
            data["mean"] = round(statistics.mean(values), 1)
            data["std"] = round(statistics.stdev(values), 1)
        elif len(values) == 1:
            data["mean"] = values[0]
            data["std"] = 0

    baselines["data_points"] = len(baselines["dates"])

    save_baselines(baselines)

    logger.info(f"\n9. Baselines saved with {baselines['data_points']} data points")
    logger.info("\nBaseline summary:")
    for metric, data in baselines["metrics"].items():
        if data["values"]:
            logger.info(f"   {metric}: {data['mean']:.1f} ± {data['std']:.1f} (n={len(data['values'])})")

    volume.commit()

    logger.info("\nBackfill complete!")
    return {
        "days_processed": len(all_metrics),
        "baseline_data_points": baselines["data_points"],
        "metrics_files_saved": len(all_metrics)
    }


# ============================================================================
# TELEGRAM BOT WEBHOOK
# ============================================================================

from fastapi import Request
from fastapi.responses import JSONResponse


def parse_bot_command(text: str) -> tuple:
    """Parse one exact Telegram command token and its remaining argument."""
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None, ""
    first, _, argument = stripped.partition(" ")
    command = first.split("@", 1)[0].lower()
    return command, argument.strip()


def _profile_summary(profile: dict) -> str:
    if not profile:
        return "No personal profile is configured."
    user = profile.get("user", {})
    preferences = profile.get("preferences", {})
    goals = preferences.get("primary_goals", [])
    lines = ["*Personal context*"]
    if user.get("timezone"):
        lines.append(f"• Timezone: {user['timezone']}")
    if user.get("target_bedtime") or user.get("target_wake"):
        lines.append(
            "• Target sleep: "
            f"{user.get('target_bedtime', '?')}–{user.get('target_wake', '?')}"
        )
    if goals:
        lines.append("• Goals: " + ", ".join(str(goal).replace("_", " ") for goal in goals))
    style = preferences.get("communication_style")
    if style:
        lines.append(f"• Style: {style}")
    lines.append("_This context is shared by daily cards and chat._")
    return "\n".join(lines)


@app.function(
    secrets=[
        modal.Secret.from_name("telegram"),
        modal.Secret.from_name("anthropic"),
        modal.Secret.from_name("oura"),
    ],
    volumes={"/data": volume},
    timeout=300,
    max_containers=1,
)
@modal.fastapi_endpoint(method="POST")
async def telegram_webhook(request: Request):
    """Telegram webhook endpoint for receiving messages."""
    # Validate webhook secret - MANDATORY for security
    webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not webhook_secret:
        logger.error("TELEGRAM_WEBHOOK_SECRET not configured - rejecting request")
        return JSONResponse({"ok": False, "error": "server misconfigured"}, status_code=500)

    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if received_secret != webhook_secret:
        logger.warning("Webhook auth failed: invalid secret token")
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    body = await request.json()
    update_id = body.get("update_id")

    callback = body.get("callback_query")
    if callback:
        callback_id = str(callback.get("id", ""))
        callback_chat_id = str(
            callback.get("message", {}).get("chat", {}).get("id", "")
        )
        callback_user_id = str(callback.get("from", {}).get("id", ""))
        if chat_id not in {callback_chat_id, callback_user_id}:
            return {"ok": True}

        _reload_volume()
        ensure_directories()
        claim_id = claim_update(update_id, coordination=coordination)
        if claim_id is None:
            answer_callback_query(callback_id, bot_token, "Already recorded.")
            return {"ok": True}

        try:
            data = str(callback.get("data", ""))
            parts = data.split(":")
            if len(parts) != 3 or parts[0] != "fb":
                answered = answer_callback_query(
                    callback_id,
                    bot_token,
                    "That action is no longer available.",
                )
            else:
                _, card_id, feedback = parts
                try:
                    record_feedback(card_id, feedback, update_id=update_id)
                    labels = {
                        "accurate": "Accuracy noted.",
                        "useful": "Usefulness noted.",
                        "not_for_me": "Got it — I'll suppress repeats like this.",
                        "doing_it": "Marked as doing it.",
                        "skipped": "Marked as skipped.",
                    }
                    answered = answer_callback_query(
                        callback_id,
                        bot_token,
                        labels.get(feedback, "Feedback saved."),
                    )
                except (KeyError, ValueError):
                    logger.warning("Invalid feedback callback: %s", data)
                    answered = answer_callback_query(
                        callback_id,
                        bot_token,
                        "That card or action is no longer available.",
                    )
            if not answered:
                raise RuntimeError("Telegram callback acknowledgement failed")

            # Make valid feedback durable before marking the Telegram update
            # complete. An exception before completion leaves a retryable
            # failed claim instead of silently discarding the action.
            volume.commit()
            if not complete_update(
                update_id,
                claim_id,
                coordination=coordination,
            ):
                raise RuntimeError("Telegram callback ownership was lost")
            volume.commit()
            return {"ok": True}
        except Exception:
            fail_update(update_id, claim_id, coordination=coordination)
            raise

    message = body.get("message", {})
    text = message.get("text", "")
    sender_chat_id = str(message.get("chat", {}).get("id", ""))

    if sender_chat_id != chat_id:
        return {"ok": True}

    _reload_volume()
    ensure_directories()
    claim_id = claim_update(update_id, coordination=coordination)
    if claim_id is None:
        return {"ok": True}

    try:
        response_text = None

        # Check for photo message — route through the streaming agent like text
        photo = message.get("photo")
        if photo:
            file_id = photo[-1]["file_id"]
            caption = message.get("caption", "")
            photo_download_failed = False
            try:
                image_data = download_telegram_photo(bot_token, file_id)
            except Exception as e:
                # requests exceptions can include the token-bearing Telegram
                # URL, so log only the exception class here.
                logger.error("Photo download error (%s)", type(e).__name__)
                photo_download_failed = True

            # Keep recovery outside the exception handler so a later failure
            # cannot retain the token-bearing Requests exception as context.
            if photo_download_failed:
                delivered = send_telegram(
                    "Sorry, I couldn't download that photo. "
                    "Try sending a text description instead.",
                    bot_token,
                    chat_id,
                )
                if not delivered:
                    raise RuntimeError("Telegram photo error reply failed")
                if not complete_update(
                    update_id,
                    claim_id,
                    coordination=coordination,
                ):
                    raise RuntimeError("Telegram update ownership was lost")
                volume.commit()
                return {"ok": True}
            process_chat_message.spawn(caption, image_data)
            if not complete_update(
                update_id,
                claim_id,
                coordination=coordination,
            ):
                raise RuntimeError("Telegram update ownership was lost")
            volume.commit()
            return {"ok": True}

        if not text.strip():
            if not complete_update(
                update_id,
                claim_id,
                coordination=coordination,
            ):
                raise RuntimeError("Telegram update ownership was lost")
            volume.commit()
            return {"ok": True}

        command, argument = parse_bot_command(text)

        if command == "/log":
            raw_text = argument
            if raw_text:
                cleaned_text = clean_intervention_with_claude(anthropic_key, raw_text)
                entry = save_intervention_raw(
                    raw_text,
                    cleaned_text,
                    source_update_id=update_id,
                )
                response_text = f"✓ Logged: {entry.get('cleaned', cleaned_text)}"
            else:
                response_text = "Usage: /log <intervention>\nExamples:\n  /log magnesium 400mg\n  /log sauna 20min\n  /log 2 drinks of wine"

        elif command == "/status":
            entries = get_today_interventions()
            if entries:
                lines = ["Today's interventions:"]
                for e in entries:
                    display_text = e.get(
                        "cleaned", e.get("raw", e.get("name", "unknown"))
                    )
                    lines.append(f"  • {display_text}")
                response_text = "\n".join(lines)
            else:
                response_text = "No interventions logged today."

        elif command == "/brief":
            response_text = get_latest_brief()

        elif command == "/regen-brief":
            # Spawn async - return immediately to avoid Telegram webhook timeout/retry
            regenerate_brief_request.spawn(str(update_id))
            response_text = (
                "⏳ Regenerating morning brief... This may take a minute."
            )

        elif command == "/clear":
            today = now_nyc().strftime("%Y-%m-%d")
            if argument.lower() != "confirm":
                response_text = (
                    "This will hide today's intervention log but keep an undo snapshot.\n"
                    "Send `/clear confirm` to continue."
                )
            else:
                result = soft_clear_interventions(
                    today,
                    source_update_id=update_id,
                )
                if result["status"] == "cleared":
                    response_text = (
                        f"Cleared {result['cleared_count']} intervention(s) for "
                        f"{today}. Send /undo to restore them."
                    )
                else:
                    response_text = f"No interventions to clear for {today}."

        elif command == "/undo":
            result = undo_clear_interventions(source_update_id=update_id)
            if result["status"] == "restored":
                response_text = (
                    f"Restored {result['restored_count']} intervention(s) for "
                    f"{result['date']}."
                )
            else:
                response_text = "There is no intervention clear to undo."

        elif command == "/profile":
            response_text = _profile_summary(load_profile())

        elif command == "/help":
            response_text = """Commands:
/status - Today's interventions
/brief - Latest morning brief
/regen-brief - Regenerate today's brief
/profile - Show shared personal context
/clear - Ask to clear today's interventions
/undo - Restore the last clear
/help - Show this

Log interventions:
  "took 2 magnesium"
  "20 min sauna"
  [send a photo]

Ask questions:
  "How did I sleep last week?"
  "What's my HRV trend?"
  "Compare today to my baseline" """

        elif command is not None:
            response_text = "Unknown command. Try /help"

        else:
            # Use agent with tools for all messages. Spawn async to avoid the
            # Telegram webhook timeout/retry loop.
            process_chat_message.spawn(text)
            response_text = None

        # Persist any intervention/profile state before confirming it to the
        # user or marking this Telegram update complete.
        volume.commit()
        if response_text:
            delivered = send_telegram(response_text, bot_token, chat_id)
            if not delivered:
                raise RuntimeError("Telegram command reply failed")

        if not complete_update(
            update_id,
            claim_id,
            coordination=coordination,
        ):
            raise RuntimeError("Telegram update ownership was lost")
        volume.commit()
        return {"ok": True}
    except Exception:
        fail_update(update_id, claim_id, coordination=coordination)
        raise


@app.local_entrypoint()
def main():
    """CLI entrypoint for manual runs."""
    logger.info("Triggering morning brief...")
    result = run_now.remote()
    logger.info(
        "Result status=%s date=%s card_id=%s run_id=%s model=%s data_quality=%s",
        result.get("status"),
        result.get("date"),
        result.get("card_id"),
        result.get("run_id"),
        result.get("model"),
        result.get("data_quality"),
    )
