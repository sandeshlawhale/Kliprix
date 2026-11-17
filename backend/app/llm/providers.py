"""The LLM providers a user can choose (docs/07, "LLM session"; D-011)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderId = Literal["gemini", "anthropic", "openai", "openrouter", "openai_compatible"]


@dataclass(frozen=True)
class Provider:
    id: ProviderId
    label: str
    needs_base_url: bool


PROVIDERS: tuple[Provider, ...] = (
    Provider("gemini", "Google Gemini", needs_base_url=False),
    Provider("anthropic", "Anthropic Claude", needs_base_url=False),
    Provider("openai", "OpenAI", needs_base_url=False),
    Provider("openrouter", "OpenRouter", needs_base_url=False),
    Provider("openai_compatible", "OpenAI-compatible (custom URL)", needs_base_url=True),
)

PROVIDERS_BY_ID: dict[str, Provider] = {provider.id: provider for provider in PROVIDERS}
