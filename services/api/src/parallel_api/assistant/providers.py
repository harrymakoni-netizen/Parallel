"""Language-model providers behind one interface (spec 22.6).

The assistant never depends on one vendor: the service talks to a ``Provider``.
``AnthropicProvider`` calls Claude through the official SDK; ``ScriptedProvider``
replays a fixed script for tests and demonstrations without a network; when
no provider is configured the assistant reports itself unavailable and every
manual workflow continues to work.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from parallel_core.settings import Settings, get_settings

PROMPT_TEMPLATE_VERSION = "parallel-assistant-1"


class ProviderError(RuntimeError):
    """The provider could not produce a turn (network, credentials, rate limit, ...)."""


@dataclass
class ProviderTurn:
    content: list[dict[str, Any]]  # blocks exactly as returned; replayed unchanged on the next request
    stop_reason: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    refusal: dict[str, Any] | None = None

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def text(self) -> str:
        return "\n\n".join(b["text"] for b in self.content if b.get("type") == "text" and b.get("text"))


class Provider(Protocol):
    name: str
    model: str

    def availability(self) -> tuple[bool, str | None]: ...

    def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderTurn: ...


class AnthropicProvider:
    """Claude via the Anthropic SDK (beta endpoint for server-side refusal fallbacks)."""

    name = "anthropic"

    def __init__(self, settings: Settings):
        self.model = settings.assistant_model
        self.effort = settings.assistant_effort
        self._key = settings.anthropic_api_key

    def availability(self) -> tuple[bool, str | None]:
        if self._key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True, None
        return (
            False,
            "No Anthropic credentials are configured on the server (set PARALLEL_ANTHROPIC_API_KEY).",
        )

    def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        import anthropic

        client = anthropic.Anthropic(api_key=self._key) if self._key else anthropic.Anthropic()
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                tools=tools,  # strict schemas; tool_choice stays "auto" (forced choice is rejected on this model)
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},  # caches the stable prefix and the growing history
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # a classifier decline is retried server-side on the recommended model
            )
        except anthropic.AuthenticationError as e:
            raise ProviderError("The language-model provider rejected the server's credentials.") from e
        except anthropic.RateLimitError as e:
            raise ProviderError(
                "The language-model provider is rate limiting requests; try again shortly."
            ) from e
        except anthropic.BadRequestError as e:
            raise ProviderError(f"The language-model provider rejected the request: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"The language-model provider returned an error ({e.status_code}).") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError("The language-model provider could not be reached.") from e
        refusal = None
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            refusal = {
                "category": getattr(details, "category", None),
                "explanation": getattr(details, "explanation", None),
            }
        return ProviderTurn(
            content=[b.to_dict() for b in response.content],
            stop_reason=response.stop_reason or "end_turn",
            model=response.model,
            usage=response.usage.to_dict() if response.usage else {},
            refusal=refusal,
        )


class DisabledProvider:
    name = "none"
    model = "none"

    def __init__(self, reason: str):
        self.reason = reason

    def availability(self) -> tuple[bool, str | None]:
        return False, self.reason

    def complete(self, **_: Any) -> ProviderTurn:
        raise ProviderError(self.reason)


Step = Callable[[list[dict[str, Any]]], dict[str, Any]] | dict[str, Any]


class ScriptedProvider:
    """Deterministic provider for tests: each step is a dict (or a function of the messages) giving
    ``text`` and/or ``tool_calls`` ([{name, input}]). It exercises the same loop, tools and persistence."""

    name = "scripted"
    model = "scripted-test-model"

    def __init__(self, steps: list[Step]):
        self.steps = list(steps)
        self.requests: list[dict[str, Any]] = []
        self._n = 0

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        self.requests.append({"system": system, "messages": messages, "tools": [t["name"] for t in tools]})
        if not self.steps:
            return ProviderTurn([{"type": "text", "text": "(script finished)"}], "end_turn", self.model)
        step = self.steps.pop(0)
        spec = step(messages) if callable(step) else step
        content: list[dict[str, Any]] = []
        if spec.get("text"):
            content.append({"type": "text", "text": spec["text"]})
        for call in spec.get("tool_calls", []):
            self._n += 1
            content.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_scripted_{self._n}",
                    "name": call["name"],
                    "input": call["input"],
                }
            )
        stop = spec.get("stop_reason") or ("tool_use" if spec.get("tool_calls") else "end_turn")
        return ProviderTurn(
            content, stop, self.model, {"input_tokens": 0, "output_tokens": 0}, refusal=spec.get("refusal")
        )


_scripted: ScriptedProvider | None = None


def use_scripted(provider: ScriptedProvider | None) -> None:
    """Install (or clear) the scripted provider used when PARALLEL_ASSISTANT_PROVIDER=scripted."""
    global _scripted
    _scripted = provider


def get_provider() -> Provider:
    s = get_settings()
    if s.assistant_provider == "disabled":
        return DisabledProvider("The assistant is disabled by the server configuration.")
    if s.assistant_provider == "scripted":
        return _scripted or DisabledProvider("No scripted provider is installed.")
    return AnthropicProvider(s)
