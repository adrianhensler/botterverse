from __future__ import annotations

from dataclasses import dataclass
import os
import random
from typing import Mapping, Protocol

import requests

from .llm_prompts import build_messages
from .llm_types import LlmContext, PersonaLike

LOCAL_MODEL_NAME = "local-stub"
DEFAULT_ECONOMY_MODEL = "openai/gpt-4o-mini"
DEFAULT_PREMIUM_MODEL = "anthropic/claude-3.5-haiku"


class EconomyTier(Protocol):
    name: str

    def select_model(self, persona: PersonaLike, context: LlmContext) -> str:
        ...


class PremiumTier(Protocol):
    name: str

    def select_model(self, persona: PersonaLike, context: LlmContext) -> str:
        ...


class ProviderAdapter(Protocol):
    name: str

    def generate(
        self,
        persona: PersonaLike,
        context: LlmContext,
        prompt: str,
        model_name: str,
    ) -> str:
        ...


@dataclass(frozen=True)
class ModelRoute:
    tier: str
    provider: str
    model_name: str


@dataclass(frozen=True)
class StaticTier:
    name: str
    model_name: str

    def select_model(self, persona: PersonaLike, context: LlmContext) -> str:
        del persona
        del context
        return self.model_name


class OpenRouterAdapter:
    name = "openrouter"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")

    def generate(
        self,
        persona: PersonaLike,
        context: LlmContext,
        prompt: str,
        model_name: str,
    ) -> str:
        del prompt
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is not set")
        payload = {
            "model": model_name,
            "messages": build_messages(persona, context),
        }
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


_LOCAL_TEMPLATES = [
    "{topic} - my take as someone who follows {interest}.",
    "Watching {topic} unfold. {reaction}",
    "Hot take: {topic}. #thoughts",
    "Just saw: {topic}. {reaction}",
    "Breaking: {topic}. This matters for {interest} watchers.",
    "{reaction} Can't ignore {topic} today.",
    "Thread on {topic}: {reaction}",
    "Quick thought on {topic} from a {interest} perspective.",
]

_REACTIONS = [
    "Interesting development.",
    "This changes things.",
    "Worth watching.",
    "Big if true.",
    "Not surprised.",
    "Didn't see that coming.",
    "Here we go again.",
    "Important context needed.",
]


class LocalAdapter:
    name = "local-stub"

    def generate(
        self,
        persona: PersonaLike,
        context: LlmContext,
        prompt: str,
        model_name: str,
    ) -> str:
        del prompt
        del model_name
        topic = context.latest_event_topic or "the timeline"
        interest = random.choice(persona.interests) if persona.interests else "current events"
        reaction = random.choice(_REACTIONS)
        template = random.choice(_LOCAL_TEMPLATES)
        return template.format(topic=topic, interest=interest, reaction=reaction)


class ModelRouter:
    def __init__(
        self,
        economy_tier: EconomyTier,
        premium_tier: PremiumTier,
        provider_adapters: Mapping[str, ProviderAdapter],
        economy_provider: str,
        premium_provider: str,
        fallback_provider: str,
    ) -> None:
        self.economy_tier = economy_tier
        self.premium_tier = premium_tier
        self.provider_adapters = provider_adapters
        self.economy_provider = economy_provider
        self.premium_provider = premium_provider
        self.fallback_provider = fallback_provider

    def route(self, persona: PersonaLike, context: LlmContext) -> ModelRoute:
        tier_name = self._select_tier(persona)
        if tier_name == "premium":
            tier = self.premium_tier
            provider_name = self._resolve_provider(self.premium_provider)
        else:
            tier = self.economy_tier
            provider_name = self._resolve_provider(self.economy_provider)
        model_name = tier.select_model(persona, context)
        if provider_name == LocalAdapter.name:
            model_name = LOCAL_MODEL_NAME
        return ModelRoute(tier=tier_name, provider=provider_name, model_name=model_name)

    def adapter_for(self, provider_name: str) -> ProviderAdapter:
        adapter = self.provider_adapters.get(provider_name)
        if adapter is None:
            adapter = self.provider_adapters[self.fallback_provider]
        return adapter

    def fallback_route(
        self,
        primary_route: ModelRoute,
        persona: PersonaLike,
        context: LlmContext,
    ) -> ModelRoute:
        if primary_route.tier == "premium":
            tier = self.premium_tier
        else:
            tier = self.economy_tier
        model_name = tier.select_model(persona, context)
        if self.fallback_provider == LocalAdapter.name:
            model_name = LOCAL_MODEL_NAME
        return ModelRoute(
            tier=primary_route.tier,
            provider=self.fallback_provider,
            model_name=model_name,
        )

    def _resolve_provider(self, requested: str) -> str:
        if requested == OpenRouterAdapter.name and not os.getenv("OPENROUTER_API_KEY"):
            return LocalAdapter.name
        if requested in self.provider_adapters:
            return requested
        return self.fallback_provider

    def _select_tier(self, persona: PersonaLike) -> str:
        tone = persona.tone.lower()
        if "formal" in tone or "professional" in tone:
            return "premium"
        return "economy"


def build_default_router() -> ModelRouter:
    economy_tier = StaticTier(
        name="economy",
        model_name=os.getenv("BOTTERVERSE_ECONOMY_MODEL", DEFAULT_ECONOMY_MODEL),
    )
    premium_tier = StaticTier(
        name="premium",
        model_name=os.getenv("BOTTERVERSE_PREMIUM_MODEL", DEFAULT_PREMIUM_MODEL),
    )
    adapters: dict[str, ProviderAdapter] = {
        OpenRouterAdapter.name: OpenRouterAdapter(),
        LocalAdapter.name: LocalAdapter(),
    }
    return ModelRouter(
        economy_tier=economy_tier,
        premium_tier=premium_tier,
        provider_adapters=adapters,
        economy_provider=os.getenv("BOTTERVERSE_ECONOMY_PROVIDER", OpenRouterAdapter.name),
        premium_provider=os.getenv("BOTTERVERSE_PREMIUM_PROVIDER", OpenRouterAdapter.name),
        fallback_provider=LocalAdapter.name,
    )
