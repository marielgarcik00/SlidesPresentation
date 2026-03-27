from __future__ import annotations

from typing import Any, Dict, Optional

from llm.client import generate
from llm.config import interpret_char_limit, max_output_tokens, model as resolve_model, structure_char_limit
from llm.constants import CONTENT_TYPES
from llm.json_utils import parse_object
from llm.prompts import interpret_default, structure_json


# Normaliza la respuesta JSON de Gemini al formato estándar de estructura (content_type, main_title, subtitles).
def _normalize_structure(parsed: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "content_type": "",
        "content_type_note": "",
        "main_title": "",
        "has_subtitles": False,
        "subtitles": [],
    }
    if not parsed:
        return out
    ct = (parsed.get("content_type") or parsed.get("tipo") or "").strip().lower()
    if ct in CONTENT_TYPES:
        out["content_type"] = ct
    elif ct:
        out["content_type"] = "otro"
        out["content_type_note"] = ct[:300]
    out["content_type_note"] = (parsed.get("content_type_note") or out["content_type_note"] or "")[:300]
    main = parsed.get("main_title") or parsed.get("titulo") or ""
    out["main_title"] = (main if isinstance(main, str) else str(main)).strip()[:500]
    sub = parsed.get("subtitles") or parsed.get("items") or []
    if isinstance(sub, list):
        out["has_subtitles"] = len(sub) > 0
        for item in sub[:20]:
            if isinstance(item, dict):
                out["subtitles"].append({
                    "title": (item.get("title") or item.get("titulo") or "")[:500],
                    "description": (item.get("description") or item.get("descripcion") or "")[:1500],
                })
            elif isinstance(item, str):
                out["subtitles"].append({"title": item[:500], "description": ""})
    return out


# Envía el texto a Gemini con una instrucción libre y devuelve la interpretación en texto plano.
def ask_gemini(
    text: str,
    instruction: str = "Qué entiendes de este texto?",
    model: Optional[str] = None,
) -> str:
    if not (text or "").strip():
        return ""
    m = resolve_model(model)
    body = text.strip()[: interpret_char_limit()]
    return generate(
        m,
        interpret_default(instruction, body),
        temperature=0.25,
        max_output_tokens=max_output_tokens("interpret"),
        json_mode=False,
    )


# Pide a Gemini que clasifique el texto: content_type, título principal y lista de subtítulos.
def ask_gemini_title_and_subtitles(text: str, model: Optional[str] = None) -> Dict[str, Any]:
    if not (text or "").strip():
        return {"content_type": "", "content_type_note": "", "main_title": "", "has_subtitles": False, "subtitles": []}
    m = resolve_model(model)
    body = text.strip()[: structure_char_limit()]
    raw = generate(
        m,
        structure_json(body),
        temperature=0.0,
        max_output_tokens=max_output_tokens("structure"),
        json_mode=False,
    )
    return _normalize_structure(parse_object(raw))
