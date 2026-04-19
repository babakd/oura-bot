"""
Claude AI handlers for briefs and intervention processing.
"""

import json
import base64

from oura_agent.config import CLAUDE_MODEL, logger
from oura_agent.prompts import SYSTEM_PROMPT
from oura_agent.telegram.client import _detect_image_mime_type


def _brief_system_blocks() -> list:
    """Cached system prompt block for the morning brief."""
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


# Server-side code execution tool — Claude can run Python to compute z-scores,
# correlations, rolling averages etc. rather than approximating numerically.
BRIEF_TOOLS = [
    {"type": "code_execution_20250825", "name": "code_execution"},
]


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
    """Use Claude Opus to generate the morning brief with verbose context."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

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
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=_brief_system_blocks(),
        tools=BRIEF_TOOLS,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    # With code_execution, Claude may emit multiple text blocks interleaved with
    # server_tool_use and bash_code_execution_tool_result blocks. Concatenate
    # every text block in order so the full narrative + final brief survives.
    text_parts = [b.text for b in response.content if b.type == "text"]
    if text_parts:
        return "\n\n".join(t for t in text_parts if t.strip())
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
