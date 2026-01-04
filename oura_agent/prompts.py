"""
Prompt loading for Oura Agent.
"""

from pathlib import Path


def get_prompts_dir() -> Path:
    """Get prompts directory - works both locally and on Modal."""
    # On Modal, prompts are copied to /root/prompts during image build
    modal_path = Path("/root/prompts")
    if modal_path.exists():
        return modal_path
    # Locally, prompts are next to modal_agent.py
    local_path = Path(__file__).parent.parent / "prompts"
    if local_path.exists():
        return local_path
    raise FileNotFoundError(f"Prompts directory not found at {modal_path} or {local_path}")


def load_prompt(name: str) -> str:
    """Load a prompt from the prompts directory."""
    prompt_file = get_prompts_dir() / f"{name}.md"
    if prompt_file.exists():
        return prompt_file.read_text()
    raise FileNotFoundError(f"Prompt file not found: {prompt_file}")


# Load prompts at module level for convenience
try:
    SYSTEM_PROMPT = load_prompt("morning_brief")
    CHAT_SYSTEM_PROMPT = load_prompt("chat")
except FileNotFoundError:
    # Handle case where prompts aren't available (e.g., during testing)
    SYSTEM_PROMPT = ""
    CHAT_SYSTEM_PROMPT = ""
