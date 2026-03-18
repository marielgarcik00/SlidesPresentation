# app.py — API FastAPI para el flujo: texto → Gemini interpreta → segmenta y asigna a slides → copia en Drive rellenada.

import os
import logging
import time
from dataclasses import dataclass
from typing import Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from slides_automation import GoogleSlidesAutomation
from gemini_parser import (
    ask_gemini,
    ask_gemini_title_and_subtitles,
    ask_gemini_batch_for_slides,
    ask_gemini_for_slide,
    segment_text_into_parts,
)
from context_service import (
    get_context_summary_for_segmenter,
    get_preferred_templates_for_content,
    find_best_slide_index,
    get_template_and_placeholders_by_identifier,
)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"))
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Helpers: credenciales y automatización
# Obtiene la ruta del archivo de credenciales (en el .env)
def get_credentials_path() -> str:
    path = os.getenv("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
    if not os.path.isabs(path):
        path = os.path.normpath(os.path.join(PROJECT_ROOT, path))
    return path

# Comprueba que exista el archivo de credenciales y devuelve su ruta
def validate_credentials() -> str:
    creds_path = get_credentials_path()
    if not os.path.exists(creds_path):
        raise HTTPException(
            status_code=400,
            detail=f"Archivo de credenciales no encontrado: {creds_path}",
        )
    return creds_path

#Crea una instancia de GoogleSlidesAutomation con la ruta de credenciales dada
def create_automation(credentials_path: str) -> GoogleSlidesAutomation:
    return GoogleSlidesAutomation(credentials_path)

# Registra el error y re-lanza HTTPException
def handle_api_error(context: str, error: Exception) -> None:
    logger.error("✗ Error %s: %s", context, str(error))
    if isinstance(error, ValueError):
        raise HTTPException(status_code=400, detail=str(error))
    raise HTTPException(status_code=500, detail=f"Error al procesar: {str(error)}")

# Detecta si el error es de cuota/límite de API 
def _is_quota_error(err: Exception) -> bool:
    s = str(err).upper()
    return "429" in s or "RESOURCE_EXHAUSTED" in s or "QUOTA" in s

# Resultado de run_segment_and_assign: segmentos asignados a slides y lista de slides no usadas
@dataclass
class SegmentAssignResult:
    segments_with_slides: List[Dict]
    slides_used: List[Dict]
    slides_not_used: List[int]

# Asigna cada segmento a la mejor plantilla de slide y pide a Gemini el JSON para rellenar cada una. Respeta una pausa entre llamadas a Gemini.
# Devuelve un único objeto con segments_with_slides, slides_used y slides_not_used.
def run_segment_and_assign(
    segments: List[Dict],
    slides: List[Dict],
    all_indices: set,
    delay_between_calls: int,
) -> SegmentAssignResult:
    """
    Asigna plantillas sin N llamadas a Gemini: una sola llamada en batch para rellenar
    todas las slides (además de la segmentación, que ya ocurrió antes).
    """
    use_batch = os.getenv("GEMINI_BATCH_SLIDES", "true").lower() in ("1", "true", "yes")
    used_indices = set()
    segments_with_slides = []
    slides_used = []
    pending_jobs: List[Dict] = []
    rows_meta: List[Dict] = []

    for seg in segments:
        num = seg.get("num_items")
        if num is None or not isinstance(num, int):
            num = 0
        content_type_raw = seg.get("content_type") or "descripcion"
        content_type_str = content_type_raw if isinstance(content_type_raw, str) else str(content_type_raw)
        structured = {
            "content_type": content_type_str.strip().lower(),
            "subtitles": [{}] * max(0, num),
        }
        preferred = get_preferred_templates_for_content(structured)
        best_index, matched_template = find_best_slide_index(slides, preferred, exclude_indices=None)
        text_raw = seg.get("text") or ""
        text_str = text_raw if isinstance(text_raw, str) else str(text_raw)
        text_preview = text_str[:200]
        if len(text_str) > 200:
            text_preview += "..."

        if best_index is None or matched_template is None:
            rows_meta.append({"kind": "skip", "seg": seg, "text_preview": text_preview})
            continue

        context_template, placeholders = get_template_and_placeholders_by_identifier(matched_template)
        fill_idx = None
        if context_template and placeholders:
            fill_idx = len(pending_jobs)
            pending_jobs.append({
                "text": text_str,
                "placeholders": placeholders,
                "context_template": context_template,
            })
        rows_meta.append({
            "kind": "ok",
            "seg": seg,
            "text_preview": text_preview,
            "best_index": best_index,
            "matched_template": matched_template,
            "fill_idx": fill_idx,
            "text_str": text_str,
            "placeholders": placeholders if context_template and placeholders else [],
            "context_template": context_template,
        })

    filled: List[Dict] = []
    if pending_jobs:
        if use_batch:
            filled = ask_gemini_batch_for_slides(pending_jobs)
            while len(filled) < len(pending_jobs):
                filled.append({})
            logger.info("Relleno batch: %s slides en ~%s llamada(s) a Gemini", len(pending_jobs), 1 + (len(pending_jobs) - 1) // 6)
        else:
            first = True
            for job in pending_jobs:
                if not first:
                    time.sleep(delay_between_calls)
                first = False
                filled.append(
                    ask_gemini_for_slide(job["text"], job["placeholders"], job["context_template"])
                )

    for row in rows_meta:
        if row["kind"] == "skip":
            segments_with_slides.append({
                "part_index": row["seg"].get("part_index", len(segments_with_slides)),
                "content_type": row["seg"].get("content_type"),
                "text_preview": row["text_preview"],
                "slide_index": None,
                "template": None,
                "json_for_slide": None,
                "is_duplicate": False,
                "insert_after_slide": None,
            })
            continue

        best_index = row["best_index"]
        matched_template = row["matched_template"]
        json_for_slide = None
        if row["fill_idx"] is not None and row["fill_idx"] < len(filled):
            json_for_slide = filled[row["fill_idx"]]

        is_duplicate = best_index in used_indices
        if not is_duplicate:
            used_indices.add(best_index)

        segments_with_slides.append({
            "part_index": row["seg"].get("part_index", len(segments_with_slides)),
            "content_type": row["seg"].get("content_type"),
            "text_preview": row["text_preview"],
            "slide_index": best_index,
            "template": matched_template,
            "json_for_slide": json_for_slide,
            "is_duplicate": is_duplicate,
            "insert_after_slide": best_index if is_duplicate else None,
        })
        slides_used.append({
            "slide_index": best_index,
            "template": matched_template,
            "json_for_slide": json_for_slide,
            "is_duplicate": is_duplicate,
            "insert_after_slide": best_index if is_duplicate else None,
        })

    slides_not_used = sorted(all_indices - used_indices)
    return SegmentAssignResult(
        segments_with_slides=segments_with_slides,
        slides_used=slides_used,
        slides_not_used=slides_not_used,
    )


# Resultado de run_generate_copy_from_segment que genera la copia en Drive: ID, URL y secuencia de slides
@dataclass
class GenerateCopyResult:    
    new_presentation_id: str
    new_presentation_url: str
    slides_count: int
    slide_sequence: List[int]

# Convierte cada ítem de slides_used (con clave "template") en el índice de slide
# que coincide en la presentación. Reutiliza find_best_slide_index.
# Lanza ValueError si algún template no existe en la presentación.
def resolve_slide_sequence(slides: List[Dict], slides_used: List[Dict]) -> List[int]:
    sequence = []
    for u in slides_used:
        if not isinstance(u, dict):
            continue
        template_raw = u.get("template") or ""
        template = (template_raw if isinstance(template_raw, str) else str(template_raw)).strip()
        if not template:
            continue
        best_index, _ = find_best_slide_index(slides, [template], exclude_indices=None)
        if best_index is None:
            raise ValueError(
                f"No se encontró ninguna slide que coincida con la plantilla '{template}'."
            )
        sequence.append(best_index)
    if not sequence:
        raise ValueError(
            "No se pudo resolver ninguna plantilla. Revisá que cada ítem tenga 'template'."
        )
    return sequence

# Crea una copia de la presentación en la carpeta indicada, con las slides en el orden de slides_used, y rellena cada slide con su json_for_slide. Elimina los $.
# Lanza ValueError si la URL es inválida, no hay slides o falta algún template.
def run_generate_copy_from_segment(
    automation: GoogleSlidesAutomation,
    presentation_url: str,
    folder_url_or_id: str,
    new_name: str,
    slides_used: List[Dict],
) -> GenerateCopyResult:
    slides = automation.get_presentation_slides(presentation_url)
    if not slides:
        raise ValueError("No se pudo leer las slides de la presentación.")
    slide_sequence = resolve_slide_sequence(slides, slides_used)
    new_id = automation.copy_presentation_advanced(
        presentation_url=presentation_url,
        slide_counts={},
        folder_url_or_id=folder_url_or_id,
        new_name=new_name,
        slide_sequence=slide_sequence,
    )
    new_url = f"https://docs.google.com/presentation/d/{new_id}/edit"
    for i, u in enumerate(slides_used):
        if not isinstance(u, dict):
            continue
        content = u.get("json_for_slide")
        if not isinstance(content, dict):
            content = {}
        if content:
            automation.replace_components_in_slide_by_index(
                presentation_url=new_url,
                slide_index=i,
                replacements=content,
                remove_identifiers=True,
            )
    return GenerateCopyResult(
        new_presentation_id=new_id,
        new_presentation_url=new_url,
        slides_count=len(slide_sequence),
        slide_sequence=slide_sequence,
    )


# -----------------------------------------------------------------------------
# Modelos de solicitud/respuesta
# Solicitud: solo texto para que Gemini lo interprete en bruto.
class AskGeminiRequest(BaseModel):
    text: str

#  Solicitud: texto + URL de presentación para segmentar y asignar cada parte a una slide
class SegmentAndAssignRequest(BaseModel):
    text: str
    presentation_url: str

#Solicitud: URL plantilla + carpeta Drive + nombre opcional + resultado de segment-and-assign
class GenerateCopyFromSegmentRequest(BaseModel):
    presentation_url: str
    folder_url_or_id: str
    new_name: str = None
    slides_used: List[Dict]


# -----------------------------------------------------------------------------
# App
app = FastAPI(
    title="Slides desde texto",
    description="API: texto → Gemini interpreta y segmenta → copia en Drive con slides rellenadas",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Endpoints

# Sirve el frontend estático si existe static/index.html;
# si no, devuelve un JSON con información del servicio y endpoints.
@app.get("/")
async def root():
    index_path = os.path.join(PROJECT_ROOT, "static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return {
        "service": "Slides desde texto",
        "version": "1.0.0",
        "endpoints": {
            "health": "GET /api/health",
            "ask_gemini": "POST /api/ask-gemini",
            "ask_gemini_structure": "POST /api/ask-gemini-structure",
            "segment_and_assign": "POST /api/segment-and-assign-slides",
            "generate_copy": "POST /api/generate-copy-from-segment",
        },
    }

# Comprueba que el servicio esté activo y que exista el archivo de credenciales
@app.get("/api/health")
async def health_check():
    try:
        validate_credentials()
        return {"status": "healthy", "message": "Servicio activo y listo"}
    except HTTPException as e:
        return {"status": "warning", "message": "Archivo de credenciales no configurado"}

# Envía el texto a Gemini con la instrucción por defecto y devuelve la interpretación en texto plano (qué entiende la IA del texto).
@app.post("/api/ask-gemini")
async def ask_gemini_endpoint(request: AskGeminiRequest):
    try:
        text = (request.text or "").strip()
        if not text:
            raise ValueError("El texto no puede estar vacío.")
        response = ask_gemini(text)
        return {"success": True, "response": response or "(La IA no devolvió texto)"}
    except Exception as e:
        if _is_quota_error(e):
            raise HTTPException(
                status_code=429,
                detail=(
                    "Límite de la API de Gemini alcanzado (la app ya reintentó 3 veces). "
                    "Probá en unos 5–10 minutos, mañana, o creá otra API key en https://aistudio.google.com/apikey y ponela en el .env."
                ),
            )
        handle_api_error("ask-gemini", e)

# Pide a Gemini que estructure el texto: content_type, main_title, has_subtitles y lista de subtitles con title/description.
@app.post("/api/ask-gemini-structure")
async def ask_gemini_structure_endpoint(request: AskGeminiRequest):
    try:
        text = (request.text or "").strip()
        if not text:
            raise ValueError("El texto no puede estar vacío.")
        structured = ask_gemini_title_and_subtitles(text)
        return {"success": True, "structured": structured}
    except Exception as e:
        if _is_quota_error(e):
            raise HTTPException(
                status_code=429,
                detail="Límite de Gemini. «Dividir y asignar» usa pocas llamadas (batch); esperá 5–10 min o otra API key en https://aistudio.google.com/apikey",
            )
        handle_api_error("ask-gemini-structure", e)

#Carga contexto de plantillas. Obtiene las slides de la presentación (con $).
# Gemini divide el texto en partes por tema/largo. Asigna cada parte a la mejor plantilla y genera el JSON para rellenar cada slide. Devuelve segments y slides_used.
@app.post("/api/segment-and-assign-slides")
async def segment_and_assign_slides_endpoint(request: SegmentAndAssignRequest):
    text = (request.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="El texto no puede estar vacío.")
    url = (request.presentation_url or "").strip()
    if not url or "docs.google.com/presentation" not in url or "/d/" not in url:
        raise HTTPException(
            status_code=400,
            detail="Se necesita la URL de la presentación de Google Slides.",
        )
    try:
        context_summary = get_context_summary_for_segmenter()
        creds_path = validate_credentials()
        automation = create_automation(creds_path)
        slides = automation.get_presentation_slides(url)
        if not slides:
            raise HTTPException(
                status_code=400,
                detail="No se pudo leer la presentación o no tiene slides. Revisá la URL y las credenciales.",
            )
        all_indices = {s["index"] for s in slides}
        segments = segment_text_into_parts(text, context_summary=context_summary)
        if not segments:
            raise HTTPException(
                status_code=422,
                detail="La IA no pudo dividir el texto en partes. Probá con otro texto.",
            )
        delay_between_calls = int(os.getenv("GEMINI_DELAY_BETWEEN_CALLS", "20"))
        batch_on = os.getenv("GEMINI_BATCH_SLIDES", "true").lower() in ("1", "true", "yes")
        if batch_on:
            n_batch = 1 + (max(0, len(segments) - 1) // 6)
            logger.info(
                "Dividir y asignar: %s segmentos → ~%s llamadas Gemini (1 segmentar + %s batch relleno)",
                len(segments), 1 + n_batch, n_batch,
            )
        else:
            logger.info(
                "Dividir y asignar (sin batch): %s segmentos → %s llamadas (pausa %ss)",
                len(segments), 1 + len(segments), delay_between_calls,
            )
        result = run_segment_and_assign(
            segments, slides, all_indices, delay_between_calls,
        )
        return {
            "success": True,
            "segments": result.segments_with_slides,
            "slides_used": result.slides_used,
            "slides_not_used": result.slides_not_used,
        }
    except HTTPException:
        raise
    except Exception as e:
        if _is_quota_error(e):
            raise HTTPException(
                status_code=429,
                detail="Límite de Gemini. «Dividir y asignar» usa pocas llamadas (batch); esperá 5–10 min o otra API key en https://aistudio.google.com/apikey",
            )
        logger.exception("Error en segment-and-assign-slides")
        handle_api_error("segment-and-assign-slides", e)

# Crea una copia de la presentación en la carpeta de Drive indicada, con las slides
# en el orden y cantidad dados por slides_used (cada ítem = una slide en la copia),
# y rellena cada slide con su json_for_slide. Elimina los $ de cada slide.
@app.post("/api/generate-copy-from-segment")
async def generate_copy_from_segment_endpoint(request: GenerateCopyFromSegmentRequest):
    url = (request.presentation_url or "").strip()
    if not url or "docs.google.com/presentation" not in url or "/d/" not in url:
        raise HTTPException(status_code=400, detail="URL de presentación inválida.")
    folder = (request.folder_url_or_id or "").strip()
    if not folder:
        raise HTTPException(
            status_code=400,
            detail="Indicá la carpeta de Drive (URL o ID) donde guardar la copia.",
        )
    slides_used = request.slides_used or []
    if not slides_used:
        raise HTTPException(
            status_code=400,
            detail="No hay slides para generar. Ejecutá primero «Dividir y asignar a slides».",
        )
    try:
        creds_path = validate_credentials()
        automation = create_automation(creds_path)
        result = run_generate_copy_from_segment(
            automation=automation,
            presentation_url=url,
            folder_url_or_id=folder,
            new_name=request.new_name or "Presentación generada desde texto",
            slides_used=slides_used,
        )
        return {
            "success": True,
            "message": "Se creó la copia en Drive y se rellenaron las slides.",
            "new_presentation_id": result.new_presentation_id,
            "new_presentation_url": result.new_presentation_url,
            "slides_count": result.slides_count,
            "slide_sequence": result.slide_sequence,
        }
    except HTTPException:
        raise
    except Exception as e:
        handle_api_error("generate-copy-from-segment", e)


if os.path.exists(os.path.join(PROJECT_ROOT, "static")):
    app.mount("/static", StaticFiles(directory=os.path.join(PROJECT_ROOT, "static")), name="static")


if __name__ == "__main__":
    import uvicorn
    port = 8080
    print(f"""
    ╔════════════════════════════════════════╗
    ║  Slides desde texto                     ║
    ║  http://localhost:{port}                 ║
    ╚════════════════════════════════════════╝
    """)
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True, log_level="info")
