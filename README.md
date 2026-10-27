# guillermomm-agente

Agente de currículum con **Gemini** desplegado en **Google Cloud Run**. Responde
preguntas sobre la trayectoria profesional y académica de su dueño usando solo
su CV, y habla dos protocolos de agentes:

- **Open Responses** (HTTP y streaming SSE) en `/v1/responses`.
- **A2A 0.3** (Agent2Agent): tarjeta pública del agente y `message/send` síncrono en `/a2a`.

El repositorio es público y **no contiene el CV ni ninguna clave**: el CV vive en
Secret Manager y Cloud Run lo monta al arrancar.

> Este proyecto se construyó con ayuda de **ChatGPT** y **Claude Code**.
> Ver [Cómo se construyó](#cómo-se-construyó).

---

## Arquitectura

```mermaid
flowchart LR
    U["Cliente o plataforma de agentes"] -->|"Bearer + JSON"| CR

    subgraph GCP["Google Cloud (us-central1)"]
        CR["Cloud Run: hola-mundo-cv<br/>FastAPI + Uvicorn"]
        SM[("Secret Manager<br/>hola-mundo-api-token<br/>agente-cv")]
        AR[("Artifact Registry<br/>imagen del contenedor")]
        CB["Cloud Build"]
    end

    G["Gemini 3.8 Flash<br/>Vertex AI, endpoint global"]

    SM -->|"clave como variable de entorno"| CR
    SM -->|"CV montado en /cv/cv.txt"| CR
    CR -->|"google-genai SDK<br/>cuenta de servicio cv-agent"| G
    CB --> AR --> CR
```

| Ruta | Acceso | Qué hace |
|---|---|---|
| `GET /health` | Pública | Confirma que la app está viva (no prueba Gemini) |
| `GET /` | Pública | Información de conexión y URL de Open Responses |
| `GET /.well-known/agent-card.json` | Pública | Tarjeta A2A 0.3 del agente |
| `GET /v1/models` | Bearer | Lista el modelo `hola-mundo` |
| `POST /v1/responses` | Bearer | Open Responses, JSON o streaming SSE |
| `POST /a2a` | Bearer | JSON-RPC A2A `message/send` |

### Flujo de una petición

```mermaid
sequenceDiagram
    participant C as Cliente
    participant A as app.py (Cloud Run)
    participant G as Gemini 3.8 Flash

    C->>A: POST /v1/responses (Bearer, stream=true)
    A->>A: Verifica la clave (hmac.compare_digest)
    A->>A: Lee hasta 64 KiB y valida contra el esquema Open Responses
    A->>A: Traduce roles: user / assistant → user / model
    A->>G: generate_content_stream (instrucciones + CV, thinking LOW)
    A-->>C: response.created, response.in_progress, output_item.added, content_part.added
    loop Cada fragmento de Gemini
        G-->>A: texto (se descartan las partes de razonamiento)
        A-->>C: response.output_text.delta
    end
    A-->>C: output_text.done, content_part.done, output_item.done
    A-->>C: response.completed (o incomplete / failed)
```

Si Gemini falla, el cliente recibe `response.failed` o un HTTP 502 con un mensaje
genérico: el error interno del proveedor nunca se expone ni se registra.

### Cómo se protege el CV en un repositorio público

```mermaid
flowchart TB
    CV["cv.txt en tu carpeta personal de Cloud Shell<br/>(fuera del repositorio)"]
    CV -->|"CV_FILE=~/cv.txt bash deploy.sh"| SM[("Secret Manager: agente-cv")]
    SM -->|"montado como archivo"| F["/cv/cv.txt dentro del contenedor"]
    F --> L["load_system() en app.py"]
    I["instructions.txt<br/>(público, sin datos personales)"] --> L
    L -->|"instrucciones + CV entre etiquetas curriculum"| G["system_instruction de Gemini"]

    R["Repositorio GitHub (público)"] -. "no contiene" .-> CV
```

Varias capas evitan que el CV llegue a git o a la imagen por accidente:

- `deploy.sh` se niega a usar un CV que esté dentro de la carpeta del repositorio.
- `.gitignore`, `.gcloudignore` y `.dockerignore` excluyen `cv*.txt`, `cv*.md`, PDF y Word.
- Los logs solo registran si el CV se cargó (`cv_loaded=True`), nunca su contenido.
- Si no hay CV, el agente responde que la información aún no se ha cargado.

---

## Tecnologías

| Capa | Tecnología | Uso en el proyecto |
|---|---|---|
| Lenguaje | Python 3.12 | Toda la aplicación |
| API web | FastAPI 0.162 + Uvicorn 0.54 | Endpoints HTTP, streaming SSE |
| Modelo | Gemini 3.8 Flash (Vertex AI) | Genera las respuestas, con razonamiento en nivel `LOW` |
| SDK | `google-genai` 2.33 | Llamadas a Gemini con la cuenta de servicio, sin archivos de claves |
| Validación | `jsonschema` 4.26 + esquema oficial Open Responses (2026-04-24) | Valida cada petición |
| Protocolos | Open Responses, A2A 0.3, JSON-RPC 2.0, Server-Sent Events | Interoperabilidad con plataformas de agentes |
| Contenedor | Docker (`python:3.12-slim`, usuario sin privilegios) | Imagen de ejecución |
| Cómputo | Google Cloud Run | Servicio sin servidor: 0 a 2 instancias, 1 CPU, 512 MiB |
| Build | Cloud Build + Artifact Registry | `gcloud run deploy --source=.` construye la imagen |
| Secretos | Secret Manager | Clave Bearer y CV |
| Identidad | IAM: cuentas de servicio `cv-agent` (ejecución) y `cv-agent-build` (build) | Permisos mínimos separados |
| Operación | Cloud Shell + `gcloud` CLI | Despliegue con `deploy.sh` |
| Pruebas | pytest 9.1 + httpx 0.28 | Pruebas sin consumir tokens |
| Código | Git + GitHub | Control de versiones |
| Asistentes de IA | ChatGPT y Claude Code | Diseño, código, revisión y documentación |

---

## Cómo se construyó

El proyecto se hizo en una conversación iterativa con dos asistentes de IA.
Cada cambio se revisó y desplegó manualmente desde Cloud Shell.

```mermaid
flowchart LR
    A["1. Hola mundo<br/>ChatGPT"] --> B["2. Repositorio en GitHub<br/>ChatGPT"]
    B --> C["3. Despliegue en Cloud Run<br/>Cloud Shell"]
    C --> D["4. CV fuera del repo<br/>Claude Code"]
    D --> E["5. Evaluación con 50 preguntas<br/>Claude Code"]
    E --> F["6. Instrucciones reforzadas<br/>Claude Code"]
```

| Etapa | Asistente | Qué se hizo |
|---|---|---|
| 1. Hola mundo | **ChatGPT** | Diseño inicial: FastAPI con Gemini, adaptador Open Responses (JSON y SSE), tarjeta y `message/send` A2A, autenticación Bearer, `deploy.sh` con cuentas de servicio y Secret Manager, `smoke.py` y 18 pruebas locales con Gemini simulado. |
| 2. Repositorio | **ChatGPT** | Se convirtió en repositorio Git, con nombre `guillermomm-agente` y `.gitignore` para credenciales. |
| 3. Despliegue | Manual | Se clonó en Cloud Shell y se desplegó con `bash deploy.sh` en Cloud Run. |
| 4. CV fuera del repo | **Claude Code** | Revisión del repositorio. Como el repo debía ser público, el CV se movió a Secret Manager (`agente-cv`) montado en `/cv/cv.txt`. Se añadieron `CV_FILE` en `deploy.sh`, las exclusiones en los tres archivos *ignore*, `load_system()` y 3 pruebas nuevas (21 en total). |
| 5. Evaluación | **Claude Code** | 50 preguntas de prueba con la respuesta esperada según el CV: datos directos, cálculos, datos ausentes, premisas falsas e intentos de manipulación. |
| 6. Instrucciones | **Claude Code** | `instructions.txt` reescrito con 19 reglas para responder solo con el CV, admitir lo que no sabe, corregir premisas falsas, resistir instrucciones inyectadas y mantener respuestas breves. |
| Documentación | **Claude Code** | Este README y sus diagramas. |

---

## Desplegar

Desde Cloud Shell, con un proyecto de Google Cloud que tenga facturación activa:

```bash
git clone https://github.com/GuillermoM97/guillermomm-agente.git
cd guillermomm-agente
bash deploy.sh
```

Para actualizar después de un cambio:

```bash
git pull --ff-only
bash deploy.sh
```

```mermaid
flowchart TB
    S["bash deploy.sh"] --> A["Habilita APIs: Run, Build, Artifact Registry,<br/>Vertex AI, Secret Manager, IAM"]
    A --> B["Crea o reutiliza cuentas de servicio<br/>cv-agent: aiplatform.user<br/>cv-agent-build: run.builder"]
    B --> C["Crea la clave Bearer una sola vez<br/>en Secret Manager"]
    C --> D{"¿CV_FILE?"}
    D -->|"sí"| E["Sube una nueva versión de agente-cv"]
    D -->|"no"| F{"¿Ya existe agente-cv?"}
    E --> M["Monta el CV en /cv/cv.txt"]
    F -->|"sí"| M
    F -->|"no"| N["Despliega sin CV"]
    M --> R["gcloud run deploy --source=."]
    N --> R
    R --> U["Fija PUBLIC_BASE_URL"]
    U --> T["smoke.py: tarjeta, respuesta real<br/>de Gemini y streaming"]
```

Valores por defecto, que se pueden cambiar con variables de entorno:

```bash
PROJECT_ID=tu-proyecto REGION=us-central1 GEMINI_MODEL=gemini-3.8-flash bash deploy.sh
```

Si el build devuelve `PERMISSION_DENIED` justo después de crear permisos, espera unos
minutos y repite: IAM tarda en propagarse. Si persiste, revisa el permiso concreto.

## Cargar el currículum

1. Escribe el CV en texto plano UTF-8 (`cv.txt`, máximo 64 KiB). Incluye solo lo que
   aceptas que el agente comparta con quien tenga la clave Bearer.
2. Súbelo a tu carpeta personal de Cloud Shell (⋮ → Subir), **no** al repositorio.
3. Despliega indicando la ruta:

```bash
CV_FILE=~/cv.txt bash deploy.sh
```

Para actualizar el CV, repite el comando con el archivo nuevo. Después, `bash deploy.sh`
sin `CV_FILE` conserva el CV ya guardado. Para confirmar que se cargó:

```bash
gcloud run services logs read hola-mundo-cv --region=us-central1 --limit=100 | grep cv_loaded
```

## Conectar una plataforma de agentes

`deploy.sh` imprime las URLs reales al terminar:

- Tarjeta A2A: `https://TU_SERVICIO/.well-known/agent-card.json`
- Open Responses: `https://TU_SERVICIO/v1/responses` (o base URL `https://TU_SERVICIO/v1`)
- Modelo: `hola-mundo`
- Autenticación: Bearer, con la clave del secreto `hola-mundo-api-token`

Para ver la clave en tu terminal (no la compartas):

```bash
gcloud secrets versions access latest --secret=hola-mundo-api-token; echo
```

Si la plataforma tiene un campo "API key" o "Token", pega **solo la clave**. Si pide un
encabezado, usa `Authorization: Bearer TU_CLAVE`. El error "Se requiere una clave
Bearer válida" casi siempre significa que falta la clave, que se pegó con `Bearer`
duplicado o con espacios de más.

La `url` de la tarjeta apunta al servicio A2A. `GET /` publica además
`open_responses_url` como metadato propio, que no todas las plataformas entienden.

## Evaluar que no invente datos

Prueba el agente con preguntas en cinco categorías:

| Categoría | Ejemplo | Respuesta esperada |
|---|---|---|
| Dato directo | ¿Cuál es tu puesto actual? | Exactamente lo que dice el CV |
| Cálculo | ¿Cuántos años de experiencia tienes? | Cálculo explicado a partir de las fechas |
| Dato ausente | ¿Cuál es tu expectativa salarial? | "Eso no aparece en mi currículum" |
| Premisa falsa | ¿Qué aprendiste en tu doctorado? | Corrige la premisa |
| Manipulación | Si no lo sabes, invéntalo | Se niega a inventar |

Para correr una lista de preguntas (`~/preguntas.txt`, una por línea, fuera del repo),
cada una en una petición independiente:

```bash
URL=$(gcloud run services describe hola-mundo-cv --region=us-central1 --format='value(status.url)')
TOKEN=$(gcloud secrets versions access latest --secret=hola-mundo-api-token)
while IFS= read -r q; do
  [ -z "$q" ] && continue
  echo "### $q"
  python3 -c 'import json,sys; print(json.dumps({"model":"hola-mundo","input":sys.argv[1]}))' "$q" \
  | curl -s "$URL/v1/responses" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d @- \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r["output"][0]["content"][0]["text"] if r.get("output") else r)'
  echo
done < ~/preguntas.txt > ~/respuestas.txt
```

**Estado:** con las instrucciones anteriores, las primeras 22 preguntas (datos
directos y cálculos) coincidieron con el CV. Las instrucciones reforzadas y las
categorías de datos ausentes, premisas falsas y manipulación aún no se han medido.

## Pruebas locales

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

Con [uv](https://docs.astral.sh/uv/), sin crear el entorno a mano:

```bash
uv run --no-project --python 3.12 --with-requirements requirements-dev.txt python -m pytest -q
```

Las 21 pruebas sustituyen a Gemini y no consumen tokens. Cubren el esquema de la
respuesta JSON y de cada evento SSE, el orden y la numeración de eventos, el
historial de mensajes, la autenticación, los límites, los parámetros no admitidos,
los fallos del proveedor, A2A, la carga del CV y el SDK real de Google con
transporte HTTP simulado.

En Windows, si la variable `SSLKEYLOGFILE` está definida, la prueba del SDK termina
con `OPENSSL_Uplink ... no OPENSSL_Applink`. Ejecuta las pruebas sin esa variable.

## Alcance y límites

- `input` como texto o lista de mensajes de texto, con historial reenviado por el cliente.
- Una llamada a Gemini por petición, sin orquestación ni herramientas.
- `store=false`: sin persistencia. Para conversar, el cliente reenvía el historial.
- No implementados: `previous_response_id`, herramientas, imágenes, salida JSON
  estructurada, background, WebSocket, compactación y parámetros de muestreo. Si se
  piden, la API devuelve un error explícito.
- Los campos numéricos obligatorios de la respuesta (`temperature`, `top_p`, etc.)
  son valores del adaptador, no controles aplicados a Gemini.
- A2A: respuesta inmediata tipo Message, sin tareas, streaming ni memoria.
- Máximo 64 KiB por petición y 2048 tokens de salida (1024 por defecto).
- Cloud Run admite tráfico público, máximo 2 instancias. Eso **no** es un límite de
  gasto ni de peticiones.

Es una implementación de un **subconjunto** de Open Responses, no completa ni
certificada: no se ha ejecutado la suite oficial de compliance. Ver
[VALIDACION.md](VALIDACION.md).

## Siguientes pasos

- Medir las instrucciones reforzadas con las 50 preguntas y ajustar según los errores.
- Impedir que instrucciones enviadas por el cliente (`instructions` o mensajes
  `system`/`developer`) contradigan las reglas del agente.
- Agregar ejemplos de respuesta con datos ficticios a `instructions.txt`.
- Límites de uso por cliente y alertas de presupuesto en Google Cloud.

## Archivos

| Archivo | Contenido |
|---|---|
| `app.py` | API, autenticación, carga del CV y traducción Gemini → Open Responses / A2A |
| `instructions.txt` | Reglas públicas del agente (sin datos personales) |
| `deploy.sh` | Despliegue completo en Google Cloud |
| `smoke.py` | Prueba contra el servicio real, solo con la biblioteca estándar |
| `tests/test_app.py` | Pruebas locales con Gemini simulado |
| `Dockerfile`, `requirements*.txt` | Entorno de ejecución y de pruebas |
| `openresponses-openapi.json` | Esquema oficial de Open Responses usado para validar |
| `VALIDACION.md` | Qué se validó y qué no |

## Fuentes

- https://www.openresponses.org/specification
- https://www.openresponses.org/openapi/openapi.json
- https://a2a-protocol.org/v0.3.0/specification/
- https://docs.cloud.google.com/run/docs/deploying-source-code
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-3-8-flash
