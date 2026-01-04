"""
Claude AI handlers for briefs, chat, and intervention processing.
"""

import json
import re
import base64

from oura_agent.config import CLAUDE_MODEL, logger
from oura_agent.prompts import SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT
from oura_agent.storage.baselines import load_baselines
from oura_agent.storage.interventions import save_intervention_raw, get_today_interventions
from oura_agent.storage.metrics import load_historical_metrics, load_recent_briefs
from oura_agent.storage.conversations import load_conversation_history, save_conversation_message
from oura_agent.telegram.client import _detect_image_mime_type


def generate_brief_with_claude(
    api_key: str,
    today: str,
    metrics: dict,
    detailed_sleep: dict,
    detailed_workouts: list,
    baselines: dict,
    historical_metrics: list,
    historical_interventions: dict,
    recent_briefs: list
) -> str:
    """Use Claude Opus 4.5 to generate the morning brief with verbose context."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    # Build comprehensive context for Claude
    user_prompt = f"""Generate my morning optimization brief for {today}.

═══════════════════════════════════════════════════════════════════════════════
LAST NIGHT'S DETAILED SLEEP DATA
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(detailed_sleep, indent=2)}
```

Key observations from the data:
- Bedtime: {detailed_sleep.get('bedtime_start', 'N/A')} → {detailed_sleep.get('bedtime_end', 'N/A')}
- Time in bed: {detailed_sleep.get('time_in_bed_minutes', 'N/A')} min, actual sleep: {detailed_sleep.get('total_sleep_minutes', 'N/A')} min
- Sleep stages: Deep {detailed_sleep.get('deep_sleep_pct', 'N/A')}%, Light {detailed_sleep.get('light_sleep_pct', 'N/A')}%, REM {detailed_sleep.get('rem_sleep_pct', 'N/A')}%
- HR trend: first third avg {detailed_sleep.get('hr_first_third_avg', 'N/A')} → last third avg {detailed_sleep.get('hr_last_third_avg', 'N/A')} bpm
- HRV trend: first third avg {detailed_sleep.get('hrv_first_third_avg', 'N/A')} → last third avg {detailed_sleep.get('hrv_last_third_avg', 'N/A')} ms
- Phase transitions (sleep fragmentation indicator): {detailed_sleep.get('phase_transitions', 'N/A')}

═══════════════════════════════════════════════════════════════════════════════
YESTERDAY'S WORKOUTS
═══════════════════════════════════════════════════════════════════════════════

"""

    if detailed_workouts:
        user_prompt += f"```json\n{json.dumps(detailed_workouts, indent=2)}\n```\n\n"
        total_mins = sum(w.get('duration_minutes', 0) for w in detailed_workouts)
        total_cals = sum(w.get('calories', 0) or 0 for w in detailed_workouts)
        activities = [w.get('activity') for w in detailed_workouts if w.get('activity')]
        user_prompt += f"Summary: {len(detailed_workouts)} workout(s), {total_mins} total minutes, {total_cals} calories\n"
        user_prompt += f"Activities: {', '.join(activities)}\n"
        for i, w in enumerate(detailed_workouts, 1):
            intensity = w.get('intensity', 'unknown')
            label = f" ({w.get('label')})" if w.get('label') else ""
            user_prompt += f"  {i}. {w.get('activity')}{label}: {w.get('duration_minutes')}min, {intensity} intensity, {w.get('calories') or 0} cal\n"
    else:
        user_prompt += "No workouts recorded yesterday.\n"

    user_prompt += f"""
═══════════════════════════════════════════════════════════════════════════════
TODAY'S SUMMARY METRICS
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(metrics, indent=2)}
```

═══════════════════════════════════════════════════════════════════════════════
BASELINES (rolling 60-day averages, updated daily)
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(baselines.get('metrics', {}), indent=2)}
```
Data points in baseline: {baselines.get('data_points', 0)}
Dates covered: {baselines.get('dates', [])}

═══════════════════════════════════════════════════════════════════════════════
HISTORICAL METRICS (last 28 days)
═══════════════════════════════════════════════════════════════════════════════

"""

    if historical_metrics:
        for day_data in historical_metrics[:28]:
            date = day_data.get('date', 'unknown')
            summary = day_data.get('summary', {})
            line = f"\n{date}: Sleep={summary.get('sleep_score', '-')}, Readiness={summary.get('readiness', '-')}, HRV={summary.get('hrv', '-')}, Deep={summary.get('deep_sleep_minutes', '-')}min, RHR={summary.get('resting_hr', '-')}"
            if summary.get('stress_high') is not None:
                line += f", Stress={summary.get('stress_high')}min, Recovery={summary.get('recovery_high', '-')}min"
            if summary.get('workout_minutes'):
                line += f", Workout={summary.get('workout_minutes')}min/{summary.get('workout_calories', '-')}cal"
            if summary.get('daytime_hr_avg'):
                line += f", DayHR={summary.get('daytime_hr_avg')}bpm"
            user_prompt += line
    else:
        user_prompt += "No historical data available yet (building baseline)."

    user_prompt += """

═══════════════════════════════════════════════════════════════════════════════
INTERVENTIONS (last 28 days)
═══════════════════════════════════════════════════════════════════════════════

"""

    if historical_interventions:
        for date, data in sorted(historical_interventions.items(), reverse=True):
            entries = data.get("entries", [])
            for e in entries:
                display = e.get("cleaned", e.get("raw", "unknown"))
                user_prompt += f"{date}: {display}\n"
    else:
        user_prompt += "No interventions logged yet."

    user_prompt += """

═══════════════════════════════════════════════════════════════════════════════
RECENT BRIEFS (for continuity)
═══════════════════════════════════════════════════════════════════════════════

"""

    for brief in recent_briefs[:3]:
        user_prompt += f"\n### {brief['date']}\n{brief['content']}\n"

    if not recent_briefs:
        user_prompt += "No previous briefs available."

    user_prompt += """

═══════════════════════════════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════════════════════════════

1. Analyze last night's detailed sleep data - look at HR/HRV trends, sleep architecture, timing
2. Compare today's metrics against 60-day baselines (calculate z-scores where possible)
3. Look for patterns in the 28-day historical data
4. Correlate any interventions with outcomes (e.g., did alcohol correlate with poor sleep?)
5. Generate the brief in the exact format specified in your instructions

Be specific with numbers. Use status emojis: ✅ (normal), ⚠️ (notable deviation), 🔴 (significant concern).
"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        thinking={
            "type": "enabled",
            "budget_tokens": 10000
        },
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    # Extract text content (skip thinking blocks)
    for block in response.content:
        if block.type == "text":
            return block.text
    return response.content[-1].text


def clean_intervention_with_claude(api_key: str, raw_text: str) -> str:
    """Use Claude to clean/normalize intervention text."""
    import anthropic

    prompt = f"""Clean and normalize this health intervention log entry. Fix typos, remove filler words, standardize format. Keep it brief (under 10 words ideally).

Input: "{raw_text}"

Output only the cleaned text, nothing else."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip().strip('"')
    except Exception as e:
        logger.error(f"Error cleaning intervention: {e}")
        return raw_text


def build_chat_context(baselines: dict, metrics: list, interventions: list, briefs: list) -> str:
    """Build context string for chat with health data."""
    from oura_agent.utils import now_nyc

    lines = []

    # Add current date anchor
    today = now_nyc().strftime("%Y-%m-%d")
    lines.append(f"## Current Date: {today}")
    lines.append("")

    # Baselines summary
    if baselines.get("metrics"):
        lines.append("## Your Baselines (60-day rolling averages)")
        for metric, data in baselines["metrics"].items():
            if data.get("mean") is not None:
                mean = data["mean"]
                std = data.get("std", 0)
                lines.append(f"- {metric}: {mean:.1f} ± {std:.1f}")
        lines.append("")

    # Recent metrics
    if metrics:
        lines.append("## Recent Daily Metrics (last 7 days)")
        for day_data in metrics[:7]:
            date = day_data.get("date", "unknown")
            summary = day_data.get("summary", {})
            sleep = summary.get("sleep_score", "N/A")
            hrv = summary.get("hrv", "N/A")
            readiness = summary.get("readiness", "N/A")
            deep = summary.get("deep_sleep_minutes", "N/A")
            rhr = summary.get("resting_hr", "N/A")
            workout_mins = summary.get("workout_minutes")
            workout_cals = summary.get("workout_calories")
            workout_acts = summary.get("workout_activities", [])
            stress = summary.get("stress_high")
            recovery = summary.get("recovery_high")
            day_hr = summary.get("daytime_hr_avg")

            line = f"- {date}: Sleep {sleep}, HRV {hrv}, Readiness {readiness}, Deep {deep}min, RHR {rhr}"
            if workout_mins:
                acts_str = "/".join(workout_acts) if workout_acts else ""
                line += f", Workout {workout_mins}min/{workout_cals:.0f}cal ({acts_str})" if workout_cals else f", Workout {workout_mins}min ({acts_str})"
            if stress is not None:
                line += f", Stress {stress}min"
            if recovery is not None:
                line += f", Recovery {recovery}min"
            if day_hr:
                line += f", DayHR {day_hr}bpm"
            lines.append(line)
        lines.append("")

    # Today's interventions
    if interventions:
        lines.append("## Today's Interventions")
        for entry in interventions:
            cleaned = entry.get("cleaned", entry.get("raw", "unknown"))
            time = entry.get("time", "")
            lines.append(f"- {time}: {cleaned}")
        lines.append("")

    # Recent briefs
    if briefs:
        lines.append("## Recent Brief Highlights")
        for brief in briefs[-2:]:
            date = brief.get("date", "unknown")
            content = brief.get("content", "")
            if "*TL;DR*" in content:
                tldr_start = content.find("*TL;DR*")
                tldr_end = content.find("*", tldr_start + 7)
                if tldr_end == -1:
                    tldr_end = min(tldr_start + 200, len(content))
                tldr = content[tldr_start:tldr_end].strip()
                lines.append(f"- {date}: {tldr[:150]}...")
        lines.append("")

    return "\n".join(lines)


def handle_message(api_key: str, user_message: str) -> str:
    """Handle any user message - Claude decides if it's an intervention or question."""
    import anthropic
    from oura_agent.config import RAW_WINDOW_DAYS

    # Load all available data
    baselines = load_baselines()
    historical_metrics = load_historical_metrics(RAW_WINDOW_DAYS)
    today_interventions = get_today_interventions()
    recent_briefs = load_recent_briefs(3)
    conversation_history = load_conversation_history(10)

    # Build context
    context = build_chat_context(
        baselines, historical_metrics,
        today_interventions, recent_briefs
    )

    # Build messages with conversation history
    messages = []
    for msg in conversation_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    # Call Claude
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=500,
            system=CHAT_SYSTEM_PROMPT + "\n\n## Current Health Data\n" + context,
            messages=messages
        )

        assistant_response = response.content[0].text.strip()

        # Check if Claude identified this as an intervention
        log_match = re.match(r'^\[LOG:\s*(.+?)\]\s*\n?', assistant_response)
        if log_match:
            cleaned_intervention = log_match.group(1).strip()
            display_response = assistant_response[log_match.end():].strip()
            save_intervention_raw(user_message, cleaned_intervention)

            save_conversation_message("user", user_message)
            save_conversation_message("assistant", display_response)

            return display_response
        else:
            save_conversation_message("user", user_message)
            save_conversation_message("assistant", assistant_response)
            return assistant_response

    except Exception as e:
        logger.error(f"Message handling error: {e}")
        return "Sorry, I couldn't process that. Try again or rephrase."


def format_intervention_response(api_key: str, just_logged: str) -> str:
    """Use Claude to generate a natural acknowledgment with today's summary."""
    import anthropic

    entries = get_today_interventions()

    if not entries:
        return "Logged."

    # Build list of today's entries
    entry_lines = []
    for e in entries:
        time = e.get("time", "")
        raw = e.get("raw", e.get("name", "unknown"))
        entry_lines.append(f"- {time}: {raw}")

    prompt = f"""Acknowledge this intervention was logged. Then summarize today's interventions naturally in 1-2 sentences.

Just logged: "{just_logged}"

Today's entries:
{chr(10).join(entry_lines)}

Keep response under 3 lines. No emojis. Be concise."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        return f"Logged. ({len(entries)} today)"


def analyze_photo_with_claude(api_key: str, image_data: bytes, caption: str = "") -> str:
    """Use Claude Vision to analyze a photo and extract intervention details."""
    import anthropic

    media_type = _detect_image_mime_type(image_data)
    image_base64 = base64.b64encode(image_data).decode("utf-8")

    caption_context = f'\nUser caption: "{caption}"\n\nIMPORTANT: Include EVERYTHING mentioned in the caption, even if not visible in the image.' if caption else ""

    prompt = f"""Extract health interventions from BOTH the image AND the user's caption.

From the image, look for:
- Supplements/vitamins (name, dosage, quantity)
- Food/drinks (what it is, portion if visible)
- Exercise equipment or activity
- Wellness products (sauna, ice bath, etc.)
{caption_context}

Respond with a normalized intervention log entry listing ALL items.
If the caption mentions items not in the image, include them too.
Keep under 30 words. Use comma-separated format for multiple items.
If neither image nor caption shows a health intervention, respond with "NOT_AN_INTERVENTION".
Examples: "Creatine 2 capsules, Neuro-Mag 1 capsule", "Post-workout protein shake", "20 min sauna session"
"""

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=100,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_base64,
                    }
                },
                {"type": "text", "text": prompt}
            ]
        }]
    )
    return response.content[0].text.strip()
