"""Prompts cortos = menos tokens de entrada y respuesta más rápida."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def interpret_default(instruction: str, text: str) -> str:
    return f"{instruction}\n\n{text}"


def structure_json(text: str) -> str:
    return (
        "JSON único: content_type (comparacion|descripcion|lista_items|portada|capitulo|otro), "
        "content_type_note, main_title, has_subtitles, subtitles:[{title,description}]. Texto:\n"
        + text
    )


def segment_json(text: str, context_summary: str) -> str:
    base = (
        "Dividí en slides: una parte por slide. Tipos: portada, capitulo, descripcion, "
        "comparacion (num_items:2), lista_items (num_items:3). "
        'JSON: {"parts":[{"part_index":0,"content_type":"...","text":"...","num_items":null}]}\n\n'
    )
    if context_summary:
        base += "Plantillas:\n" + context_summary[:3500].strip() + "\n\n"
    base += "Texto:\n" + text
    return base


def slide_placeholders(placeholders: List[str], context_template: Optional[Dict[str, Any]]) -> str:
    if context_template:
        lines = [
            "Solo JSON con claves: " + ", ".join(placeholders) + ".",
            (context_template.get("instrucciones") or "")[:600],
        ]
        mk = {k.lstrip("#").lower(): v for k, v in (context_template.get("marcadores") or {}).items()}
        for p in placeholders:
            lines.append(f"{p}: {mk.get(p, '')[:200]}")
        ex = context_template.get("few_shot_ejemplo")
        if ex:
            lines.append("Ej:" + str(ex)[:500])
        return "\n".join(lines)
    return f"Solo JSON, claves {', '.join(placeholders)}. Texto → valores por significado."


def batch_blocks(chunk: List[Dict[str, Any]]) -> str:
    parts = [
        'Un JSON: claves "0","1",… Cada valor = objeto con las claves de ESE bloque. No mezclar.\n'
    ]
    for i, job in enumerate(chunk):
        ph = [p.lstrip("#").lower() for p in (job.get("placeholders") or []) if (p or "").strip()]
        if not ph:
            parts.append(f'[{i}] {{}}')
            continue
        sub = slide_placeholders(ph, job.get("context_template"))
        txt = (job.get("text") or "")[:4800]
        parts.append(f"[{i}] {sub}\n---\n{txt}")
    return "\n\n".join(parts)
