"""
1. Carga del contexto — load_context() Lee context.json una sola vez (cacheado con lru_cache). Todo el resto del archivo parte de acá.
2. Resolución de marcadores — resolve_markers_for_tags() / resolve_context_template() Dado un conjunto de $tags de una slide, determina dinámicamente cuáles son sus #placeholders activos:
El tag primary aporta los marcadores base (introduces)
Los tags modifier eliminan marcadores (removes)
resolve_context_template además incluye instrucciones y few_shot_ejemplo para pasarle a Gemini
3. Selección de template por contenido — get_preferred_templates_for_content() Recibe el segmento estructurado (content_type, subtítulos, texto) y devuelve una lista ordenada de templates preferidos. Incluye una heurística: si el texto es muy corto y no tiene subtítulos, lo trata como título de sección.
4. Matching de slides — find_best_slide_index() Dado el ranking de templates preferidos, busca en la presentación real cuál slide tiene los tags que coinciden. Devuelve el índice de la mejor slide.
5. Lookup por template ID — get_template_and_placeholders_by_identifier() Dado un ID como $chapter_cover, devuelve el dict completo del template + la lista de placeholders. Lo usa app.py para saber qué claves pedirle a Gemini.
6. Resumen para el segmentador — get_context_summary_for_segmenter() Construye un texto descriptivo de todos los templates disponibles. Se le pasa a Gemini en el prompt de segmentación para que sepa qué tipos de slide existen.
Helpers compartidos — _marker_desc() / _marker_max_chars() Utilidades para extraer descripción y límite de caracteres de un marcador. También los importa llm/prompts.py.
"""

import functools
import json
import os
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONTEXT_PATH = os.path.join(PROJECT_ROOT, "context.json")


@functools.lru_cache(maxsize=1)
def load_context() -> dict:
    if not os.path.exists(CONTEXT_PATH):
        return {}
    try:
        with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Resolución dinámica de marcadores
# ---------------------------------------------------------------------------

# Dado un conjunto de tags de una slide, devuelve los marcadores (#) resueltos.
def resolve_markers_for_tags(tags: List[str]) -> Dict[str, Dict]:
    tag_defs = load_context().get("tag_definitions") or {}
    markers: Dict[str, Dict] = {}
    removes: List[str] = []

    primary_used = False
    for tag in tags:
        td = tag_defs.get(tag) or {}
        tag_type = td.get("type", "descriptor")

        if tag_type == "primary" and not primary_used and td.get("introduces"):
            markers = dict(td["introduces"])
            primary_used = True
        elif tag_type == "modifier":
            removes.extend(td.get("removes") or [])

    for r in removes:
        markers.pop(r, None)

    return markers

# Recibe una lista de $tags y devuelve un dict para Gemini con los #marcadores e instrucciones del tag (context.json
def resolve_context_template(tags: List[str]) -> Dict:
    tag_defs = load_context().get("tag_definitions") or {}
    markers: Dict[str, Dict] = {}
    removes: List[str] = []
    instrucciones = ""
    few_shot = ""
    primary_used = False

    for tag in tags:
        td = tag_defs.get(tag) or {}
        tag_type = td.get("type", "descriptor")
        if tag_type == "primary" and not primary_used:
            if td.get("introduces"):
                markers = dict(td["introduces"])
            instrucciones = td.get("instrucciones") or ""
            few_shot = td.get("few_shot_ejemplo") or ""
            primary_used = True
        elif tag_type == "modifier":
            removes.extend(td.get("removes") or [])

    for r in removes:
        markers.pop(r, None)

    result: Dict = {"marcadores": markers}
    if instrucciones:
        result["instrucciones"] = instrucciones
    if few_shot:
        result["few_shot_ejemplo"] = few_shot
    return result


# ---------------------------------------------------------------------------
# Preferencia de templates por tipo de contenido
# ---------------------------------------------------------------------------

# Lee content_type_routing de context.json y devuelve los template IDs preferidos en orden de prioridad.
def get_preferred_templates_for_content(structured: Dict) -> List[str]:
    content_type = (structured.get("content_type") or "").strip().lower()
    subtitles = structured.get("subtitles") or []
    num = len(subtitles) if isinstance(subtitles, list) else 0
    text_len = len((structured.get("text") or "").strip())
    is_title_only = text_len < 120 and num == 0

    routing = load_context().get("content_type_routing") or {}
    entry = routing.get(content_type) or routing.get("__fallback__") or {}

    if num == 3 and "exact_items_3" in entry:
        return entry["exact_items_3"]
    if num == 2 and "exact_items_2" in entry:
        return entry["exact_items_2"]
    if num >= 3 and "min_items_3" in entry:
        return entry["min_items_3"]
    if is_title_only and "is_title_only" in entry:
        return entry["is_title_only"]
    return entry.get("default") or routing.get("__fallback__", {}).get("default", ["$descriptive_presentation"])


# ---------------------------------------------------------------------------
# Matching de slides
# ---------------------------------------------------------------------------

def find_best_slide_index(
    slides: List[Dict],
    preferred_templates: List[str],
    exclude_indices: Optional[set] = None,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Dado el ranking de templates preferidos, encuentra el primer slide que
    contenga TODOS los tags requeridos por el template.
    Los templates más específicos (más tags) se evalúan primero según el orden
    de preferred_templates.
    """
    exclude_indices = exclude_indices or set()
    slides_context = load_context().get("slides_context") or {}

    for template_id in preferred_templates:
        key = template_id if template_id.startswith("$") else f"${template_id}"
        template_def = slides_context.get(key) or {}
        required_tags = template_def.get("tags")

        for slide in slides:
            idx = slide.get("index", 0)
            if idx in exclude_indices:
                continue
            ids = slide.get("identifiers") or []
            slide_tag_set = {(i if isinstance(i, str) else "").lower() for i in ids}

            if required_tags:
                required = {t.lower() for t in required_tags}
                if required.issubset(slide_tag_set):
                    return idx, template_id
            else:
                # Fallback legacy: match por nombre compuesto
                template_key = key.lstrip("$").lower()
                slide_set_no_dollar = {t.lstrip("$") for t in slide_tag_set}
                if template_key in slide_set_no_dollar:
                    return idx, template_id

    return None, None


# ---------------------------------------------------------------------------
# Lookup por template ID
# ---------------------------------------------------------------------------

def get_template_and_placeholders_by_identifier(
    template_id: str,
) -> Tuple[Optional[Dict], List[str]]:
    """
    Dado un template_id (ej. $chapter_cover), resuelve sus marcadores dinámicamente
    desde tag_definitions y devuelve (context_template_dict, placeholders_list).
    """
    data = load_context()
    slides_context = data.get("slides_context") or {}
    key = template_id if template_id.startswith("$") else f"${template_id}"

    if key not in slides_context:
        key_norm = key.lstrip("$").lower()
        for k in slides_context:
            if k.lstrip("$").lower() == key_norm:
                key = k
                break
        else:
            return None, []

    tags = slides_context[key].get("tags") or []
    context_template = resolve_context_template(tags)
    marcadores = context_template.get("marcadores") or {}
    placeholders = [m.lstrip("#").lower() for m in marcadores]
    return context_template, placeholders


# ---------------------------------------------------------------------------
# Resumen para el segmentador
# ---------------------------------------------------------------------------

def get_context_summary_for_segmenter() -> str:
    """
    Devuelve un resumen de los templates disponibles construido desde tag_definitions.
    Se pasa a la IA para que sepa qué tipos de slide existen.
    """
    data = load_context()
    slides_context = data.get("slides_context") or {}
    tag_defs = data.get("tag_definitions") or {}
    lines = []
    for key, t in slides_context.items():
        tags = t.get("tags") or []
        descs = [tag_defs.get(tag, {}).get("description") or tag for tag in tags]
        description = " + ".join(descs)
        lines.append(f"- {key} [tags: {' '.join(tags)}]: {description}")
    return "\n".join(lines) if lines else ""


# ---------------------------------------------------------------------------
# Helpers de marcadores (usados también por llm/prompts.py)
# ---------------------------------------------------------------------------

def _marker_desc(marker_value) -> str:
    """Extrae la descripción de un marcador (string o dict), truncada a 200 chars."""
    if isinstance(marker_value, dict):
        return (marker_value.get("description") or "")[:200]
    return str(marker_value or "")[:200]


def _marker_max_chars(marker_value) -> Optional[int]:
    if isinstance(marker_value, dict):
        v = marker_value.get("max_chars")
        return int(v) if v is not None else None
    return None
