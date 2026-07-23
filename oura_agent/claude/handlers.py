"""
Claude AI handlers — intervention cleaning, photo analysis, and the brief
entrypoint (which now delegates to the tool-using agent loop).
"""

import base64

from oura_agent.config import CLAUDE_MODEL, logger
from oura_agent.claude.agent import run_brief_agent
from oura_agent.claude.brief_card import generate_daily_card
from oura_agent.claude.models import create_message_with_fallback, response_text
from oura_agent.telegram.client import _detect_image_mime_type


def generate_brief_with_agent(
    api_key: str,
    today: str,
    metrics: dict,
    detailed_sleep: dict,
    detailed_workouts: list,
    profile: dict = None,
) -> str:
    """Generate the morning brief via the tool-using agent.

    Thin wrapper over oura_agent.claude.agent.run_brief_agent so the public
    handlers surface remains stable for modal_agent re-exports.
    """
    return run_brief_agent(
        api_key=api_key,
        today=today,
        metrics=metrics,
        detailed_sleep=detailed_sleep,
        detailed_workouts=detailed_workouts,
        profile=profile,
    )


def clean_intervention_with_claude(api_key: str, raw_text: str) -> str:
    """Use Claude to clean/normalize intervention text."""
    import anthropic

    prompt = f"""Clean and normalize this health intervention log entry. Fix typos, remove filler words, standardize format. Keep it brief (under 10 words ideally).

Input: "{raw_text}"

Output only the cleaned text, nothing else."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        call = create_message_with_fallback(
            client,
            {
                "max_tokens": 50,
                "messages": [{"role": "user", "content": prompt}],
            },
            model=CLAUDE_MODEL,
        )
        cleaned = response_text(call.response)
        return cleaned.strip().strip('"') if cleaned else raw_text
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
    call = create_message_with_fallback(
        client,
        {
            "max_tokens": 100,
            "messages": [{
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
            }],
        },
        model=CLAUDE_MODEL,
    )
    return response_text(call.response).strip() or "NOT_AN_INTERVENTION"
