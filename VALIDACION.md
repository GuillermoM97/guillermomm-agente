# Validación del hola mundo

Pruebas locales:
- Respuesta JSON contra el esquema oficial Open Responses 2026-04-24.
- Cada evento SSE contra su esquema, orden, números de secuencia y texto final.
- Mensajes con historial y roles traducidos a Gemini.
- Clave obligatoria, JSON inválido, tamaño máximo y parámetros no soportados.
- Fallo del proveedor durante streaming sin exponer su mensaje interno.
- Límite de tokens y bloqueo del proveedor.
- Tarjeta y mensaje A2A síncrono.
- SDK oficial google-genai real con transporte HTTP simulado: petición a Gemini,
  lectura de SSE, consumo de tokens y exclusión de fragmentos de razonamiento.
- Sintaxis de deploy.sh y compilación Python.

No se ejecutaron aquí:
- Acceso real a tu proyecto, IAM, facturación o Gemini.
- Construcción Docker ni despliegue en Cloud Run (herramientas no disponibles).
- Importación en tu plataforma, cuyo nombre y contrato aún no se conocen.
- Suite oficial de compliance completa de Open Responses o A2A.

`bash deploy.sh` ejecutará la prueba real de Gemini después de desplegar desde
tu Cloud Shell. El currículum se carga desde Secret Manager (ver README).
