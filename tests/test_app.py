import asyncio
import json
from types import SimpleNamespace

import pytest
import httpx
from google import genai
from google.auth.credentials import AnonymousCredentials
from google.genai import types
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
import app as api


def validate(value, name):
    schema = {**api.SCHEMA, "$ref": "#/components/schemas/" + name}
    Draft202012Validator(schema).validate(value)


def parse_events(response):
    events = []
    for block in response.text.strip().split("\n\n"):
        header, data = block.split("\n", 1)
        event = json.loads(data.removeprefix("data: "))
        assert header == "event: " + event["type"]
        assert event["sequence_number"] == len(events)
        name = next(k for k, schema in api.SCHEMA["components"]["schemas"].items()
                    if schema.get("properties", {}).get("type", {}).get("enum") == [event["type"]]
                    and "StreamingEvent" in k)
        validate(event, name)
        events.append(event)
    return events


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "API_TOKEN", "test-token")
    monkeypatch.setattr(api, "BASE_URL", "https://example.com")

    async def backend(contents, instructions, limit):
        assert contents[-1].role == "user"
        assert "demostración" in instructions
        usage = SimpleNamespace(prompt_token_count=4, candidates_token_count=3,
                                thoughts_token_count=2, cached_content_token_count=0)
        yield "Hola ", None, None
        yield "mundo", usage, "STOP"

    monkeypatch.setattr(api, "backend", backend)
    # No lifespan: no credenciales ni cliente real de Google durante estas pruebas.
    return TestClient(api.app, headers={"Authorization": "Bearer test-token"})


def test_json_and_schema(client):
    response = client.post("/v1/responses", json={"input": "Hola", "model": "hola-mundo"})
    assert response.status_code == 200
    result = response.json()
    validate(result, "ResponseResource")
    assert result["status"] == "completed"
    assert result["output"][0]["content"][0]["text"] == "Hola mundo"
    assert result["usage"]["total_tokens"] == 9


def test_stream_schema_order_and_final_parity(client):
    result = client.post("/v1/responses", json={"input": "Hola", "stream": True})
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/event-stream")
    events = parse_events(result)
    assert [e["type"] for e in events] == [
        "response.created", "response.in_progress", "response.output_item.added",
        "response.content_part.added", "response.output_text.delta", "response.output_text.delta",
        "response.output_text.done", "response.content_part.done", "response.output_item.done", "response.completed"]
    assert events[0]["response"]["output"] == []
    final = events[-1]["response"]
    assert final["output"][0]["content"][0]["text"] == "".join(e["delta"] for e in events if "delta" in e)
    plain = client.post("/v1/responses", json={"input": "Hola"}).json()
    for key in ("status", "usage", "model", "store"):
        assert final[key] == plain[key]


def test_history_translation(client, monkeypatch):
    captured = []

    async def backend(contents, instructions, limit):
        captured.extend(contents)
        assert "Sé breve" in instructions
        yield "Bien", None, "STOP"

    monkeypatch.setattr(api, "backend", backend)
    response = client.post("/v1/responses", json={"input": [
        {"role": "system", "content": "Sé breve"},
        {"role": "user", "content": [{"type": "input_text", "text": "Hola"}]},
        {"role": "assistant", "content": [{"type": "output_text", "text": "Hola", "annotations": []}]},
        {"role": "user", "content": "¿Qué tal?"}]})
    assert response.status_code == 200
    assert [x.role for x in captured] == ["user", "model", "user"]


@pytest.mark.parametrize("body", [
    {"input": "Hola", "store": True}, {"input": "Hola", "previous_response_id": "resp_old"},
    {"input": "Hola", "tools": [{"type": "function", "name": "foo", "description": "foo", "parameters": {}, "strict": False}]},
    {"input": "Hola", "temperature": 0.5}, {"input": "Hola", "stream": "yes"},
    {"input": "Hola", "max_output_tokens": 3000}, {"input": "Hola", "background": True},
    {"input": []}, {"input": "Hola", "unknown": True},
    {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "https://example.com/a.png"}]}]},
])
def test_rejects_unsupported(client, body):
    response = client.post("/v1/responses", json=body)
    assert response.status_code == 400
    assert set(response.json()["error"]) == {"type", "message", "param", "code"}


def test_auth_limits_and_bad_json(client):
    assert client.post("/v1/responses", json={"input": "Hola"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/v1/responses", content="bad").status_code == 400
    assert client.post("/v1/responses", content="x" * 66000).status_code == 413
    assert client.post("/v1/responses", json={"input": "Hola", "model": "other"}).status_code == 404


def test_failure_after_delta(client, monkeypatch):
    async def broken(*args):
        yield "Partial", None, None
        raise RuntimeError("SECRET PROVIDER DETAIL")
    monkeypatch.setattr(api, "backend", broken)
    response = client.post("/v1/responses", json={"input": "Hola", "stream": True})
    events = parse_events(response)
    assert events[-1]["type"] == "response.failed"
    assert "SECRET PROVIDER DETAIL" not in response.text
    assert client.post("/v1/responses", json={"input": "Hola"}).status_code == 502


def test_token_limit_and_blocked(client, monkeypatch):
    async def limited(*args):
        yield "Parcial", None, "MAX_TOKENS"
    monkeypatch.setattr(api, "backend", limited)
    events = parse_events(client.post("/v1/responses", json={"input": "Hola", "stream": True}))
    assert events[-1]["type"] == "response.incomplete"
    assert events[-1]["response"]["incomplete_details"]["reason"] == "max_output_tokens"

    async def blocked(*args):
        yield "", None, "SAFETY"
    monkeypatch.setattr(api, "backend", blocked)
    assert client.post("/v1/responses", json={"input": "Hola"}).status_code == 502


def test_card_and_a2a(client):
    card = client.get("/.well-known/agent-card.json", headers={"Authorization": ""}).json()
    assert card["url"] == "https://example.com/a2a"
    assert card["capabilities"]["streaming"] is False
    payload = {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {
        "message": {"kind": "message", "messageId": "123", "role": "user", "parts": [{"kind": "text", "text": "Hola"}]}}}
    result = client.post("/a2a", json=payload).json()
    assert result["result"]["parts"][0]["text"] == "Hola mundo"
    assert result["result"]["kind"] == "message"
    payload["method"] = "tasks/get"
    assert client.post("/a2a", json=payload).json()["error"]["code"] == -32601
    assert client.get("/").json()["open_responses_url"] == "https://example.com/v1/responses"


def test_cv_loaded_from_mounted_file(tmp_path):
    cv = tmp_path / "cv.txt"
    cv.write_text("Experiencia: analista de datos.\n", encoding="utf-8")
    system = api.load_system(cv)
    assert "demostración" in system
    assert "<curriculum>\nExperiencia: analista de datos.\n</curriculum>" in system
    assert api.NO_CV not in system


@pytest.mark.parametrize("content", [None, "  \n"])
def test_missing_or_empty_cv(tmp_path, content):
    cv = tmp_path / "cv.txt"
    if content is not None:
        cv.write_text(content, encoding="utf-8")
    system = api.load_system(cv)
    assert system.endswith(api.NO_CV)
    assert "<curriculum>" not in system


def test_real_google_sdk_with_simulated_http(monkeypatch):
    """Ejercita el SDK y backend reales; solo se sustituye el transporte HTTP."""
    requests = []

    async def handle(request):
        requests.append(request)
        assert "streamGenerateContent" in str(request.url)
        assert api.MODEL in str(request.url)
        body = json.loads(request.content)
        assert body["contents"][0]["parts"][0]["text"] == "Hola"
        thinking = body["generationConfig"]["thinkingConfig"]
        assert thinking.get("thinkingLevel", thinking.get("thinking_level")) == "LOW"
        chunks = [
            {"candidates": [{"content": {"role": "model", "parts": [
                {"text": "Privado", "thought": True}, {"text": "Hola "}]}, "index": 0}]},
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "mundo"}]},
                             "index": 0, "finishReason": "STOP"}],
             "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 3,
                               "thoughtsTokenCount": 2, "totalTokenCount": 9}},
        ]
        sse = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False)
        sync_http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)), trust_env=False)
        credentials = AnonymousCredentials()
        credentials.token = "fake-token-for-mocked-transport"
        google = genai.Client(enterprise=True, project="test-project", location="global",
                              credentials=credentials,
                              http_options=types.HttpOptions(api_version="v1", httpx_async_client=http,
                                                             httpx_client=sync_http))
        monkeypatch.setattr(api.app.state, "gemini", google, raising=False)
        try:
            values = [value async for value in api.backend(
                [types.Content(role="user", parts=[types.Part(text="Hola")])], "Sé breve", 1024)]
            assert "".join(v[0] for v in values) == "Hola mundo"
            assert values[-1][2] == "STOP"
            assert values[-1][1].thoughts_token_count == 2
        finally:
            await google.aio.aclose()
            google.close()
            sync_http.close()
            await http.aclose()
    asyncio.run(run())
    assert len(requests) == 1
