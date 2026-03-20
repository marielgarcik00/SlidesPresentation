# Slides desde texto

El usuario envía un texto; Gemini lo interpreta, lo estructura y lo divide en partes; cada parte se asigna a una plantilla de slide de una presentación base (Google Slides); se genera una copia en una carpeta de Drive con las slides rellenadas y listas.

## Requisitos

- Python 3.10+
- Cuenta de Google con la plantilla en Google Slides y una carpeta en Drive.
- **Credenciales**:
  - **Gemini (Vertex AI en GCP)**: API de [Vertex AI Generative](https://cloud.google.com/vertex-ai/generative-ai/docs/start/quickstarts/quickstart-multimodal) habilitada en el proyecto; en `.env`: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` (p. ej. `us-central1`). Autenticación con [ADC](https://cloud.google.com/docs/authentication/application-default-credentials) (`gcloud auth application-default login` o `GOOGLE_APPLICATION_CREDENTIALS`) y cuenta de servicio con rol **Vertex AI User**. Opcional: `GEMINI_VERTEX_CREDENTIALS_PATH` si el JSON de Slides/Drive es distinto.
  - **Google Slides/Drive**: archivo `credentials.json` de una cuenta de servicio con acceso a la presentación y a la carpeta de Drive. Variable opcional `GOOGLE_CREDENTIALS_PATH` (por defecto `./credentials.json`).

## Cómo usar

1. **Copiar credenciales**  
   Poner el `credentials.json` de tu cuenta de servicio en la raíz del proyecto (o indicar la ruta en `GOOGLE_CREDENTIALS_PATH`).

2. **Variables de entorno**  
   Crear un `.env` a partir de `.env.example` y definir proyecto y región de Vertex (`GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`) más autenticación GCP (ver `.env.example`).

3. **Instalar dependencias**
   ```bash
   pip install -r requirements.txt
   ```

4. **Arrancar el servidor**
   ```bash
   python app.py
   ```
   Abrir `http://localhost:8000`.

## Flujo en el frontend

1. **Texto**: pegar el contenido que quieras convertir en slides.
2. **URLs**: URL de la presentación plantilla (Google Slides) y URL de la carpeta de Drive donde guardar la copia.
3. **Interpretar**: ver qué entiende Gemini del texto.
4. **Estructurar**: ver título, tipo de contenido y subtítulos.
5. **Dividir y asignar**: dividir el texto en partes y asignar cada parte a una slide de la plantilla.
6. **Generar copia**: crear la copia en Drive, con las slides elegidas y rellenadas, y obtener el enlace.

## Estructura del proyecto

- `app.py` — API FastAPI (endpoints del flujo).
- `llm/` — Módulos Gemini: `config` (RPM/batch), `rate_limit`, `client`, `prompts`, `interpret`, `segmentation`, `slide_fill`.
- `gemini_parser.py` — Reexporta `llm` (compatibilidad).

### Cuotas y ritmo (Vertex AI)

Por defecto: **`gemini-2.0-flash`**. Si ves 404, cambiá `GEMINI_MODEL` según los modelos disponibles en tu región de Vertex. La app espacia llamadas según el modelo (~12/min para Flash, ~1.5/min para Pro, ~55/min para Gemma) para no saturar; ajustá `GEMINI_RPM_LIMIT` si tu cuota de proyecto es distinta. Más slides por batch → menos llamadas: `GEMINI_BATCH_CHUNK=9` (ver `.env.example`).
- `context_service.py` — Carga de `context.json` y resolución de plantillas ($) y placeholders (#).
- `slides_automation.py` — Google Slides/Drive: leer presentación, copiar, reordenar, reemplazar marcadores.
- `context.json` — Definición de plantillas ($) y marcadores (#) para cada tipo de slide.
- `static/index.html` — Frontend mínimo.

## Plantilla de Google Slides

La presentación base debe tener en cada slide:

- **Identificadores** con `$`: por ejemplo `$cover_presentation`, `$descriptive_presentation`, para que el sistema sepa qué tipo de slide es.
- **Placeholders** con `#`: por ejemplo `#main_title`, `#description`, que se reemplazan por el contenido generado por Gemini.

Los nombres deben coincidir con los definidos en `context.json`.
