"""
Agent handler with tools for chat interactions.

Uses Claude with tool use to handle both interventions and questions
with a single code path. The agent decides dynamically what to do
based on the message content.
"""

import json
from typing import Callable, Optional

import anthropic

from oura_agent.config import CLAUDE_MODEL, RAW_WINDOW_DAYS, logger
from oura_agent.prompts import load_prompt
from oura_agent.storage.baselines import load_baselines
from oura_agent.storage.conversations import (
    load_conversation_history,
    save_conversation_message,
)
from oura_agent.storage.interventions import (
    get_today_interventions,
    load_historical_interventions,
    save_intervention_raw,
)
from oura_agent.storage.metrics import load_historical_metrics, load_recent_briefs
from oura_agent.utils import now_nyc


def _get_agent_prompt() -> str:
    """Load agent prompt and inject current date."""
    try:
        prompt = load_prompt("agent")
        current_date = now_nyc().strftime("%Y-%m-%d")
        return prompt.replace("{current_date}", current_date)
    except FileNotFoundError:
        logger.error("CRITICAL: agent.md prompt not found!")
        return ""


TOOLS = [
    {
        "name": "get_metrics",
        "description": "Get daily health metrics for a date range. Use for questions about sleep, HRV, readiness trends. Returns summary data for each day including sleep_score, hrv, deep_sleep_minutes, readiness, resting_hr, stress_high, recovery_high, workout info.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD"
                },
                "end_date": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD (inclusive)"
                },
            },
            "required": ["start_date", "end_date"]
        }
    },
    {
        "name": "get_detailed_sleep",
        "description": "Get detailed sleep data for a specific night (HR/HRV trends through the night, sleep stages percentages, efficiency, latency). Use when user asks about a specific night's sleep quality.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date YYYY-MM-DD (the day you woke up)"
                }
            },
            "required": ["date"]
        }
    },
    {
        "name": "get_interventions",
        "description": "Get logged interventions (supplements, activities, food, etc.) for a date range. Use for correlation analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD"
                },
                "end_date": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD (inclusive)"
                }
            },
            "required": ["start_date", "end_date"]
        }
    },
    {
        "name": "get_baselines",
        "description": "Get 60-day rolling baseline statistics (mean, std) for all metrics. Use to compare current values against personal averages.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "log_intervention",
        "description": "Log an intervention the user reports (supplement, activity, food, etc). Use when user tells you they did/took something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "raw_text": {
                    "type": "string",
                    "description": "Original user input exactly as written"
                },
                "normalized": {
                    "type": "string",
                    "description": "Cleaned/normalized version (e.g., 'Magnesium 400mg', 'Sauna 20 min')"
                }
            },
            "required": ["raw_text", "normalized"]
        }
    },
    {
        "name": "get_today_interventions",
        "description": "Get all interventions logged today. Use to acknowledge what's been logged or answer questions about today's logging.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_recent_briefs",
        "description": "Get recent morning briefs (last 3 days). Use when user asks about previous recommendations or analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days of briefs to retrieve (default 3, max 7)"
                }
            }
        }
    }
]


def execute_tool(name: str, tool_input: dict) -> str:
    """Execute a tool and return JSON result."""
    try:
        if name == "get_metrics":
            all_metrics = load_historical_metrics(RAW_WINDOW_DAYS)
            start = tool_input["start_date"]
            end = tool_input["end_date"]
            filtered = [
                m for m in all_metrics
                if start <= m.get("date", "") <= end
            ]
            # Return only summary data to keep response concise
            result = [
                {"date": m["date"], "summary": m.get("summary", {})}
                for m in filtered
            ]
            return json.dumps(result, indent=2)

        elif name == "get_detailed_sleep":
            all_metrics = load_historical_metrics(RAW_WINDOW_DAYS)
            target_date = tool_input["date"]
            for m in all_metrics:
                if m.get("date") == target_date:
                    detailed = m.get("detailed_sleep", {})
                    if detailed:
                        return json.dumps(detailed, indent=2)
                    return json.dumps({"error": f"No detailed sleep data for {target_date}"})
            return json.dumps({"error": f"No data found for {target_date}"})

        elif name == "get_interventions":
            interventions = load_historical_interventions(RAW_WINDOW_DAYS)
            start = tool_input["start_date"]
            end = tool_input["end_date"]
            filtered = {
                d: v for d, v in interventions.items()
                if start <= d <= end
            }
            return json.dumps(filtered, indent=2)

        elif name == "get_baselines":
            baselines = load_baselines()
            # Return just metrics without the raw values arrays
            simplified = {
                "data_points": baselines.get("data_points", 0),
                "last_updated": baselines.get("last_updated"),
                "metrics": {
                    k: {"mean": v.get("mean"), "std": v.get("std")}
                    for k, v in baselines.get("metrics", {}).items()
                }
            }
            return json.dumps(simplified, indent=2)

        elif name == "log_intervention":
            raw = tool_input["raw_text"]
            normalized = tool_input["normalized"]
            entry = save_intervention_raw(raw, normalized)
            return json.dumps({
                "status": "logged",
                "time": entry.get("time"),
                "normalized": normalized
            })

        elif name == "get_today_interventions":
            entries = get_today_interventions()
            return json.dumps(entries, indent=2)

        elif name == "get_recent_briefs":
            days = min(tool_input.get("days", 3), 7)
            briefs = load_recent_briefs(days)
            return json.dumps(briefs, indent=2)

        else:
            return json.dumps({"error": f"Unknown tool: {name}"})

    except Exception as e:
        logger.error(f"Tool execution error ({name}): {e}")
        return json.dumps({"error": str(e)})


def handle_message_with_agent(
    api_key: str,
    user_message: str,
    send_progress: Optional[Callable[[str], None]] = None
) -> str:
    """
    Handle any message using agent with tools.

    Args:
        api_key: Anthropic API key
        user_message: The user's message
        send_progress: Optional callback to send progress updates (e.g., to Telegram)

    Returns:
        The agent's final response text
    """
    client = anthropic.Anthropic(api_key=api_key)
    agent_prompt = _get_agent_prompt()

    if not agent_prompt:
        return "Sorry, I'm not properly configured. Please check the logs."

    # Load conversation history for context
    history = load_conversation_history(10, today_only=True)
    messages = [{"role": msg["role"], "content": msg["content"]} for msg in history]
    messages.append({"role": "user", "content": user_message})

    max_iterations = 5  # Prevent infinite loops
    progress_sent = False

    for iteration in range(max_iterations):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=16000,
                thinking={
                    "type": "enabled",
                    "budget_tokens": 10000
                },
                system=agent_prompt,
                tools=TOOLS,
                messages=messages
            )
        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}")
            return "Sorry, I encountered an error. Please try again."

        # Check for tool use
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if not tool_uses:
            # No tools - extract final text response
            text_response = ""
            for block in response.content:
                if block.type == "text":
                    text_response = block.text
                    break

            # Save conversation
            save_conversation_message("user", user_message)
            save_conversation_message("assistant", text_response)
            return text_response

        # Extract and send progress text BEFORE tool execution (if any)
        # Agent is prompted to output "Looking at your data..." before tool calls
        if send_progress and not progress_sent:
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    send_progress(block.text)
                    progress_sent = True
                    break

        # Execute tools and continue loop
        tool_results = []
        for tool_use in tool_uses:
            logger.info(f"Executing tool: {tool_use.name}")
            result = execute_tool(tool_use.name, tool_use.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result
            })

        # Add assistant response and tool results to conversation
        # Need to serialize content blocks for the API
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    # Exhausted iterations
    logger.warning(f"Agent exhausted {max_iterations} iterations")
    return "I wasn't able to complete the analysis. Please try rephrasing your question."
