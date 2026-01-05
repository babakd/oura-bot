"""
Oura Daily Optimization Agent
Runs daily via Modal cron, analyzes Oura data, sends brief to Telegram.
Uses Claude Opus 4.5 for analysis.

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
        "anthropic>=0.40.0",
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
    BASELINES_FILE,
    OURA_API_BASE,
    CLAUDE_MODEL,
    RAW_WINDOW_DAYS,
    BASELINE_WINDOW_DAYS,
    NYC_TZ,
    logger,
)

from oura_agent.utils import (
    now_nyc,
    ensure_directories,
    prune_old_data,
    get_latest_brief,
)

from oura_agent.prompts import (
    get_prompts_dir as _get_prompts_dir,
    load_prompt as _load_prompt,
    SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
)

from oura_agent.api.oura import (
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
    update_baselines,
)

from oura_agent.storage.interventions import (
    load_interventions,
    save_interventions,
    load_historical_interventions,
    save_intervention_raw,
    get_today_interventions,
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
    send_telegram,
    download_telegram_photo,
    _detect_image_mime_type,
    _send_telegram_chunk,
)

from oura_agent.claude.handlers import (
    generate_brief_with_claude,
    clean_intervention_with_claude,
    handle_message,
    format_intervention_response,
    analyze_photo_with_claude,
    build_chat_context,
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
    schedule=modal.Cron("0 15 * * *"),  # 15:00 UTC = 10:00 AM EST
)
def morning_brief():
    """
    Main agent function. Runs daily to:
    1. Fetch Oura data
    2. Analyze against baselines
    3. Generate recommendations with Claude Opus 4.5
    4. Send brief to Telegram
    """
    today = now_nyc().strftime("%Y-%m-%d")

    oura_token = os.environ.get("OURA_ACCESS_TOKEN")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    logger.info(f"Starting morning brief for {today}")

    try:
        ensure_directories()

        yesterday = (now_nyc() - timedelta(days=1)).strftime("%Y-%m-%d")

        # === FETCH AND SAVE SLEEP DATA (today's file) ===
        logger.info(f"Fetching sleep data for {today} (wake-date)...")
        sleep_data = get_oura_sleep_data(oura_token, today)

        sleep_metrics = extract_sleep_metrics(sleep_data)
        detailed_sleep = extract_detailed_sleep(sleep_data)
        logger.info(f"Extracted sleep metrics: {len(sleep_metrics)} fields, detailed: {len(detailed_sleep)} fields")

        # Check if we actually got sleep data for today (Oura may not have synced yet)
        if not sleep_data.get("sleep") or not detailed_sleep:
            error_msg = f"Sleep data for {today} not yet available. Oura may not have synced."
            logger.warning(error_msg)
            if bot_token and chat_id:
                send_telegram(f"⏳ *Morning Brief Delayed*\n\n{error_msg}\n\nPlease sync your Oura ring, then use /regen-brief to generate the brief.", bot_token, chat_id)
            return {"status": "delayed", "date": today, "reason": "sleep_data_not_available"}

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
        oura_data = {**sleep_data, **activity_data}
        raw_file = RAW_DIR / f"{today}.json"
        with open(raw_file, 'w') as f:
            json.dump(oura_data, f, indent=2)
        logger.info(f"Saved raw data to {raw_file}")

        # === PREPARE COMBINED METRICS ===
        metrics = {**sleep_metrics, **activity_metrics}
        logger.info(f"Combined metrics: {metrics}")

        if not metrics:
            raise ValueError("No metrics extracted from Oura data")

        # Load baselines (60-day aggregates)
        baselines = load_baselines()

        # Load historical context (28 days)
        historical_metrics = load_historical_metrics(RAW_WINDOW_DAYS)
        historical_interventions = load_historical_interventions(RAW_WINDOW_DAYS)
        recent_briefs = load_recent_briefs(3)

        logger.info(f"Loaded context: {len(historical_metrics)} days of metrics, {len(historical_interventions)} days with interventions")

        # Generate brief with Claude
        logger.info("Generating brief with Claude Opus 4.5...")
        brief_content = generate_brief_with_claude(
            anthropic_key,
            today,
            metrics,
            detailed_sleep,
            detailed_workouts,
            baselines,
            historical_metrics,
            historical_interventions,
            recent_briefs
        )

        # Save brief
        brief_file = BRIEFS_DIR / f"{today}.md"
        with open(brief_file, 'w') as f:
            f.write(brief_content)
        logger.info(f"Saved brief to {brief_file}")

        # Update baselines
        baselines = update_baselines(baselines, metrics, today)
        with open(BASELINES_FILE, 'w') as f:
            json.dump(baselines, f, indent=2)
        logger.info("Updated baselines")

        # Prune old data
        prune_old_data()

        # Commit volume changes
        volume.commit()

        # Send to Telegram
        telegram_message = f"*Morning Brief — {today}*\n\n{brief_content}"

        if send_telegram(telegram_message, bot_token, chat_id):
            logger.info("Brief sent to Telegram")
        else:
            logger.info("Warning: Failed to send to Telegram, but brief saved")

        return {"status": "success", "date": today, "metrics": metrics}

    except Exception as e:
        error_msg = f"Morning brief failed: {str(e)}"
        logger.error(f" {error_msg}")

        if bot_token and chat_id:
            send_telegram(f"*Oura Agent Error*\n\n`{error_msg}`", bot_token, chat_id)

        raise


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
def run_now():
    """Manual trigger for testing."""
    return morning_brief.local()


@app.function(
    secrets=[modal.Secret.from_name("telegram"), modal.Secret.from_name("anthropic")],
    volumes={"/data": volume},
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
        send_telegram(f"✓ {cleaned_text}", bot_token, chat_id)

    logger.info(f"Logged intervention: {cleaned_text}")
    return entry


@app.function(volumes={"/data": volume})
def reset_baselines():
    """Reset baselines to defaults."""
    ensure_directories()
    baselines = get_default_baselines()
    with open(BASELINES_FILE, 'w') as f:
        json.dump(baselines, f, indent=2)
    volume.commit()
    logger.info("Baselines reset to defaults")
    return baselines


@app.function(volumes={"/data": volume})
def clear_today_interventions():
    """Clear today's interventions."""
    ensure_directories()
    today = now_nyc().strftime("%Y-%m-%d")
    jsonl_file = INTERVENTIONS_DIR / f"{today}.jsonl"
    json_file = INTERVENTIONS_DIR / f"{today}.json"

    cleared = False
    if jsonl_file.exists():
        jsonl_file.unlink()
        cleared = True
    if json_file.exists():
        json_file.unlink()
        cleared = True

    if cleared:
        volume.commit()
        logger.info(f"Cleared interventions for {today}")
    else:
        logger.info(f"No interventions file for {today}")


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

    if BASELINES_FILE.exists():
        with open(BASELINES_FILE) as f:
            result["baselines"] = json.load(f)
        logger.info("\nCurrent Baselines:")
        for metric, values in result["baselines"].get("metrics", {}).items():
            logger.info(f"  {metric}: {values['mean']:.1f} +/- {values['std']:.1f}")

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

            if i < RAW_WINDOW_DAYS:
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

    with open(BASELINES_FILE, 'w') as f:
        json.dump(baselines, f, indent=2)

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
        "metrics_files_saved": min(len(all_metrics), RAW_WINDOW_DAYS)
    }


# ============================================================================
# TELEGRAM BOT WEBHOOK
# ============================================================================

from fastapi import Request
from fastapi.responses import JSONResponse


@app.function(
    secrets=[
        modal.Secret.from_name("telegram"),
        modal.Secret.from_name("anthropic"),
        modal.Secret.from_name("oura"),
    ],
    volumes={"/data": volume},
    timeout=300,
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
    message = body.get("message", {})
    text = message.get("text", "")
    sender_chat_id = str(message.get("chat", {}).get("id", ""))

    if sender_chat_id != chat_id:
        return {"ok": True}

    _reload_volume()
    ensure_directories()

    response_text = None

    # Check for photo message
    photo = message.get("photo")
    if photo:
        file_id = photo[-1]["file_id"]
        caption = message.get("caption", "")

        try:
            image_data = download_telegram_photo(bot_token, file_id)
            intervention = analyze_photo_with_claude(anthropic_key, image_data, caption)

            if intervention == "NOT_AN_INTERVENTION":
                response_text = "I couldn't identify a health intervention in that photo. Try adding a caption describing what it is."
            else:
                save_intervention_raw(intervention, intervention)
                volume.commit()
                response_text = format_intervention_response(anthropic_key, intervention)
        except Exception as e:
            logger.error(f"Photo processing error: {e}")
            response_text = "Sorry, I couldn't process that photo. Try sending a text description instead."

        if response_text:
            send_telegram(response_text, bot_token, chat_id)
        return {"ok": True}

    if not text.strip():
        return {"ok": True}

    if text.startswith("/log"):
        raw_text = text[4:].strip()
        if raw_text:
            cleaned_text = clean_intervention_with_claude(anthropic_key, raw_text)
            save_intervention_raw(raw_text, cleaned_text)
            volume.commit()
            response_text = format_intervention_response(anthropic_key, cleaned_text)
        else:
            response_text = "Usage: /log <intervention>\nExamples:\n  /log magnesium 400mg\n  /log sauna 20min\n  /log 2 drinks of wine"

    elif text.startswith("/status"):
        entries = get_today_interventions()
        if entries:
            lines = ["Today's interventions:"]
            for e in entries:
                display_text = e.get("cleaned", e.get("raw", e.get("name", "unknown")))
                lines.append(f"  • {display_text}")
            response_text = "\n".join(lines)
        else:
            response_text = "No interventions logged today."

    elif text.startswith("/brief"):
        response_text = get_latest_brief()

    elif text.startswith("/regen-brief"):
        send_telegram("⏳ Regenerating morning brief... This may take a minute.", bot_token, chat_id)
        try:
            # Spawn async - return immediately to avoid Telegram webhook timeout/retry
            morning_brief.spawn()
            response_text = None  # morning_brief will send the brief when done
        except Exception as e:
            response_text = f"❌ Failed to start brief regeneration: {str(e)}"

    elif text.startswith("/clear"):
        today = now_nyc().strftime("%Y-%m-%d")
        jsonl_file = INTERVENTIONS_DIR / f"{today}.jsonl"
        json_file = INTERVENTIONS_DIR / f"{today}.json"
        cleared = False
        if jsonl_file.exists():
            jsonl_file.unlink()
            cleared = True
        if json_file.exists():
            json_file.unlink()
            cleared = True
        if cleared:
            volume.commit()
            response_text = f"Cleared interventions for {today}"
        else:
            response_text = f"No interventions to clear for {today}"

    elif text.startswith("/help"):
        response_text = """Commands:
/status - Today's interventions
/brief - Latest morning brief
/regen-brief - Regenerate today's brief
/clear - Clear today's interventions
/help - Show this

Log interventions:
  "took 2 magnesium"
  "20 min sauna"
  [send a photo]

Ask questions:
  "How did I sleep last week?"
  "What's my HRV trend?"
  "Compare today to my baseline" """

    elif text.startswith("/"):
        response_text = "Unknown command. Try /help"

    else:
        response_text = handle_message(anthropic_key, text)
        volume.commit()

    if response_text:
        send_telegram(response_text, bot_token, chat_id)

    return {"ok": True}


@app.local_entrypoint()
def main():
    """CLI entrypoint for manual runs."""
    logger.info("Triggering morning brief...")
    result = run_now.remote()
    logger.info(f"Result: {result}")
