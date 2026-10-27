# guillermomm-agente

Primer paso del agente de currículum: texto mediante Open Responses HTTP/SSE,
tarjeta A2A 0.3 y `message/send` síncrono. Sin CV todavía.

## Desplegar desde un repositorio en Cloud Shell

Clona este repositorio usando su URL de GitHub y ejecuta:

```bash
cd guillermomm-agente
bash deploy.sh
```

Para actualizar el servicio después de un cambio en el repositorio:

```bash
git pull --ff-only
bash deploy.sh
```

El repositorio no necesita claves de Google: Cloud Run usa su cuenta de servicio
y la clave de acceso del agente se guarda en Secret Manager.

## Cargar el currículum (fuera del repositorio)

El repositorio es público, así que el CV NUNCA se guarda aquí. Vive en el secreto
`agente-cv` de Secret Manager y Cloud Run lo monta en `/cv/cv.txt`.

1. Escribe tu CV en texto plano (`cv.txt`, máximo 64 KiB). Incluye solo lo que
   aceptas que el agente comparta con quien tenga la clave Bearer.
2. Súbelo a tu carpeta personal de Cloud Shell (⋮ → Subir archivo), no al repo.
3. Despliega indicando la ruta:

```bash
CV_FILE=~/cv.txt bash deploy.sh
```

Para actualizarlo, repite el comando con el archivo nuevo. Después de cargarlo,
`bash deploy.sh` sin `CV_FILE` conserva el CV ya guardado. El script se niega a
usar un CV que esté dentro de la carpeta del repositorio, y `.gitignore`,
`.gcloudignore` y `.dockerignore` excluyen `cv*.txt`, `cv*.md`, PDF y Word.

## Alternativa: desplegar desde el ZIP en Cloud Shell

1. Sube `hola-mundo-google.zip` con el menú de Cloud Shell → Subir archivo.
2. Ejecuta desde la carpeta donde lo subiste:

```bash
unzip hola-mundo-google.zip
cd hola-mundo-google
bash deploy.sh
```

El proyecto predeterminado es `project-9dd3a621-40da-47fe-a6f`.
La región de Cloud Run es `us-central1`; Gemini usa el endpoint `global`.
El modelo configurado es `gemini-3.8-flash`. Puedes cambiar estas variables:

```bash
PROJECT_ID=tu-proyecto REGION=us-central1 GEMINI_MODEL=gemini-3.8-flash bash deploy.sh
```

El script habilita APIs, crea/reutiliza cuentas de servicio `cv-agent` y
`cv-agent-build`, concede los roles de invocación y construcción, crea una clave
en Secret Manager, construye y despliega. Necesitas permisos para esas operaciones.
Si IAM acaba de cambiar y el build devuelve PERMISSION_DENIED, espera unos minutos
y vuelve a ejecutar el mismo script. Si persiste, revisa el permiso concreto.

El servicio de Cloud Run admite tráfico público. La aplicación protege
`/v1/responses`, `/v1/models` y `/a2a` con una clave Bearer obligatoria.
La tarjeta, información de conexión y `/health` son públicas. La clave no se
incluye en la tarjeta. Se aplican cargos normales de Google Cloud por uso.
Máximo dos instancias; esto NO es un límite de gasto ni un rate limit.

Al terminar, el script prueba tarjeta, respuesta real de Gemini y streaming real.
Si la prueba falla, el despliegue puede existir: lee el error antes de volver a probar.
Un `/health` exitoso solo confirma que la aplicación está viva, no acceso a Gemini.

## Registrar en tu plataforma

El script imprime las URLs reales. Usa:

- Tarjeta: `https://TU_SERVICIO/.well-known/agent-card.json`
- Open Responses: `https://TU_SERVICIO/v1/responses`
- Si el formulario pide **base URL** en lugar de endpoint completo: `https://TU_SERVICIO/v1`
- Modelo: `hola-mundo`
- Autenticación: Bearer; copia la clave desde el secreto `hola-mundo-api-token`.

Para ver la clave en tu propia terminal (no la compartas en el chat):

```bash
gcloud secrets versions access latest \
  --secret=hola-mundo-api-token \
  --project=project-9dd3a621-40da-47fe-a6f
```

La tarjeta contiene campos estándar A2A 0.3. Su `url` apunta al servicio A2A,
no a Open Responses. La descripción menciona Open Responses y `GET /` publica
`open_responses_url` como metadato propio de esta demo. NO asumimos que tu
plataforma entienda ese metadato. Si no completa el formulario, introduce la URL
manualmente. La detección automática requiere conocer la plataforma y su contrato.

## Alcance probado

- `input` como texto o lista de mensajes de texto, incluyendo historial reenviado.
- Una llamada a Gemini por petición, sin ADK: este hola mundo no necesita
  orquestación. La lógica del agente está en `instructions.txt` y el backend.
- Streaming real: los fragmentos de Gemini se convierten a eventos Open Responses.
- `store=false`; no persistencia. Para conversar, el cliente reenvía el historial.
- `previous_response_id`, herramientas, imágenes, JSON estructurado, background,
  WebSocket y compactación no implementados; opciones no soportadas devuelven error.
- Parámetros de muestreo no admitidos en esta demo. Los campos numéricos
  obligatorios de la respuesta (`temperature`, `top_p`, etc.) son valores del
  adaptador; no son controles aplicados a Gemini 3.8.
- A2A 0.3: respuesta inmediata tipo Message. No tareas, streaming A2A ni memoria.
- Tamaño máximo 64 KiB y salida máxima 2048 tokens por petición.

Es una demo de un subconjunto de Open Responses, NO una implementación completa
ni certificada. No se ha ejecutado la suite oficial ni probado el importador destino.
La base de validación es el esquema oficial 2026-04-24 incluido en este proyecto.

## Pruebas locales

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

Las pruebas sustituyen Gemini y no consumen tokens. Para una prueba real local:

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=project-9dd3a621-40da-47fe-a6f
export AGENT_API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
uvicorn app:app --host 0.0.0.0 --port 8080
```

Conserva ese terminal abierto. Las llamadas necesitan la misma clave Bearer.

## Archivos y siguientes pasos

- `app.py`: API y traducción Gemini → Open Responses.
- `instructions.txt`: instrucciones públicas; el CV se añade desde Secret Manager.
- `deploy.sh`: despliegue en tu proyecto.
- `smoke.py`: prueba real sin dependencias adicionales.
- `tests/test_app.py`: pruebas locales de protocolo y errores.
- `Dockerfile`, `requirements*.txt`: entorno de ejecución y pruebas.
- `openresponses-openapi.json`: esquema de referencia oficial.

Antes de usar datos reales, completar la integración con tu importador,
controles de acceso por usuario, límites de uso y la persistencia si se requiere.

Fuentes:
- https://www.openresponses.org/specification
- https://www.openresponses.org/openapi/openapi.json
- https://a2a-protocol.org/v0.3.0/specification/
- https://docs.cloud.google.com/run/docs/deploying-source-code
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-3-8-flash
