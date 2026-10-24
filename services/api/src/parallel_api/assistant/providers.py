"""Language-model providers behind one interface (spec 22.6).

The assistant never depends on one vendor: the service talks to a ``Provider``.
``AnthropicProvider`` calls Claude through the official SDK; ``GeminiProvider`` calls Google's Gemini
REST interface; ``ScriptedProvider``
replays a fixed script for tests and demonstrations without a network; when
no provider is configured the assistant reports itself unavailable and every
manual workflow continues to work.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

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


# ---------------------------------------------------------------- Gemini
_GEMINI_TYPES = {"string": "STRING", "number": "NUMBER", "integer": "INTEGER", "boolean": "BOOLEAN",
                 "array": "ARRAY", "object": "OBJECT"}
_GEMINI_KEYS = {"description", "enum", "format", "minimum", "maximum", "minItems", "maxItems", "minLength",
                "maxLength", "pattern", "required"}
_GEMINI_BLOCKED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "LANGUAGE"}


def gemini_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    """Our JSON-schema tool inputs in Gemini's OpenAPI-subset form.

    Keywords Gemini does not accept (``additionalProperties`` and the like) are dropped: the tool handlers
    validate their own input. ``anyOf [T, null]`` becomes ``nullable``. An object with no properties has no
    parameters, which Gemini expresses by omitting them (an empty OBJECT is rejected).
    """
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        out = gemini_schema(options[0]) if len(options) == 1 else {"anyOf": [gemini_schema(o) for o in options]}
        if out is not None and len(options) != len(schema["anyOf"]):
            out["nullable"] = True
        if out is not None and schema.get("description"):
            out["description"] = schema["description"]
        return out
    out = {k: v for k, v in schema.items() if k in _GEMINI_KEYS}
    kind = schema.get("type")
    if kind == "object":
        props = {k: gemini_schema(v) for k, v in (schema.get("properties") or {}).items()}
        props = {k: v for k, v in props.items() if v is not None}
        if not props:
            return None
        out["properties"] = props
        out["required"] = [r for r in schema.get("required", []) if r in props]
    elif kind == "array":
        out["items"] = gemini_schema(schema.get("items") or {"type": "string"}) or {"type": "STRING"}
    if kind in _GEMINI_TYPES:
        out["type"] = _GEMINI_TYPES[kind]
    return out


def gemini_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decls = []
    for t in tools:
        d: dict[str, Any] = {"name": t["name"], "description": t.get("description", "")}
        params = gemini_schema(t.get("input_schema") or {})
        if params:
            d["parameters"] = params
        decls.append(d)
    return [{"functionDeclarations": decls}] if decls else []


def gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Our stored turns (Anthropic-shaped blocks) as Gemini ``contents``.

    A tool result names its function, so call identifiers are resolved against the calls seen earlier.
    Turns without any content are left out: Gemini rejects empty parts.
    """
    names: dict[str, str] = {}
    native: set[str] = set()
    contents: list[dict[str, Any]] = []
    for m in messages:
        blocks = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
        parts: list[dict[str, Any]] = []
        for b in blocks:
            kind = b.get("type")
            if kind == "text" and b.get("text"):
                part: dict[str, Any] = {"text": b["text"]}
                if b.get("thought_signature"):
                    part["thoughtSignature"] = b["thought_signature"]
                parts.append(part)
            elif kind == "tool_use":
                names[b["id"]] = b["name"]
                if b.get("native_id"):
                    native.add(b["id"])
                call: dict[str, Any] = {"name": b["name"], "args": b.get("input") or {}}
                if b.get("native_id"):
                    call["id"] = b["id"]
                part = {"functionCall": call}
                if b.get("thought_signature"):
                    part["thoughtSignature"] = b["thought_signature"]
                parts.append(part)
            elif kind == "tool_result":
                try:
                    body = json.loads(b["content"]) if isinstance(b.get("content"), str) else b.get("content")
                except ValueError:
                    body = b.get("content")
                if b.get("is_error"):
                    body = {"error": (body or {}).get("error", body) if isinstance(body, dict) else body}
                elif not isinstance(body, dict):
                    body = {"result": body}
                resp: dict[str, Any] = {"name": names.get(b["tool_use_id"], "unknown_tool"), "response": body}
                if b["tool_use_id"] in native:
                    resp["id"] = b["tool_use_id"]
                parts.append({"functionResponse": resp})
        if parts:
            contents.append({"role": "model" if m["role"] == "assistant" else "user", "parts": parts})
    return contents


class GeminiProvider:
    """Gemini through its REST interface (``generateContent`` with function calling).

    Turns are stored in the same block form as every other provider, so history is replayed unchanged:
    a function call becomes a ``tool_use`` block (keeping Gemini's ``thoughtSignature``, which later
    Gemini models require to be sent back with the call). Nothing about adoption, permissions or
    organisation consent is provider-specific: those are enforced by the service and the tools.
    """

    name = "gemini"
    _default_transport: httpx.BaseTransport | None = None  # replaced by tests; real calls use the network

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.model = settings.gemini_model
        self._key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self._base = settings.gemini_base_url.rstrip("/")
        self._transport = transport or self._default_transport

    def availability(self) -> tuple[bool, str | None]:
        if self._key:
            return True, None
        return False, "No Gemini API key is configured on the server (set PARALLEL_GEMINI_API_KEY)."

    def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": gemini_contents(messages),
            "generationConfig": {"maxOutputTokens": 16000},
        }
        if declared := gemini_tools(tools):
            body["tools"] = declared
            body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        try:
            with httpx.Client(timeout=180, transport=self._transport) as client:
                r = client.post(f"{self._base}/v1beta/models/{self.model}:generateContent", json=body,
                                headers={"x-goog-api-key": self._key or ""})
        except httpx.HTTPError as e:
            raise ProviderError("The language-model provider could not be reached.") from e
        if r.status_code in (401, 403):
            raise ProviderError("The language-model provider rejected the server's credentials.")
        if r.status_code == 429:
            raise ProviderError("The language-model provider is rate limiting requests; try again shortly.")
        if r.status_code >= 400:
            try:
                detail = r.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = f"status {r.status_code}"
            raise ProviderError(f"The language-model provider rejected the request: {detail}")
        try:
            data = r.json()
        except ValueError as e:
            raise ProviderError("The language-model provider returned an unreadable response.") from e
        return self._turn(data)

    def _turn(self, data: dict[str, Any]) -> ProviderTurn:
        usage_in = data.get("usageMetadata") or {}
        usage = {"input_tokens": usage_in.get("promptTokenCount", 0),
                 "output_tokens": usage_in.get("candidatesTokenCount", 0),
                 **({"thinking_tokens": usage_in["thoughtsTokenCount"]} if "thoughtsTokenCount" in usage_in else {})}
        model = data.get("modelVersion") or self.model
        block = (data.get("promptFeedback") or {}).get("blockReason")
        candidates = data.get("candidates") or []
        if block or not candidates:
            return ProviderTurn([], "refusal", model, usage,
                                {"category": block or "NO_CANDIDATE", "explanation": None})
        cand = candidates[0]
        content: list[dict[str, Any]] = []
        for part in (cand.get("content") or {}).get("parts") or []:
            if part.get("thought"):
                continue  # a thought summary is not part of the answer
            sig = part.get("thoughtSignature")
            if "functionCall" in part:
                call = part["functionCall"]
                native = call.get("id")
                content.append({"type": "tool_use", "id": native or f"gemini_{uuid.uuid4().hex[:16]}",
                                "name": call["name"], "input": call.get("args") or {},
                                **({"native_id": True} if native else {}),
                                **({"thought_signature": sig} if sig else {})})
            elif part.get("text"):
                content.append({"type": "text", "text": part["text"], **({"thought_signature": sig} if sig else {})})
        finish = cand.get("finishReason", "STOP")
        if finish == "MALFORMED_FUNCTION_CALL":
            raise ProviderError("The model produced a malformed tool call; try again.")
        if finish in _GEMINI_BLOCKED:
            return ProviderTurn(content, "refusal", model, usage, {"category": finish, "explanation": None})
        if finish == "MAX_TOKENS":
            return ProviderTurn(content, "max_tokens", model, usage)
        has_call = any(b["type"] == "tool_use" for b in content)
        if not content:
            raise ProviderError("The language-model provider returned an empty answer; try again.")
        return ProviderTurn(content, "tool_use" if has_call else "end_turn", model, usage)


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
    if s.assistant_provider == "gemini":
        return GeminiProvider(s)
    return AnthropicProvider(s)
