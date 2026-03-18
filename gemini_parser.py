# gemini_parser.py — Envía texto a Gemini y obtiene interpretación, estructura o JSON por tipo de slide.

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# True si el error es por límite de cuota / rate limit de la API
def _is_quota_error(e: Exception) -> bool:
    s = (str(e) or "").upper()
    return "429" in s or "RESOURCE_EXHAUSTED" in s or "QUOTA" in s or "RATE" in s


# Índice de slide por defecto cuando no se especifica
DEFAULT_SLIDE_INDEX = 0

# Extrae un objeto JSON de la respuesta en texto (tolera markdown con ```json).
# Si hay texto alrededor, busca el primer { y el último } y parsea ese tramo.
def _parse_json_from_response(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    return {}

# Extrae un array JSON de la respuesta (para segmentación).
# Tolera texto alrededor; busca el primer [ y el último ].
def _parse_json_array_from_response(raw: str) -> List[Dict[str, Any]]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        out = json.loads(raw)
        return out if isinstance(out, list) else []
    except json.JSONDecodeError:
        pass
    start = raw.find("[")
    end = raw.rfind("]") + 1
    if start >= 0 and end > start:
        try:
            out = json.loads(raw[start:end])
            return out if isinstance(out, list) else []
        except json.JSONDecodeError:
            pass
    return []


# Tipos de contenido que la IA puede detectar (alineados con context.json)
CONTENT_TYPES = (
    "comparacion",
    "descripcion",
    "lista_items",
    "portada",
    "capitulo",
    "otro",
)

# Normaliza el JSON que devuelve Gemini a la estructura esperada: content_type, content_type_note, main_title, has_subtitles, subtitles.
def _normalize_interpretation(parsed: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "content_type": "",
        "content_type_note": "",
        "main_title": "",
        "has_subtitles": False,
        "subtitles": [],
    }
    if not parsed:
        return out
    ct = (parsed.get("content_type") or parsed.get("tipo_contenido") or parsed.get("tipo") or "").strip().lower()
    if ct in CONTENT_TYPES:
        out["content_type"] = ct
    elif ct:
        out["content_type"] = "otro"
        out["content_type_note"] = ct[:300]
    note = (parsed.get("content_type_note") or parsed.get("nota_tipo") or "").strip()[:300]
    if note:
        out["content_type_note"] = note
    main = parsed.get("main_title") or parsed.get("titulo") or ""
    out["main_title"] = (main if isinstance(main, str) else str(main)).strip()[:500]
    sub = parsed.get("subtitles") or parsed.get("subtitulos") or parsed.get("items") or []
    if isinstance(sub, list):
        out["has_subtitles"] = len(sub) > 0
        for item in sub[:20]:
            if isinstance(item, dict):
                title = (item.get("title") or item.get("titulo") or item.get("name") or "").strip()[:500]
                desc = (item.get("description") or item.get("descripcion") or "").strip()[:1500]
                out["subtitles"].append({"title": title, "description": desc})
            elif isinstance(item, str):
                out["subtitles"].append({"title": item[:500], "description": ""})
    return out

# Envía texto a Gemini con una instrucción y devuelve la respuesta en texto plano.
# Un solo modelo (GEMINI_MODEL o gemini-2.5-flash). Si falla por cuota (429), espera y reintenta.
def ask_gemini(
    text: str,
    instruction: str = "Qué entiendes de este texto?",
    model: Optional[str] = None,
) -> str:
    if not (text and text.strip()):
        return ""
    model = model or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    from google import genai
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    prompt = f"{instruction}\n\nTexto:\n{text.strip()}"
    wait_seconds = 90
    max_attempts = 3

    for attempt in range(max_attempts):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"temperature": 0.0},
            )
            return (response.text or "").strip()
        except Exception as e:
            if _is_quota_error(e) and attempt < max_attempts - 1:
                logger.warning(
                    "Cuota Gemini. Esperando %s s antes de reintentar (%s/%s)...",
                    wait_seconds, attempt + 2, max_attempts,
                )
                time.sleep(wait_seconds)
                continue
            raise
    return ""

# Pide a Gemini que interprete el texto y clasifique: tipo de contenido (comparación, descripción, lista de ítems, portada, capítulo), título principal,
# si hay subtítulos y lista de objetos {title, description}.
# Devuelve la estructura normalizada.
def ask_gemini_title_and_subtitles(text: str, model: Optional[str] = None) -> Dict[str, Any]:
    if not (text and text.strip()):
        return {"content_type": "", "content_type_note": "", "main_title": "", "has_subtitles": False, "subtitles": []}
    instruction = (
        "Interpretá el siguiente texto y clasificá su contenido. Devolvé ÚNICAMENTE un JSON (en español) con:\n"
        "- content_type: uno de: comparacion, descripcion, lista_items, portada, capitulo, otro.\n"
        "- content_type_note: (opcional) aclaración si es 'otro'.\n"
        "- main_title: título o tema general si hay; si no, string vacío.\n"
        "- has_subtitles: true si hay subtemas o ítems; false si es un solo bloque.\n"
        "- subtitles: lista de objetos con 'title' y 'description'. No inventes contenido.\n"
    )
    raw = ask_gemini(text, instruction=instruction, model=model)
    parsed = _parse_json_from_response(raw)
    return _normalize_interpretation(parsed)

# Construye la instrucción para Gemini según los placeholders y el template de context.json (instrucciones, marcadores, ejemplos).
def _build_instruction_for_slide(
    placeholders: List[str],
    context_template: Optional[Dict[str, Any]],
) -> str:
    if context_template:
        parts = [
            "Interpretá el siguiente texto y devolvé ÚNICAMENTE un JSON con estas claves (en español).",
            context_template.get("instrucciones", ""),
        ]
        marcadores = context_template.get("marcadores") or {}
        marcadores_norm = {k.lstrip("#").lower(): v for k, v in marcadores.items()}
        for p in placeholders:
            desc = marcadores_norm.get(p) or marcadores.get(f"#{p}") or marcadores.get(p) or "valor que corresponda"
            parts.append(f"- {p}: {desc}")
        if context_template.get("few_shot_ejemplo"):
            parts.append("\nEjemplo: " + context_template["few_shot_ejemplo"])
        parts.append("\nNo agregues texto fuera del JSON. Cada clave = un fragmento distinto.")
        return "\n".join(parts)
    keys_str = ", ".join(placeholders)
    return (
        f"Interpretá el texto y devolvé ÚNICAMENTE un JSON con exactamente estas claves: {keys_str}. "
        "Asigná a cada clave el fragmento que corresponda por significado. Respuesta = solo el objeto JSON."
    )

# Dado un texto, la lista de placeholders de una slide y opcionalmente el template de context.json, pide a Gemini que devuelva un JSON con esas claves rellenadas según el significado de cada una (no por orden de aparición).
def ask_gemini_for_slide(
    text: str,
    placeholders: List[str],
    context_template: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
) -> Dict[str, str]:
    if not (text and text.strip()):
        return {p: "" for p in placeholders}
    placeholders = [p.lstrip("#").lower() for p in placeholders if (p or "").strip()]
    if not placeholders:
        return {}
    instruction = _build_instruction_for_slide(placeholders, context_template)
    raw = ask_gemini(text, instruction=instruction, model=model)
    out = {p: "" for p in placeholders}
    parsed = _parse_json_from_response(raw)
    parsed_lower = {k.lower(): v for k, v in parsed.items() if isinstance(k, str)}
    for key in out:
        if key in parsed_lower and isinstance(parsed_lower[key], str):
            out[key] = parsed_lower[key].strip()[:1500]
        elif key in parsed_lower:
            out[key] = str(parsed_lower[key]).strip()[:1500]
    return out


# Máximo de slides por una sola llamada a Gemini (evita prompts enormes).
_BATCH_SLIDES_CHUNK = 6


def ask_gemini_batch_for_slides(
    slide_jobs: List[Dict[str, Any]],
    model: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Rellena varias slides en pocas llamadas a la API (una por cada chunk de hasta
    _BATCH_SLIDES_CHUNK slides). Cada job: text, placeholders, context_template.
    Devuelve una lista de dicts (misma longitud que slide_jobs).
    """
    if not slide_jobs:
        return []
    all_out: List[Dict[str, str]] = []
    for start in range(0, len(slide_jobs), _BATCH_SLIDES_CHUNK):
        chunk = slide_jobs[start : start + _BATCH_SLIDES_CHUNK]
        all_out.extend(_ask_gemini_batch_for_slides_chunk(chunk, model))
    return all_out


def _ask_gemini_batch_for_slides_chunk(
    chunk: List[Dict[str, Any]],
    model: Optional[str] = None,
) -> List[Dict[str, str]]:
    blocks = []
    for i, job in enumerate(chunk):
        placeholders = [p.lstrip("#").lower() for p in (job.get("placeholders") or []) if (p or "").strip()]
        if not placeholders:
            blocks.append(f'=== BLOQUE ÍNDICE "{i}" ===\nSin campos. Devolvé para esta clave un objeto vacío {{}}.\n')
            continue
        sub = _build_instruction_for_slide(placeholders, job.get("context_template"))
        txt = (job.get("text") or "")[:5500]
        blocks.append(f'=== BLOQUE ÍNDICE "{i}" (solo usar el TEXTO de este bloque) ===\n{sub}\n\nTEXTO:\n{txt}')
    if not chunk:
        return []

    instruction = (
        "Tenés varios bloques numerados 0, 1, 2, ... Cada bloque tiene su propio TEXTO y sus claves JSON.\n"
        "Devolvé UN SOLO objeto JSON cuyas claves sean los strings \"0\", \"1\", \"2\", ... "
        "(una por bloque, en orden). Cada valor debe ser el objeto JSON con las claves de ESE bloque "
        "rellenadas solo con el TEXTO de ese bloque. No mezclar textos entre bloques.\n\n"
        + "\n\n".join(blocks)
    )
    raw = ask_gemini("batch", instruction=instruction, model=model)
    parsed = _parse_json_from_response(raw)
    if not isinstance(parsed, dict):
        parsed = {}

    results: List[Dict[str, str]] = []
    for i, job in enumerate(chunk):
        placeholders = [p.lstrip("#").lower() for p in (job.get("placeholders") or []) if (p or "").strip()]
        out = {p: "" for p in placeholders}
        if not placeholders:
            results.append(out)
            continue
        obj = parsed.get(str(i))
        if obj is None and i in parsed:
            obj = parsed.get(i)
        if not isinstance(obj, dict):
            obj = {}
        parsed_lower = {str(k).lower(): v for k, v in obj.items()}
        for key in out:
            if key in parsed_lower:
                v = parsed_lower[key]
                out[key] = (v.strip()[:1500] if isinstance(v, str) else str(v).strip()[:1500])
        results.append(out)
    return results


# La IA interpreta el texto, lo divide en secciones (por tema y por largo) y clasifica cada parte para asignarla a una plantilla de slide según context.json. 
# Devuelve lista de dicts con part_index, content_type, text, num_items.
def segment_text_into_parts(
    text: str,
    model: Optional[str] = None,
    context_summary: str = "",
) -> List[Dict[str, Any]]:
    if not (text and text.strip()):
        return []
    instruction = (
        "Tu tarea: 1) INTERPRETAR el texto. 2) DIVIDIRLO en secciones: "
        "cada sección es un bloque que irá en una slide. Dividí POR TEMA y POR LARGO. "
        "3) Para cada parte, indicá qué tipo de slide necesita:\n\n"
        "- portada: título principal y/o pie.\n"
        "- capitulo: número de capítulo + título de sección.\n"
        "- descripcion: un concepto desarrollado (título + cuerpo).\n"
        "- comparacion: dos temas comparados → num_items: 2.\n"
        "- lista_items: tres ítems → num_items: 3.\n\n"
    )
    if context_summary:
        instruction += "Contexto de plantillas disponibles:\n" + context_summary.strip() + "\n\n"
    instruction += (
        "Devolvé ÚNICAMENTE un JSON array. Cada elemento: part_index, content_type, text, num_items (2 o 3 si aplica). "
        "No inventes contenido. Respuesta = solo el array JSON, sin markdown."
    )
    raw = ask_gemini(text, instruction=instruction, model=model)
    arr = _parse_json_array_from_response(raw)
    out = []
    for i, item in enumerate(arr[:30]):
        if not isinstance(item, dict):
            continue
        part_index = item.get("part_index", i)
        ct_raw = item.get("content_type") or item.get("tipo") or "descripcion"
        content_type = (ct_raw if isinstance(ct_raw, str) else str(ct_raw)).strip().lower()
        if content_type not in CONTENT_TYPES:
            content_type = "descripcion"
        text_raw = item.get("text") or item.get("texto") or ""
        part_text = (text_raw if isinstance(text_raw, str) else str(text_raw)).strip()[:8000]
        num_items = item.get("num_items")
        if num_items is not None and not isinstance(num_items, int):
            try:
                num_items = int(num_items)
            except (TypeError, ValueError):
                num_items = None
        if part_text:
            out.append({
                "part_index": part_index,
                "content_type": content_type,
                "text": part_text,
                "num_items": num_items,
            })
    return out
