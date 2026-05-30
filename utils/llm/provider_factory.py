import os

from dotenv import load_dotenv

from .anthropic_provider import AnthropicProvider
from .base_provider import LLMProvider
from .openai_provider import OpenAIProvider

load_dotenv()


def get_provider(provider_name: str | None = None) -> LLMProvider:
    """Create the selected LLM provider.

    If provider_name is passed from the Streamlit UI, that value wins.
    Otherwise, fall back to LLM_PROVIDER in .env, then OpenAI.
    """
    selected_provider = (provider_name or os.getenv("LLM_PROVIDER", "openai")).strip().lower()

    if selected_provider == "openai":
        return OpenAIProvider()

    if selected_provider in {"anthropic", "claude"}:
        return AnthropicProvider()

    raise ValueError(
        f"Unsupported LLM provider '{selected_provider}'. Use 'openai' or 'anthropic'."
    )
