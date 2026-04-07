from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from context_service import _marker_max_chars
from llm.client import generate
from llm.config import batch_chunk_size, max_output_tokens, model as resolve_model
from llm.json_utils import parse_object
from llm.prompts import batch_blocks, slide_placeholders

logger = logging.getLogger(__name__)

# Palabras que no deben quedar al final de un título truncado (artículos, preposiciones, conjunciones).
_STOP_WORDS = {
    "a", "al", "ante", "con", "de", "del", "desde", "el", "en", "entre",
    "es", "la", "las", "le", "les", "lo", "los", "para", "por", "que",
    "se", "si", "sin", "son", "su", "sus", "un", "una", "unos", "unas",
    "y", "o", "u", "e", "ni", "no", "the", "a", "an", "and", "or",
}


# Corta el texto en max_chars respetando palabras completas y evitando terminar en stop words.
def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_space = cut.rfind(" ")
    if last_space > max_chars // 2:
        cut = cut[:last_space]
    cut = cut.rstrip(" ,.;:—-")
    # Retroceder si el último token es una stop word
    words = cut.split()
    while words and words[-1].lower().strip(".,;:—-") in _STOP_WORDS:
        words.pop()
    return " ".join(words) if words else cut


# Extrae el mapa {placeholder_sin_hash: max_chars} desde el context_template de una slide.
def _build_max_chars_map(context_template: Optional[Dict[str, Any]]) -> Dict[str, int]:
    if not context_template:
        return {}
    result: Dict[str, int] = {}
    for key, val in (context_template.get("marcadores") or {}).items():
        mc = _marker_max_chars(val)
        if mc:
            result[key.lstrip("#").lower()] = mc
    return result


# Llama a Gemini en batch para reformular valores que superan el límite de caracteres.
# Devuelve el dict con los valores reformulados (usa _truncate como fallback si Gemini falla).
def _rephrase_overlong(
    values: Dict[str, str],
    max_chars_map: Dict[str, int],
    model: str,
) -> Dict[str, str]:
    overlong = {k: v for k, v in values.items() if k in max_chars_map and len(v) > max_chars_map[k]}
    if not overlong:
        return values

    lines = [
        "Reformulá cada campo para que entre en el límite de caracteres indicado.",
        "Respondé ÚNICAMENTE con un JSON válido. Claves = los mismos nombres de campo.",
        "Para títulos: usá palabras clave, sin artículos ni preposiciones al inicio.",
        "No uses puntos suspensivos. No cortés palabras a la mitad.",
        "",
    ]
    for k, v in overlong.items():
        mc = max_chars_map[k]
        lines.append(f'"{k}" (máx. {mc} chars, actual {len(v)} chars): {v[:600]}')

    prompt = "\n".join(lines)
    try:
        raw = generate(
            model,
            prompt,
            temperature=0.3,
            max_output_tokens=max_output_tokens("slide"),
            json_mode=False,
        )
        rephrased = parse_object(raw)
        if isinstance(rephrased, dict):
            out = dict(values)
            for k, v in rephrased.items():
                key = str(k).lower()
                if key in out and isinstance(v, str):
                    out[key] = v.strip()
            return out
    except Exception as exc:
        logger.warning("_rephrase_overlong falló (%s); usando _truncate como fallback.", exc)

    # Fallback: truncar manualmente
    out = dict(values)
    for k, mc in max_chars_map.items():
        if k in out and len(out[k]) > mc:
            out[k] = _truncate(out[k], mc)
    return out


# Aplica _truncate a todos los valores que superen su max_chars (última línea de defensa).
def _enforce_limits(values: Dict[str, str], max_chars_map: Dict[str, int]) -> Dict[str, str]:
    out = dict(values)
    for k, mc in max_chars_map.items():
        if k in out and isinstance(out[k], str) and len(out[k]) > mc:
            out[k] = _truncate(out[k], mc)
    return out


# Rellena los placeholders (#) de una sola slide enviando el texto a Gemini. Devuelve un dict {placeholder: valor}.
def ask_gemini_for_slide(
    text: str,
    placeholders: List[str],
    context_template: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
) -> Dict[str, str]:
    if not (text or "").strip():
        return {p.lstrip("#").lower(): "" for p in placeholders}
    ph = [p.lstrip("#").lower() for p in placeholders if (p or "").strip()]
    if not ph:
        return {}
    m = resolve_model(model)
    max_chars_map = _build_max_chars_map(context_template)
    instr = slide_placeholders(ph, context_template)
    prompt = f"{instr}\n\n{(text or '')[:5500]}"
    raw = generate(
        m,
        prompt,
        temperature=0.0,
        max_output_tokens=max_output_tokens("slide"),
        json_mode=False,
    )
    parsed = parse_object(raw)
    pl = {str(k).lower(): v for k, v in parsed.items()}
    out = {p: "" for p in ph}
    for k in out:
        if k in pl:
            v = pl[k]
            out[k] = v.strip() if isinstance(v, str) else str(v).strip()
    out = _rephrase_overlong(out, max_chars_map, m)
    out = _enforce_limits(out, max_chars_map)
    return out


# Rellena múltiples slides en batch (agrupa en chunks) para minimizar llamadas a Gemini.
def ask_gemini_batch_for_slides(
    slide_jobs: List[Dict[str, Any]],
    model: Optional[str] = None,
) -> List[Dict[str, str]]:
    if not slide_jobs:
        return []
    m = resolve_model(model)
    chunk_sz = batch_chunk_size()
    all_out: List[Dict[str, str]] = []
    for start in range(0, len(slide_jobs), chunk_sz):
        chunk = slide_jobs[start : start + chunk_sz]
        all_out.extend(_batch_chunk(chunk, m))
    return all_out


# Procesa un chunk de slides en una sola llamada a Gemini y mapea cada respuesta al job correspondiente.
def _batch_chunk(chunk: List[Dict[str, Any]], m: str) -> List[Dict[str, str]]:
    prompt = batch_blocks(chunk)
    raw = generate(
        m,
        prompt,
        temperature=0.0,
        max_output_tokens=max_output_tokens("batch"),
        json_mode=False,
    )
    parsed = parse_object(raw)
    results: List[Dict[str, str]] = []
    for i, job in enumerate(chunk):
        ph = [p.lstrip("#").lower() for p in (job.get("placeholders") or []) if (p or "").strip()]
        out = {p: "" for p in ph}
        if not ph:
            results.append(out)
            continue
        obj = parsed.get(str(i))
        if obj is None:
            obj = parsed.get(i)
        if obj is None:
            obj = parsed.get(f"[{i}]")
        if obj is None:
            obj = parsed.get(f"BLOQUE {i}")
        if not isinstance(obj, dict):
            obj = {}
        pl = {str(k).lower(): v for k, v in obj.items()}
        for k in out:
            if k in pl:
                v = pl[k]
                out[k] = v.strip() if isinstance(v, str) else str(v).strip()
        max_chars_map = _build_max_chars_map(job.get("context_template"))
        out = _rephrase_overlong(out, max_chars_map, m)
        out = _enforce_limits(out, max_chars_map)
        results.append(out)
    return results
