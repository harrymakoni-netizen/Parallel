"""The Gemini provider: request and response contract against a stub transport (no network, no key), the
conversion of our tool schemas and stored turns, honest error reporting, and the whole assistant loop with
the same permission, adoption and grounding rules as every other provider."""

from __future__ import annotations

import json

import httpx
import pytest

from parallel_api.assistant import providers
from parallel_api.assistant.providers import (
    GeminiProvider,
    ProviderError,
    gemini_contents,
    gemini_schema,
    gemini_tools,
)
from parallel_api.assistant.tools import definitions
from parallel_core.settings import Settings, get_settings

KEY = "test-gemini-key-not-real"


def settings(**kw) -> Settings:
    return Settings(assistant_provider="gemini", gemini_api_key=KEY, gemini_model="gemini-test", **kw)


def stub(responses, seen=None, status=200):
    """A transport that records requests and replays the given response bodies in order."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)})
        body = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def reply(*parts, finish="STOP", **extra):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7}, "modelVersion": "gemini-test-001", **extra}


def walk(schema):
    """The schema and every schema nested in its properties and items (not the property names)."""
    yield schema
    for sub in (schema.get("properties") or {}).values():
        yield from walk(sub)
    if isinstance(schema.get("items"), dict):
        yield from walk(schema["items"])


def test_every_tool_schema_converts_to_what_gemini_accepts():
    declared = gemini_tools(definitions())[0]["functionDeclarations"]
    assert [d["name"] for d in declared] == [t["name"] for t in definitions()]
    forbidden = {"additionalProperties", "anyOf", "$schema", "strict", "title", "default"}
    for d in declared:
        params = d.get("parameters")
        if params is None:
            continue
        for node in walk(params):
            assert not (forbidden & node.keys()), (d["name"], node)
            if "type" in node:
                assert node["type"] in {"STRING", "NUMBER", "INTEGER", "BOOLEAN", "ARRAY", "OBJECT"}
            if node.get("type") == "OBJECT":
                assert node["properties"], (d["name"], "an empty OBJECT is rejected by Gemini")
                assert set(node["required"]) <= set(node["properties"])
    assert "parameters" not in next(d for d in declared if d["name"] == "inspect_project")  # takes no arguments
    nullable = [n for d in declared for n in walk(d.get("parameters", {})) if n.get("nullable")]
    assert nullable and all("type" in n for n in nullable)


def test_nullable_and_nested_schemas():
    out = gemini_schema({"type": "object", "additionalProperties": False, "required": ["a", "b", "gone"], "properties": {
        "a": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "maybe"},
        "b": {"type": "array", "items": {"type": "integer"}, "description": "ids"},
    }})
    assert out == {"type": "OBJECT", "required": ["a", "b"], "properties": {
        "a": {"type": "STRING", "nullable": True, "description": "maybe"},
        "b": {"type": "ARRAY", "items": {"type": "INTEGER"}, "description": "ids"}}}


def test_stored_turns_become_gemini_contents():
    history = [
        {"role": "user", "content": [{"type": "text", "text": "Close the east drift"}]},
        {"role": "assistant", "content": []},  # a refused turn stores nothing: never sent as an empty content
        {"role": "assistant", "content": [
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "id": "c1", "name": "inspect_project", "input": {}, "thought_signature": "SIG1"},
            {"type": "tool_use", "id": "c2", "name": "get_run_status", "input": {"run_id": "r"}, "native_id": True}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": json.dumps({"project": "P"})},
            {"type": "tool_result", "tool_use_id": "c2", "content": json.dumps({"error": "no such run"}), "is_error": True}]},
    ]
    contents = gemini_contents(history)
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    model = contents[1]["parts"]
    assert model[0] == {"text": "Looking."}
    assert model[1] == {"functionCall": {"name": "inspect_project", "args": {}}, "thoughtSignature": "SIG1"}
    assert model[2] == {"functionCall": {"name": "get_run_status", "args": {"run_id": "r"}, "id": "c2"}}
    answers = contents[2]["parts"]
    assert answers[0] == {"functionResponse": {"name": "inspect_project", "response": {"project": "P"}}}
    assert answers[1] == {"functionResponse": {"name": "get_run_status", "id": "c2", "response": {"error": "no such run"}}}


def test_request_contract_and_text_answer():
    seen: list = []
    p = GeminiProvider(settings(), transport=stub([reply({"text": "Hello."})], seen))
    turn = p.complete(system="SYS", messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                      tools=definitions())
    req = seen[0]
    assert req["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    assert req["headers"]["x-goog-api-key"] == KEY and "key=" not in req["url"]  # the key travels in a header only
    body = req["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "SYS"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}
    assert len(body["tools"][0]["functionDeclarations"]) == len(definitions())
    assert turn.text == "Hello." and turn.stop_reason == "end_turn" and turn.model == "gemini-test-001"
    assert turn.usage == {"input_tokens": 11, "output_tokens": 7}


def test_function_calls_become_tool_use_blocks_with_signatures_kept():
    sig = "opaque-signature"
    p = GeminiProvider(settings(), transport=stub([reply(
        {"thought": True, "text": "private thought summary"},
        {"functionCall": {"name": "inspect_project", "args": {}}, "thoughtSignature": sig},
        {"functionCall": {"name": "get_run_status", "args": {"run_id": "r1"}, "id": "native-7"}})]))
    turn = p.complete(system="s", messages=[{"role": "user", "content": "x"}], tools=definitions())
    assert turn.stop_reason == "tool_use" and turn.text == ""
    a, b = turn.tool_calls
    assert a["name"] == "inspect_project" and a["thought_signature"] == sig and a["id"].startswith("gemini_")
    assert b["id"] == "native-7" and b["native_id"] is True and b["input"] == {"run_id": "r1"}
    assert "private thought summary" not in json.dumps(turn.content)


@pytest.mark.parametrize("finish,expected", [("MAX_TOKENS", "max_tokens"), ("SAFETY", "refusal"), ("PROHIBITED_CONTENT", "refusal")])
def test_finish_reasons(finish, expected):
    p = GeminiProvider(settings(), transport=stub([reply({"text": "partial"}, finish=finish)]))
    turn = p.complete(system="s", messages=[{"role": "user", "content": "x"}], tools=[])
    assert turn.stop_reason == expected
    if expected == "refusal":
        assert turn.refusal["category"] == finish


def test_blocked_prompt_is_a_refusal_not_an_error():
    p = GeminiProvider(settings(), transport=stub([{"promptFeedback": {"blockReason": "SAFETY"}}]))
    turn = p.complete(system="s", messages=[{"role": "user", "content": "x"}], tools=[])
    assert turn.stop_reason == "refusal" and turn.refusal["category"] == "SAFETY" and turn.content == []


@pytest.mark.parametrize("status,body,fragment", [
    (401, {"error": {"message": "bad key"}}, "rejected the server's credentials"),
    (403, {"error": {"message": "denied"}}, "rejected the server's credentials"),
    (429, {"error": {"message": "slow down"}}, "rate limiting"),
    (400, {"error": {"message": "Invalid JSON payload received"}}, "Invalid JSON payload received"),
    (500, {"unexpected": True}, "status 500"),
])
def test_provider_errors_are_reported_without_the_key(status, body, fragment):
    p = GeminiProvider(settings(), transport=stub([body], status=status))
    with pytest.raises(ProviderError) as e:
        p.complete(system="s", messages=[{"role": "user", "content": "x"}], tools=[])
    assert fragment in str(e.value) and KEY not in str(e.value)


def test_unreachable_empty_and_malformed_answers():
    def boom(_):
        raise httpx.ConnectError("no route")

    with pytest.raises(ProviderError, match="could not be reached"):
        GeminiProvider(settings(), transport=httpx.MockTransport(boom)).complete(system="s", messages=[], tools=[])
    with pytest.raises(ProviderError, match="empty answer"):
        GeminiProvider(settings(), transport=stub([reply()])).complete(system="s", messages=[], tools=[])
    with pytest.raises(ProviderError, match="malformed tool call"):
        GeminiProvider(settings(), transport=stub([reply({"text": "x"}, finish="MALFORMED_FUNCTION_CALL")])).complete(
            system="s", messages=[], tools=[])


def test_availability_names_the_missing_key_without_inventing_one(monkeypatch):
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    ok, reason = GeminiProvider(Settings(assistant_provider="gemini", gemini_api_key=None)).availability()
    assert not ok and "PARALLEL_GEMINI_API_KEY" in reason
    assert GeminiProvider(settings()).availability() == (True, None)
    monkeypatch.setenv("GEMINI_API_KEY", "from-environment")
    assert GeminiProvider(Settings(assistant_provider="gemini", gemini_api_key=None)).availability() == (True, None)


def test_the_provider_is_selected_by_configuration(monkeypatch):
    monkeypatch.setenv("PARALLEL_ASSISTANT_PROVIDER", "gemini")
    monkeypatch.setenv("PARALLEL_GEMINI_API_KEY", KEY)
    get_settings.cache_clear()
    try:
        p = providers.get_provider()
        assert p.name == "gemini" and p.model == "gemini-2.5-pro"
    finally:
        monkeypatch.delenv("PARALLEL_ASSISTANT_PROVIDER")
        monkeypatch.delenv("PARALLEL_GEMINI_API_KEY")
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def mine(client, world):
    ed = world["editor"].h
    return client.post("/api/v1/projects", headers=ed, json={
        "organisation_id": world["org_id"], "name": "Gemini mine", "project_type": "mine_evacuation",
        "template": "mine_evacuation"}).json()["id"]


def test_assistant_loop_with_gemini_runs_tools_as_the_user_and_replays_signatures(client, world, mine, monkeypatch):
    seen: list = []
    sig = "sig-abc"
    transport = stub([
        reply({"text": "Let me look."}, {"functionCall": {"name": "inspect_project", "args": {}}, "thoughtSignature": sig}),
        reply({"text": "This project is a mine evacuation project."}),
    ], seen)
    monkeypatch.setenv("PARALLEL_ASSISTANT_PROVIDER", "gemini")
    monkeypatch.setenv("PARALLEL_GEMINI_API_KEY", KEY)
    monkeypatch.setattr(GeminiProvider, "_default_transport", transport)
    get_settings.cache_clear()
    try:
        ed = world["editor"].h
        st = client.get(f"/api/v1/projects/{mine}/assistant/status", headers=ed).json()
        assert st["available"] and st["provider"] == "gemini"
        sess = client.post(f"/api/v1/projects/{mine}/assistant/sessions", headers=ed).json()
        assert sess["provider"] == "gemini"
        r = client.post(f"/api/v1/assistant/sessions/{sess['id']}/messages", headers=ed,
                        json={"text": "What is this project?"})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        call = next(i for i in items if i["type"] == "tool_call")
        assert call["tool"] == "inspect_project" and call["error"] is None and call["authorised"]
        assert any(i["type"] == "assistant" and "mine evacuation" in i["text"] and i["served_by"] == "gemini-test-001"
                   for i in items)
        # the second request replays the first turn (with its signature) and carries the tool's real result
        second = seen[1]["body"]["contents"]
        assert second[1]["parts"][1]["thoughtSignature"] == sig
        answer = second[2]["parts"][0]["functionResponse"]
        assert answer["name"] == "inspect_project" and "Gemini mine" in json.dumps(answer["response"])
        # a viewer's tools still run under the viewer's permissions, whatever the provider
        assert client.post(f"/api/v1/projects/{mine}/assistant/sessions", headers=world["outsider"].h).status_code in (403, 404)
    finally:
        get_settings.cache_clear()
