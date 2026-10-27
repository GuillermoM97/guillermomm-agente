"""Demo de texto: Gemini, Open Responses HTTP/SSE y A2A 0.3 síncrono."""
import asyncio
import copy
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from google import genai
from google.genai import types
from jsonschema import Draft202012Validator

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
API_TOKEN = os.getenv("AGENT_API_TOKEN", "")
BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
# El CV no vive en el repositorio (es público): Cloud Run lo monta desde Secret Manager.
CV_PATH = Path(os.getenv("CV_PATH", "/cv/cv.txt"))
NO_CV = ("Todavía no se ha cargado ningún currículum. Si te preguntan por la experiencia "
         "de tu dueño, di que aún no se ha cargado su información.")
logger = logging.getLogger("agent")


def load_system(cv_path):
    system = Path(__file__).with_name("instructions.txt").read_text(encoding="utf-8").strip()
    cv = cv_path.read_text(encoding="utf-8").strip() if cv_path.is_file() else ""
    # Registrar solo si hay CV, nunca su contenido.
    logger.warning("cv_loaded=%s", bool(cv))
    if not cv:
        return system + "\n\n" + NO_CV
    return system + "\n\nCurrículum de tu dueño:\n<curriculum>\n" + cv + "\n</curriculum>"


SYSTEM = load_system(CV_PATH)
SCHEMA = json.loads(Path(__file__).with_name("openresponses-openapi.json").read_text())
REQUEST_SCHEMA = {"$ref": "#/components/schemas/CreateResponseBody", **SCHEMA}
VALIDATOR = Draft202012Validator(REQUEST_SCHEMA)


@asynccontextmanager
async def lifespan(app):
    if not API_TOKEN:
        raise RuntimeError("AGENT_API_TOKEN es obligatorio")
    # Credenciales de la cuenta de servicio de Cloud Run; no archivo de claves.
    app.state.gemini = genai.Client(
        enterprise=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
        http_options=types.HttpOptions(api_version="v1", timeout=90000),
    )
    try:
        yield
    finally:
        await app.state.gemini.aio.aclose()
        app.state.gemini.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class ApiError(Exception):
    def __init__(self, message, param=None, code="invalid_request", status=400):
        self.message, self.param, self.code, self.status = message, param, code, status


@app.exception_handler(ApiError)
async def api_error(request, exc):
    kind = "server_error" if exc.status >= 500 else "invalid_request_error"
    if exc.status == 401:
        kind = "authentication_error"
    return JSONResponse({"error": {"message": exc.message, "type": kind,
                                  "param": exc.param, "code": exc.code}},
                        status_code=exc.status)


def authenticate(request):
    expected = f"Bearer {API_TOKEN}"
    supplied = request.headers.get("authorization", "")
    if not API_TOKEN or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise ApiError("Se requiere una clave Bearer válida.", code="invalid_api_key", status=401)


async def read_json(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise ApiError("Máximo 64 KiB por petición.", code="request_too_large", status=413)
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        raise ApiError("El cuerpo debe ser JSON válido.")
    if not isinstance(data, dict):
        raise ApiError("El cuerpo debe ser un objeto JSON.")
    return data


def parse_request(body):
    # Aceptar también la forma abreviada usada por clientes Responses.
    body = copy.deepcopy(body)
    if isinstance(body.get("input"), list):
        for item in body["input"]:
            if isinstance(item, dict) and "role" in item:
                item.setdefault("type", "message")
    error = next(VALIDATOR.iter_errors(body), None)
    if error:
        param = ".".join(map(str, error.absolute_path)) or "body"
        raise ApiError(f"Formato inválido en {param}.", param)
    allowed = {"model", "input", "instructions", "stream", "store", "metadata",
               "max_output_tokens", "tools", "tool_choice", "previous_response_id",
               "background", "text", "truncation", "include", "parallel_tool_calls",
               "service_tier"}
    for key in body.keys() - allowed:
        raise ApiError(f"Este hola mundo todavía no admite {key}.", key, "unsupported_parameter")
    defaults = {"store": (False,), "tools": (None, []), "tool_choice": (None, "auto", "none"),
                "previous_response_id": (None,), "background": (False,),
                "text": (None, {"format": {"type": "text"}}),
                "truncation": ("disabled",), "include": ([],),
                "service_tier": ("auto", "default")}
    for key, values in defaults.items():
        if key in body and body[key] not in values:
            raise ApiError(f"{key} no está disponible en este hola mundo.", key, "unsupported_parameter")
    if body.get("model") not in (None, "hola-mundo", MODEL):
        raise ApiError("Usa el modelo hola-mundo.", "model", "model_not_found", 404)
    limit = body.get("max_output_tokens") or 1024
    if limit > 2048:
        raise ApiError("Máximo 2048 tokens en esta demo.", "max_output_tokens")
    inp = body.get("input")
    if isinstance(inp, str):
        inp = [{"role": "user", "content": inp}]
    if not isinstance(inp, list) or not inp:
        raise ApiError("Incluye input con texto o mensajes.", "input")
    instructions = [SYSTEM]
    if body.get("instructions"):
        instructions.append(body["instructions"])
    contents = []
    for item in inp:
        if item.get("type", "message") != "message":
            raise ApiError("Solo se admiten mensajes de texto.", "input")
        role, content = item.get("role"), item.get("content")
        if role not in ("user", "assistant", "system", "developer"):
            raise ApiError("Rol no admitido.", "input")
        if isinstance(content, list):
            if any(p.get("type") not in ("input_text", "output_text") for p in content):
                raise ApiError("Solo se admiten partes de texto.", "input")
            content = "\n".join(p["text"] for p in content)
        if not isinstance(content, str) or not content.strip():
            raise ApiError("Cada mensaje debe contener texto.", "input")
        if role in ("system", "developer"):
            instructions.append(content)
        else:
            contents.append(types.Content(role="model" if role == "assistant" else "user",
                                          parts=[types.Part(text=content)]))
    if not contents or contents[-1].role != "user":
        raise ApiError("El último mensaje debe ser del usuario.", "input")
    return contents, "\n\n".join(instructions), limit


async def backend(contents, instructions, limit):
    stream = await app.state.gemini.aio.models.generate_content_stream(
        model=MODEL, contents=contents,
        config=types.GenerateContentConfig(system_instruction=instructions,
                                           max_output_tokens=limit,
                                           thinking_config=types.ThinkingConfig(thinking_level="LOW")),
    )
    try:
        async for chunk in stream:
            # No enviar partes internas de razonamiento al usuario.
            text = "".join(p.text for c in (chunk.candidates or [])
                           for p in (c.content.parts if c.content else [])
                           if p.text and not p.thought)
            finish = next((c.finish_reason.value for c in (chunk.candidates or []) if c.finish_reason), None)
            yield text, chunk.usage_metadata, finish
    finally:
        await stream.aclose()


def response_base(body, limit):
    return dict(id="resp_" + uuid.uuid4().hex, object="response", created_at=int(time.time()),
                completed_at=None, status="in_progress", incomplete_details=None, model=MODEL,
                previous_response_id=None, instructions=body.get("instructions"), output=[], error=None,
                tools=[], tool_choice=body.get("tool_choice") or "auto", truncation="disabled",
                parallel_tool_calls=body.get("parallel_tool_calls", False) or False,
                text={"format": {"type": "text"}}, top_p=1.0, temperature=1.0,
                presence_penalty=0.0, frequency_penalty=0.0, top_logprobs=0,
                reasoning={"effort": "low", "summary": None}, usage=None,
                max_output_tokens=limit, max_tool_calls=None, store=False, background=False,
                service_tier="default", metadata=body.get("metadata") or {},
                safety_identifier=None, prompt_cache_key=None)


async def events(body, contents, instructions, limit):
    response = response_base(body, limit)
    index = 0

    def event(kind, **fields):
        nonlocal index
        value = copy.deepcopy(dict(type=kind, sequence_number=index, **fields))
        index += 1
        return value

    yield event("response.created", response=response)
    yield event("response.in_progress", response=response)
    message = dict(id="msg_" + uuid.uuid4().hex, type="message", role="assistant",
                   status="in_progress", content=[])
    yield event("response.output_item.added", output_index=0, item=message)
    part = dict(type="output_text", text="", annotations=[])
    pointers = dict(item_id=message["id"], output_index=0, content_index=0)
    yield event("response.content_part.added", **pointers, part=part)
    finish, usage = None, None
    try:
        async with asyncio.timeout(100):
            async for delta, chunk_usage, chunk_finish in backend(contents, instructions, limit):
                usage = chunk_usage or usage
                finish = chunk_finish or finish
                if delta:
                    part["text"] += delta
                    yield event("response.output_text.delta", **pointers, delta=delta)
        if finish not in (None, "STOP", "MAX_TOKENS") or not part["text"]:
            raise RuntimeError("El proveedor no produjo una respuesta de texto utilizable")
    except Exception as exc:
        # No registrar prompt, respuesta, credenciales ni mensaje crudo del proveedor.
        logger.error("gemini_failed exception=%s", type(exc).__name__)
        message.update(status="incomplete", content=[part])
        response.update(status="failed", output=[message], error={
            "code": "provider_error", "message": "Gemini no pudo completar la respuesta. Revisa acceso, cuota y modelo."})
        yield event("response.failed", response=response)
        return
    status = "incomplete" if finish == "MAX_TOKENS" else "completed"
    message.update(status=status, content=[part])
    yield event("response.output_text.done", **pointers, text=part["text"])
    yield event("response.content_part.done", **pointers, part=part)
    yield event("response.output_item.done", output_index=0, item=message)
    response.update(status=status, output=[message])
    if status == "completed":
        response["completed_at"] = int(time.time())
    else:
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    if usage:
        prompt = usage.prompt_token_count or 0
        reasoning = usage.thoughts_token_count or 0
        output = (usage.candidates_token_count or 0) + reasoning
        response["usage"] = dict(input_tokens=prompt, output_tokens=output, total_tokens=prompt + output,
                                 input_tokens_details={"cached_tokens": usage.cached_content_token_count or 0},
                                 output_tokens_details={"reasoning_tokens": reasoning})
    yield event("response." + status, response=response)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def info():
    return {"name": "Hola mundo con Gemini", "model": "hola-mundo",
            "agent_card_url": BASE_URL + "/.well-known/agent-card.json",
            "open_responses_url": BASE_URL + "/v1/responses"}


@app.get("/.well-known/agent-card.json")
async def card():
    # A2A 0.3 estándar. No inventar un campo estándar para Open Responses.
    return {"protocolVersion": "0.3.0", "name": "Hola mundo con Gemini",
            "description": "Agente de prueba en español. Open Responses: " + BASE_URL + "/v1/responses",
            "url": BASE_URL + "/a2a", "preferredTransport": "JSONRPC", "version": "0.1.0",
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "security": [{"bearerAuth": []}],
            "skills": [{"id": "hello", "name": "Saludar", "description": "Saluda y responde preguntas sencillas.",
                        "tags": ["demo", "español"], "examples": ["Hola, preséntate."]}]}


@app.get("/v1/models")
async def models(request: Request):
    authenticate(request)
    return {"object": "list", "data": [{"id": "hola-mundo", "object": "model", "created": 0, "owned_by": "custom"}]}


@app.post("/v1/responses")
async def responses(request: Request):
    authenticate(request)
    body = await read_json(request)
    contents, instructions, limit = parse_request(body)
    stream = events(body, contents, instructions, limit)
    if body.get("stream", False):
        async def sse():
            async for event in stream:
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    final = None
    async for event in stream:
        if event["type"] in ("response.completed", "response.incomplete", "response.failed"):
            final = event["response"]
    return JSONResponse(final, status_code=502 if final["status"] == "failed" else 200)


@app.post("/a2a")
async def a2a(request: Request):
    authenticate(request)
    body = await read_json(request)
    rpc_id = body.get("id")

    def error(code, message):
        return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}

    if body.get("jsonrpc") != "2.0" or "id" not in body or isinstance(rpc_id, (dict, list, bool)):
        return error(-32600, "Petición JSON-RPC inválida")
    if body.get("method") != "message/send":
        return error(-32601, "Esta demo solo admite message/send")
    try:
        message = body["params"]["message"]
        if message.get("kind") != "message" or message.get("role") != "user" or not message.get("messageId"):
            return error(-32602, "Se requiere un mensaje de usuario A2A 0.3")
        if message.get("taskId") or message.get("contextId"):
            return error(-32602, "Esta demo no conserva tareas ni contextos")
        parts = message["parts"]
        if not parts or any(p.get("kind") != "text" or not isinstance(p.get("text"), str) for p in parts):
            return error(-32602, "Solo texto")
        payload = {"input": "\n".join(p["text"] for p in parts)}
        contents, instructions, limit = parse_request(payload)
    except (KeyError, TypeError, AttributeError, ApiError):
        return error(-32602, "Parámetros inválidos")
    final = None
    async for event in events(payload, contents, instructions, limit):
        if event["type"] in ("response.completed", "response.incomplete", "response.failed"):
            final = event["response"]
    if final["status"] != "completed":
        return error(-32603, "Gemini no completó la respuesta")
    return {"jsonrpc": "2.0", "id": rpc_id,
            "result": {"kind": "message", "messageId": uuid.uuid4().hex, "role": "agent",
                       "parts": [{"kind": "text", "text": final["output"][0]["content"][0]["text"]}]}}
