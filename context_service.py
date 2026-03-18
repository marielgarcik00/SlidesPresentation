# context_service.py — Carga context.json y resuelve plantillas ($) y placeholders (#) para asignar contenido a slides.

import json
import os
from typing import Dict, List, Optional, Tuple

# Ruta del archivo de contexto (plantillas y marcadores)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONTEXT_PATH = os.path.join(PROJECT_ROOT, "context.json")

# Carga el archivo context.json completo.
# Devuelve {} si no existe o hay error de lectura.
def load_context() -> dict:
    if not os.path.exists(CONTEXT_PATH):
        return {}
    try:
        with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

# Dada la lista de placeholders de una slide (ej. main_title, description),
# devuelve el template de context.json cuyos marcadores coinciden exactamente.
# placeholders puede venir con o sin # (se normaliza a sin # y lower).
def get_slide_context_by_placeholders(placeholders: List[str]) -> Optional[Dict]:
    data = load_context()
    slides_context = data.get("slides_context") or {}
    want = {p.lstrip("#").lower() for p in placeholders if (p or "").strip()}
    if not want:
        return None
    for _key, template in slides_context.items():
        marcadores = template.get("marcadores") or {}
        have = {k.lstrip("#").lower() for k in marcadores}
        if have == want:
            return template
    return None

# A partir de la interpretación estructurada (content_type, subtitles),
# devuelve la lista de identificadores de plantilla ($...) preferidos en orden.
# Se usa para elegir la mejor slide de la presentación según el tipo de contenido.
def get_preferred_templates_for_content(structured: Dict) -> List[str]:
    content_type = (structured.get("content_type") or "").strip().lower()
    subtitles = structured.get("subtitles") or []
    num = len(subtitles) if isinstance(subtitles, list) else 0

    if content_type == "comparacion":
        if num == 2:
            return ["$comparative_two_differences"]
        if num >= 3:
            return ["$three_items_list", "$comparative_two_differences"]
        return ["$descriptive_presentation", "$comparative_two_differences"]
    if content_type == "lista_items":
        if num == 3:
            return ["$three_items_list"]
        if num == 2:
            return ["$comparative_two_differences", "$three_items_list"]
        return ["$descriptive_presentation", "$three_items_list"]
    if content_type == "descripcion":
        return ["$descriptive_presentation"]
    if content_type == "portada":
        return ["$cover_presentation"]
    if content_type == "capitulo":
        return ["$chapter_cover"]
    if num == 2:
        return ["$comparative_two_differences", "$descriptive_presentation"]
    if num >= 3:
        return ["$three_items_list", "$comparative_two_differences"]
    return ["$descriptive_presentation", "$cover_presentation"]

# Dada la lista de slides (cada una con 'index' e 'identifiers') y los templates
# preferidos en orden, devuelve (slide_index, matched_identifier) del primer
# slide que coincida. exclude_indices: set de índices ya usados (opcional).
def find_best_slide_index(
    slides: List[Dict],
    preferred_templates: List[str],
    exclude_indices: Optional[set] = None,
) -> Tuple[Optional[int], Optional[str]]:
    exclude_indices = exclude_indices or set()
    preferred_norm = {t.lstrip("$").lower(): t for t in preferred_templates}
    for template_key, template_id in preferred_norm.items():
        for slide in slides:
            idx = slide.get("index", 0)
            if idx in exclude_indices:
                continue
            ids = slide.get("identifiers") or []
            slide_set = {(i if isinstance(i, str) else "").lstrip("$").lower() for i in ids}
            if template_key in slide_set:
                return idx, template_id
            parts = template_key.split("_")
            if parts and all(p in slide_set for p in parts):
                return idx, template_id
    return None, None

# Devuelve un resumen de las plantillas de context.json para que la IA sepa qué tipos de slide existen al segmentar el texto.
def get_context_summary_for_segmenter() -> str:
    data = load_context()
    slides_context = data.get("slides_context") or {}
    lines = []
    for key, t in slides_context.items():
        finalidad = (t.get("finalidad") or "").strip()
        name = key.replace("$", "").replace("_", " ")
        lines.append(f"- {key}: {finalidad or name}")
    return "\n".join(lines) if lines else ""

# Dado un identificador de plantilla (ej. $comparative_two_differences), devuelve (template_dict, placeholders) desde context.json.
# placeholders son las claves sin # y en minúsculas.
def get_template_and_placeholders_by_identifier(
    template_id: str,
) -> Tuple[Optional[Dict], List[str]]:
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
    template = slides_context[key]
    marcadores = template.get("marcadores") or {}
    placeholders = [m.lstrip("#").lower() for m in marcadores]
    return template, placeholders
