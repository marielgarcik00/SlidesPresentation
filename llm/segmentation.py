from __future__ import annotations

from typing import Any, Dict, List, Optional

from llm.client import generate
from llm.config import max_output_tokens, model as resolve_model, segment_text_char_limit
from llm.constants import CONTENT_TYPES
from llm.json_utils import parse_object
from llm.prompts import segment_json


# Divide el texto en segmentos (uno por slide) usando Gemini, asignando content_type y num_items a cada uno.
def segment_text_into_parts(
    text: str,
    model: Optional[str] = None,
    context_summary: str = "",
) -> List[Dict[str, Any]]:
    if not (text or "").strip():
        return []
    m = resolve_model(model)
    body = text.strip()[: segment_text_char_limit()]
    raw = generate(
        m,
        segment_json(body, context_summary or ""),
        temperature=0.0,
        max_output_tokens=max_output_tokens("segment"),
        json_mode=False,
    )
    d = parse_object(raw)
    arr = d.get("parts") if isinstance(d.get("parts"), list) else []
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(arr[:30]):
        if not isinstance(item, dict):
            continue
        ct_raw = item.get("content_type") or "descripcion"
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
                "part_index": item.get("part_index", i),
                "content_type": content_type,
                "text": part_text,
                "num_items": num_items,
            })
    return out
