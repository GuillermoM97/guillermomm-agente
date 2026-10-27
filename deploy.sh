#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PROJECT_ID="${PROJECT_ID:-project-9dd3a621-40da-47fe-a6f}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-hola-mundo-cv}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.8-flash}"
RUNTIME_SA="cv-agent@${PROJECT_ID}.iam.gserviceaccount.com"
BUILD_SA="cv-agent-build@${PROJECT_ID}.iam.gserviceaccount.com"
SECRET="hola-mundo-api-token"
CV_SECRET="agente-cv"
# Opcional: ruta a tu CV en texto, FUERA del repositorio (p. ej. CV_FILE=~/cv.txt).
CV_FILE="${CV_FILE:-}"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com aiplatform.googleapis.com \
  secretmanager.googleapis.com iam.googleapis.com cloudresourcemanager.googleapis.com

if ! gcloud iam service-accounts describe "$RUNTIME_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create cv-agent --project="$PROJECT_ID" --display-name="Agente de curriculum"
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/aiplatform.user --condition=None --quiet >/dev/null

if ! gcloud iam service-accounts describe "$BUILD_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create cv-agent-build --project="$PROJECT_ID" --display-name="Build del agente CV"
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BUILD_SA" --role=roles/run.builder --condition=None --quiet >/dev/null

# Crear una sola vez. Repetir el despliegue conserva la misma clave.
if ! gcloud secrets describe "$SECRET" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets create "$SECRET" --project="$PROJECT_ID" --replication-policy=automatic
fi
VERSION="$(gcloud secrets versions list "$SECRET" --project="$PROJECT_ID" \
  --filter='state=ENABLED' --sort-by='~createTime' --limit=1 --format='value(name)')"
if [[ -z "$VERSION" ]]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' | \
    gcloud secrets versions add "$SECRET" --project="$PROJECT_ID" --data-file=- >/dev/null
fi
gcloud secrets add-iam-policy-binding "$SECRET" --project="$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor --condition=None --quiet >/dev/null

# El repositorio es público: el CV solo se guarda en Secret Manager.
SECRETS="AGENT_API_TOKEN=$SECRET:latest"
if [[ -n "$CV_FILE" ]]; then
  [[ -s "$CV_FILE" ]] || { echo "No existe o está vacío: $CV_FILE" >&2; exit 1; }
  case "$(realpath "$CV_FILE")" in
    "$(pwd)"/*) echo "Guarda el CV fuera del repositorio, no en $(pwd)" >&2; exit 1 ;;
  esac
  if ! gcloud secrets describe "$CV_SECRET" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$CV_SECRET" --project="$PROJECT_ID" --replication-policy=automatic
  fi
  gcloud secrets versions add "$CV_SECRET" --project="$PROJECT_ID" --data-file="$CV_FILE" >/dev/null
  echo "CV cargado en Secret Manager: $CV_SECRET."
fi
if [[ -n "$(gcloud secrets versions list "$CV_SECRET" --project="$PROJECT_ID" \
    --filter='state=ENABLED' --limit=1 --format='value(name)' 2>/dev/null)" ]]; then
  gcloud secrets add-iam-policy-binding "$CV_SECRET" --project="$PROJECT_ID" \
    --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor --condition=None --quiet >/dev/null
  SECRETS="$SECRETS,/cv/cv.txt=$CV_SECRET:latest"
else
  echo "Sin CV: el agente dirá que aún no se ha cargado. Usa CV_FILE=~/cv.txt bash deploy.sh"
fi

echo "Construyendo y desplegando; puede tardar varios minutos."
gcloud run deploy "$SERVICE" --project="$PROJECT_ID" --region="$REGION" \
  --source=. --service-account="$RUNTIME_SA" \
  --build-service-account="projects/$PROJECT_ID/serviceAccounts/$BUILD_SA" \
  --allow-unauthenticated --port=8080 --cpu=1 --memory=512Mi \
  --min-instances=0 --max-instances=2 --concurrency=8 --timeout=120 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=global,GEMINI_MODEL=$GEMINI_MODEL" \
  --set-secrets="$SECRETS" --quiet

SERVICE_URL="$(gcloud run services describe "$SERVICE" --project="$PROJECT_ID" \
  --region="$REGION" --format='value(status.url)')"
gcloud run services update "$SERVICE" --project="$PROJECT_ID" --region="$REGION" \
  --update-env-vars="PUBLIC_BASE_URL=$SERVICE_URL" --quiet

export SERVICE_URL
AGENT_API_TOKEN="$(gcloud secrets versions access latest --secret="$SECRET" --project="$PROJECT_ID")"
export AGENT_API_TOKEN
python3 smoke.py
unset AGENT_API_TOKEN

printf '\nTarjeta A2A: %s/.well-known/agent-card.json\n' "$SERVICE_URL"
printf 'Open Responses: %s/v1/responses\nModelo: hola-mundo\n' "$SERVICE_URL"
echo "Autenticación: Bearer. La clave está en Secret Manager: $SECRET."
