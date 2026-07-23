"""
Agent handler with tools for chat interactions.

Uses Claude with tool use to handle both interventions and questions
with a single code path. Supports text-only messages, image messages
(photo interventions), and streaming responses via a TelegramStreamer.
"""

import base64
import json
from typing import Any, Callable, Optional

import anthropic

from oura_agent.config import CLAUDE_MODEL, logger
from oura_agent.prompts import load_prompt
from oura_agent.storage.baselines import load_baselines
from oura_agent.storage.conversations import (
    load_conversation_history,
    save_conversation_message,
)
from oura_agent.storage.interventions import (
    load_historical_interventions,
    save_intervention_raw,
)
from oura_agent.storage.metrics import load_historical_metrics, load_recent_briefs
from oura_agent.telegram.client import _detect_image_mime_type
from oura_agent.utils import now_nyc


def _get_agent_prompt() -> str:
    """Load the static agent prompt (no date injection; date lives in its own block)."""
    try:
        return load_prompt("agent")
    except FileNotFoundError:
        logger.error("CRITICAL: agent.md prompt not found!")
        return ""


def get_brief_prompt() -> str:
    """Load the static morning-brief prompt."""
    try:
        return load_prompt("morning_brief")
    except FileNotFoundError:
        logger.error("CRITICAL: morning_brief.md prompt not found!")
        return ""


def build_brief_system_blocks() -> list:
    """Cached brief system prompt + uncached date block."""
    prompt = get_brief_prompt()
    current_date = now_nyc().strftime("%Y-%m-%d")
    return [
        {"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"Today is {current_date}."},
    ]


def _build_system_blocks(static_prompt: str) -> list:
    """Build system blocks: cached static prompt + dynamic date block.

    The static block carries a cache_control marker so Anthropic caches
    tools + static system across turns. The date block stays out of the
    cache breakpoint so it can change daily without invalidating the cache.
    """
    current_date = now_nyc().strftime("%Y-%m-%d")
    return [
        {
            "type": "text",
            "text": static_prompt,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"Today is {current_date}.",
        },
    ]


TOOLS = [
    {
        "name": "get_metrics",
        "description": (
            "Get daily health metrics for a date range. Returns summary data for each day "
            "(sleep_score, hrv, deep_sleep_minutes, readiness, resting_hr, stress_high, "
            "recovery_high, workout info). Set include_detailed=true to also return the full "
            "detailed_sleep blob (HR/HRV trends through the night, sleep stages, efficiency, "
            "latency) — only do this for narrow ranges, it's verbose."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD (inclusive)"},
                "include_detailed": {
                    "type": "boolean",
                    "description": "If true, include detailed_sleep per day. Default false.",
                },
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_interventions",
        "description": (
            "Get logged interventions (supplements, activities, food, etc.) for a date range. "
            "Use for correlation analysis or to check what was logged today (pass today's date "
            "for both start and end)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD (inclusive)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_baselines",
        "description": "Get 60-day rolling baseline statistics (mean, std) for all metrics. Use to compare current values against personal averages.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "log_intervention",
        "description": "Log an intervention the user reports (supplement, activity, food, etc). Use when the user tells you they did/took something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "raw_text": {"type": "string", "description": "Original user input exactly as written"},
                "normalized": {
                    "type": "string",
                    "description": "Cleaned/normalized version (e.g., 'Magnesium 400mg', 'Sauna 20 min')",
                },
            },
            "required": ["raw_text", "normalized"],
        },
    },
    {
        "name": "get_recent_briefs",
        "description": "Get recent morning briefs (last 3 days). Use when user asks about previous recommendations or analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days of briefs to retrieve (default 3, max 7)"},
            },
        },
    },
    {
        "name": "correlate_intervention",
        "description": (
            "Correlate a logged intervention (e.g. 'magnesium', 'alcohol', 'sauna') with a metric "
            "(e.g. 'sleep_score', 'hrv', 'readiness'). Returns mean/std of the metric on nights "
            "following days with the substance vs. nights following days without it, plus a delta. "
            "Use for questions like 'does X help my sleep?' or 'what's the impact of alcohol on HRV?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "substance": {
                    "type": "string",
                    "description": "Substring to match against logged interventions, case-insensitive (e.g. 'magnesium').",
                },
                "metric": {
                    "type": "string",
                    "description": "Metric key from the daily summary (e.g. 'sleep_score', 'hrv', 'deep_sleep_minutes', 'readiness', 'resting_hr').",
                },
                "days": {
                    "type": "integer",
                    "description": "Lookback window in days. Default 60.",
                },
            },
            "required": ["substance", "metric"],
        },
    },
]


# Brief gets the same data tools as chat PLUS the server-side code_execution
# tool for real statistics. Server tools are handled by Anthropic; we only
# dispatch client-side tools in the loop.
BRIEF_TOOLS = TOOLS + [
    {"type": "code_execution_20250825", "name": "code_execution"},
]


def execute_tool(name: str, tool_input: dict) -> str:
    """Execute a tool and return JSON result."""
    try:
        if name == "get_metrics":
            all_metrics = load_historical_metrics()
            start = tool_input["start_date"]
            end = tool_input["end_date"]
            include_detailed = bool(tool_input.get("include_detailed", False))
            filtered = [m for m in all_metrics if start <= m.get("date", "") <= end]
            result = []
            for m in filtered:
                entry = {"date": m["date"], "summary": m.get("summary", {})}
                if include_detailed and m.get("detailed_sleep"):
                    entry["detailed_sleep"] = m["detailed_sleep"]
                result.append(entry)
            return json.dumps(result, indent=2)

        elif name == "get_interventions":
            interventions = load_historical_interventions()
            start = tool_input["start_date"]
            end = tool_input["end_date"]
            filtered = {d: v for d, v in interventions.items() if start <= d <= end}
            return json.dumps(filtered, indent=2)

        elif name == "get_baselines":
            baselines = load_baselines()
            simplified = {
                "data_points": baselines.get("data_points", 0),
                "last_updated": baselines.get("last_updated"),
                "metrics": {
                    k: {"mean": v.get("mean"), "std": v.get("std")}
                    for k, v in baselines.get("metrics", {}).items()
                },
            }
            return json.dumps(simplified, indent=2)

        elif name == "log_intervention":
            raw = tool_input["raw_text"]
            normalized = tool_input["normalized"]
            entry = save_intervention_raw(raw, normalized)
            return json.dumps({
                "status": "logged",
                "time": entry.get("time"),
                "normalized": normalized,
            })

        elif name == "get_recent_briefs":
            days = min(tool_input.get("days", 3), 7)
            briefs = load_recent_briefs(days)
            return json.dumps(briefs, indent=2)

        elif name == "correlate_intervention":
            return json.dumps(
                _correlate_intervention(
                    substance=tool_input["substance"],
                    metric=tool_input["metric"],
                    days=int(tool_input.get("days", 60)),
                ),
                indent=2,
            )

        else:
            return json.dumps({"error": f"Unknown tool: {name}"})

    except Exception as e:
        logger.error(f"Tool execution error ({name}): {e}")
        return json.dumps({"error": str(e)})


def _correlate_intervention(substance: str, metric: str, days: int) -> dict:
    """Pair each day's metric with whether the substance was logged the prior day.

    Returns:
        {substance, metric, window_days,
         n_with, mean_with, std_with,
         n_without, mean_without, std_without,
         delta}

    `delta` = mean_with - mean_without. Positive means the substance is
    associated with a higher metric value. `mean_with`/`std_with` are None
    when n_with == 0 (and likewise for `without`).
    """
    import statistics
    from datetime import datetime, timedelta

    substance_lc = substance.lower().strip()
    interventions = load_historical_interventions()
    substance_dates = set()
    for date, day_data in interventions.items():
        for e in day_data.get("entries", []):
            text = (e.get("cleaned") or e.get("raw") or "").lower()
            if substance_lc and substance_lc in text:
                substance_dates.add(date)
                break

    today = now_nyc().date()
    cutoff = today - timedelta(days=days)

    with_values = []
    without_values = []
    for m in load_historical_metrics():
        date_str = m.get("date", "")
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff or d > today:
            continue
        val = m.get("summary", {}).get(metric)
        if val is None:
            continue
        prev_date = (d - timedelta(days=1)).strftime("%Y-%m-%d")
        if prev_date in substance_dates:
            with_values.append(val)
        else:
            without_values.append(val)

    def _stats(values):
        if not values:
            return 0, None, None
        return (
            len(values),
            round(statistics.mean(values), 2),
            round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
        )

    n_w, m_w, s_w = _stats(with_values)
    n_wo, m_wo, s_wo = _stats(without_values)
    delta = (m_w - m_wo) if (m_w is not None and m_wo is not None) else None

    return {
        "substance": substance,
        "metric": metric,
        "window_days": days,
        "n_with": n_w,
        "mean_with": m_w,
        "std_with": s_w,
        "n_without": n_wo,
        "mean_without": m_wo,
        "std_without": s_wo,
        "delta": round(delta, 2) if delta is not None else None,
    }


def _build_user_content(user_message: str, image_data: Optional[bytes]) -> Any:
    """Build the content for the user turn — plain string or list with image block."""
    if not image_data:
        return user_message
    mime = _detect_image_mime_type(image_data)
    b64 = base64.b64encode(image_data).decode("utf-8")
    text = user_message or "[image]"
    return [
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
        {"type": "text", "text": text},
    ]


def _conversation_text_for_history(user_message: str, image_data: Optional[bytes]) -> str:
    """What to persist to conversation history for this user turn."""
    if image_data and not user_message:
        return "[photo]"
    if image_data:
        return f"[photo] {user_message}"
    return user_message


def handle_message_with_agent(
    api_key: str,
    user_message: str,
    send_progress: Optional[Callable[[str], None]] = None,
    streamer: Optional[Any] = None,
    image_data: Optional[bytes] = None,
) -> str:
    """
    Handle any message using the agent with tools.

    Args:
        api_key: Anthropic API key.
        user_message: The user's text (may be empty when image_data is set).
        send_progress: Legacy callback invoked once with the agent's pre-tool
            "Looking at your data..." text. Used when `streamer` is not set.
        streamer: Optional TelegramStreamer; when present, the agent uses the
            streaming API and pushes text deltas into the streamer incrementally.
        image_data: Optional raw image bytes (from a Telegram photo). When set,
            the first user turn carries an image content block so the agent
            can decide via its tools whether it's an intervention worth logging.

    Returns:
        The agent's final response text.
    """
    client = anthropic.Anthropic(api_key=api_key)
    agent_prompt = _get_agent_prompt()

    if not agent_prompt:
        return "Sorry, I'm not properly configured. Please check the logs."

    system_blocks = _build_system_blocks(agent_prompt)

    history = load_conversation_history(limit=20, days_back=3)
    messages = [{"role": msg["role"], "content": msg["content"]} for msg in history]
    messages.append({"role": "user", "content": _build_user_content(user_message, image_data)})

    persisted_user_text = _conversation_text_for_history(user_message, image_data)

    max_iterations = 5
    progress_sent = False

    for iteration in range(max_iterations):
        try:
            if streamer is not None:
                response = _run_streaming_iteration(client, system_blocks, messages, streamer)
            else:
                response = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=16000,
                    thinking={"type": "adaptive"},
                    output_config={"effort": "high"},
                    system=system_blocks,
                    tools=TOOLS,
                    messages=messages,
                )
        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}")
            return "Sorry, I encountered an error. Please try again."

        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if not tool_uses:
            text_response = ""
            for block in response.content:
                if block.type == "text":
                    text_response = block.text
                    break

            if streamer is not None:
                streamer.finalize(text_response)

            save_conversation_message("user", persisted_user_text)
            save_conversation_message("assistant", text_response)
            return text_response

        if send_progress and streamer is None and not progress_sent:
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    send_progress(block.text)
                    progress_sent = True
                    break

        tool_results = []
        for tool_use in tool_uses:
            logger.info(f"Executing tool: {tool_use.name}")
            result = execute_tool(tool_use.name, tool_use.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result,
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    logger.warning(f"Agent exhausted {max_iterations} iterations")
    if streamer is not None:
        streamer.finalize("I wasn't able to complete the analysis. Please try rephrasing your question.")
    return "I wasn't able to complete the analysis. Please try rephrasing your question."


def run_brief_agent(
    api_key: str,
    today: str,
    metrics: dict,
    detailed_sleep: dict,
    detailed_workouts: list,
    profile: Optional[dict] = None,
    max_iterations: int = 10,
) -> str:
    """Run the morning brief as a tool-using agent.

    The brief receives only last night's metrics + yesterday's activity as a
    seed. It pulls historical context, baselines, interventions, correlations,
    and recent briefs via tools as needed, and computes statistics via the
    server-side code_execution tool. This replaces the old one-shot dump.
    """
    client = anthropic.Anthropic(api_key=api_key)
    system_blocks = build_brief_system_blocks()
    if not system_blocks[0]["text"]:
        return "ERROR: morning_brief.md prompt not available"

    seed = f"""Generate my morning optimization brief for {today}.

═══════════════════════════════════════════════════════════════════════════════
LAST NIGHT'S METRICS (wake-date {today})
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(metrics, indent=2)}
```

═══════════════════════════════════════════════════════════════════════════════
DETAILED SLEEP DATA
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(detailed_sleep, indent=2)}
```

═══════════════════════════════════════════════════════════════════════════════
YESTERDAY'S WORKOUTS
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(detailed_workouts, indent=2)}
```
"""

    if profile:
        seed += f"""
═══════════════════════════════════════════════════════════════════════════════
USER PROFILE
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(profile, indent=2)}
```
"""

    seed += """

Use your tools to pull any additional context you need — baselines, historical
metrics, interventions, correlations, recent briefs. Compute real statistics
via code_execution rather than approximating. Produce only the final brief as
your last text output; intermediate reasoning/narration is fine during tool
calls but the brief itself should read as a single coherent message.
"""

    messages = [{"role": "user", "content": seed}]
    last_response = None

    for iteration in range(max_iterations):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                system=system_blocks,
                tools=BRIEF_TOOLS,
                messages=messages,
            )
        except anthropic.APIError as e:
            logger.error(f"Brief API error: {e}")
            return f"ERROR generating brief: {e}"

        last_response = response
        # Only client-side tool uses block progress. Server tools
        # (code_execution) are of type 'server_tool_use' and are
        # handled transparently by the API.
        client_tool_uses = [b for b in response.content if b.type == "tool_use"]

        if not client_tool_uses:
            text_parts = [b.text for b in response.content if b.type == "text"]
            return "\n\n".join(t for t in text_parts if t.strip()) or (
                response.content[-1].text if response.content else ""
            )

        tool_results = []
        for tu in client_tool_uses:
            logger.info(f"Brief executing tool: {tu.name}")
            result = execute_tool(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    logger.error(f"Brief agent exhausted {max_iterations} iterations")
    if last_response is not None:
        text_parts = [b.text for b in last_response.content if b.type == "text"]
        if text_parts:
            return "\n\n".join(text_parts)
    return "ERROR: brief agent exhausted iterations without final output"


def _run_streaming_iteration(
    client: "anthropic.Anthropic",
    system_blocks: list,
    messages: list,
    streamer: Any,
):
    """One streaming pass: push text deltas into the streamer, return the final Message."""
    accumulated = ""
    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=system_blocks,
        tools=TOOLS,
        messages=messages,
    ) as stream:
        for chunk in stream.text_stream:
            if not chunk:
                continue
            accumulated += chunk
            streamer.append_delta(chunk)
        final_message = stream.get_final_message()
    # If the iteration produced text but then went on to a tool call in the
    # same turn, the streamed progress text stays visible until the next
    # iteration's deltas or the final answer overwrite it — desired UX.
    return final_message
