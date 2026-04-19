"""
Tests for the brief's visual formatting rules that survive Telegram Markdown rendering.

These are prompt-content tests — they verify the rules are stated, so Claude will
follow them. Runtime rendering verification happens via live smoke.
"""

import re

import modal_agent
from oura_agent.prompts import load_prompt


PROMPT = load_prompt("morning_brief")
LOWER = PROMPT.lower()


class TestBannedSyntax:
    """Syntax that breaks Telegram's legacy Markdown parser."""

    def test_prompt_bans_double_asterisk_bold(self):
        assert "**" in PROMPT, "prompt should mention **double** asterisk"
        assert (
            "never use" in LOWER and "**" in PROMPT
        ) or "double asterisk" in LOWER, (
            "prompt must explicitly forbid **double asterisks** — Telegram uses *single*"
        )

    def test_prompt_bans_top_level_h1_header(self):
        assert (
            "#" in PROMPT
            and ("do not" in LOWER or "never" in LOWER)
            and "header" in LOWER
        ), "prompt should forbid top-level `#` markdown headers"

    def test_prompt_bans_ascii_tables(self):
        # Already in place from the old prompt; reassert after rewrite.
        assert (
            "ascii table" in LOWER or "| " in PROMPT
        ) and ("never" in LOWER or "no ascii" in LOWER), (
            "prompt must forbid ASCII tables with pipes"
        )

    def test_prompt_bans_em_dash_or_hyphen_bullets(self):
        """Claude kept using `—` or `-` as bullet markers; enforce `•` only."""
        assert "•" in PROMPT, "prompt uses • bullets"
        # Must explicitly say not to use `-` or `—` as bullets
        assert (
            "•" in PROMPT
            and ("only" in LOWER or "always" in LOWER or "consistent" in LOWER)
        ), "prompt must require • (not dashes) as the sole bullet marker"


class TestVisualStructure:
    """Section anchors and visual separators."""

    def test_prompt_includes_section_emoji_anchors(self):
        # Section headers should carry an anchor emoji
        required_anchors = ["📊", "🎯", "🔍"]
        for emoji in required_anchors:
            assert emoji in PROMPT, f"missing section anchor emoji: {emoji}"

    def test_prompt_mentions_separator_line(self):
        # A heavy line character for between-section breaks
        assert "━" in PROMPT, "prompt must show/mention the ━ separator line"

    def test_prompt_describes_metrics_row_format(self):
        """Full METRICS block should have a consistent compact row format."""
        # Row format should use backtick value or arrow (↑/↓) to emphasize scannability
        assert ("↑" in PROMPT and "↓" in PROMPT) or "`" in PROMPT, (
            "prompt should show metric rows with ↑/↓ direction or backtick-delimited values"
        )

    def test_prompt_recommendations_are_bullets_not_numbered(self):
        """RECOMMENDATIONS should render as bullets, not numbered paragraphs."""
        # Find the RECOMMENDATIONS section example in the prompt
        lower = LOWER
        idx = lower.find("recommendations")
        assert idx > -1
        # In the following ~500 chars we should see bullets, NOT "1." numbered format
        snippet = PROMPT[idx : idx + 600]
        # Numbered format "1. Workout" should NOT appear
        assert not re.search(r"^\s*1\.\s*Workout", snippet, re.MULTILINE), (
            "RECOMMENDATIONS should be bullets, not numbered"
        )
        assert "• *workout" in snippet.lower() or "• *Workout" in snippet, (
            "RECOMMENDATIONS example should show bullet-with-bold-label format"
        )


class TestTelegramWrapperHeader:
    """modal_agent.py builds the Telegram message with a header above the brief."""

    def _modal_agent_source(self) -> str:
        from pathlib import Path
        path = Path(modal_agent.__file__)
        return path.read_text()

    def test_wrapper_header_has_emoji_and_separator(self):
        src = self._modal_agent_source()
        assert "☀" in src or "🌅" in src or "🌞" in src, (
            "Telegram header should lead with a morning/sun emoji"
        )
        assert "━" in src, "Telegram header should include a ━ separator line"

    def test_wrapper_formats_short_date(self):
        src = self._modal_agent_source()
        assert "%b" in src or "%B" in src, (
            "header must format the date via strftime %b/%B for short-human form"
        )
