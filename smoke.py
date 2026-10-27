"""Prueba contra el servicio real; solo usa la biblioteca estándar."""
import json
import os
import urllib.error
import urllib.request

base = os.environ["SERVICE_URL"].rstrip("/")
token = os.environ["AGENT_API_TOKEN"]


def call(path, body=None, authorized=True):
    headers = {"Content-Type": "application/json"}
    if authorized:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(base + path, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
    try:
        return urllib.request.urlopen(request, timeout=115)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code}: {exc.read().decode()}")


with call("/.well-known/agent-card.json", authorized=False) as response:
    card = json.load(response)
    assert card["url"] == base + "/a2a", "URL de tarjeta incorrecta"
    print("Tarjeta: OK")

payload = {"model": "hola-mundo", "input": "Di hola y preséntate en una frase.", "store": False}
with call("/v1/responses", payload) as response:
    result = json.load(response)
    assert result["status"] == "completed", result
    print("Gemini:", result["output"][0]["content"][0]["text"])

with call("/v1/responses", {**payload, "stream": True}) as response:
    event_types = []
    for line in response:
        if line.startswith(b"data: "):
            event = json.loads(line[6:])
            assert event["sequence_number"] == len(event_types)
            event_types.append(event["type"])
    assert event_types[-1] == "response.completed", event_types
    assert "response.output_text.delta" in event_types
    print("Streaming: OK")

print("Hola mundo verificado contra Google.")
