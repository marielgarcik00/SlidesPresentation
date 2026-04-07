"""Prompts cortos = menos tokens de entrada y respuesta más rápida."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from context_service import _marker_desc, _marker_max_chars


def interpret_default(instruction: str, text: str) -> str:
    return f"{instruction}\n\n{text}"


def segment_json(text: str, context_summary: str) -> str:
    base = (
        "Respondé ÚNICAMENTE con un JSON válido, sin texto extra.\n"
        'Formato: {"parts":[{"part_index":0,"content_type":"...","text":"...","num_items":null}]}\n\n'
        "Dividí el texto en partes: una parte por slide. content_type debe ser uno de:\n"
        "  portada → slide de apertura o presentación general (título + empresa/contexto)\n"
        "  capitulo → abre una sección numerada (ej: '01 Introducción', '02 Resultados')\n"
        "  capitulo_sin_numero → SOLO un título de sección sin número y SIN cuerpo de texto (ej: 'Análisis de Resultados', 'Conclusiones')\n"
        "  descripcion → un concepto con título Y cuerpo de texto explicativo (párrafos, detalles)\n"
        "  descripcion_sin_titulo → continuación de un concepto ya titulado (solo cuerpo, sin título nuevo)\n"
        "  comparacion → contrasta exactamente 2 elementos (num_items:2)\n"
        "  lista_items → enumera exactamente 3 puntos clave (num_items:3)\n\n"
        "IMPORTANTE: usá capitulo_sin_numero cuando el fragmento es SOLO un título de sección (sin desarrollo). "
        "Usá descripcion cuando hay título + texto explicativo.\n\n"
        "ESTRUCTURA OBLIGATORIA (siempre, en este orden):\n"
        "  Parte 0: content_type='portada' — título principal del texto (nombre del tema, empresa o evento).\n"
        "  Parte 1: content_type='descripcion' — resumen general de todo lo que se va a exponer (mini-índice con los ejes clave).\n"
        "  Partes intermedias: el resto del contenido, reordenado por vos para que la presentación sea coherente y atractiva "
        "(no tenés que seguir el orden original del texto; priorizá el flujo narrativo).\n\n"
        "REGLA: No omitás ningún párrafo ni dato del texto. Podés reformular, pero no eliminar información.\n\n"
    )
    if context_summary:
        base += "Plantillas disponibles:\n" + context_summary[:3500].strip() + "\n\n"
    base += "Texto:\n" + text
    return base


def slide_placeholders(placeholders: List[str], context_template: Optional[Dict[str, Any]]) -> str:
    base_rule = (
        "REGLA FUNDAMENTAL: incluí TODA la información del texto. "
        "No omitás párrafos, datos ni ideas. Podés reformular pero NO eliminar contenido. "
        "Si hay límite de caracteres, condensá sin perder información."
    )
    if context_template:
        lines = [
            "Respondé ÚNICAMENTE con un JSON válido, sin texto extra. Claves: " + ", ".join(placeholders) + ".",
            base_rule,
            (context_template.get("instrucciones") or "")[:600],
        ]
        mk = {k.lstrip("#").lower(): v for k, v in (context_template.get("marcadores") or {}).items()}
        for p in placeholders:
            v = mk.get(p)
            desc = _marker_desc(v) if v is not None else ""
            mc = _marker_max_chars(v)
            limit = f" (máx. {mc} chars — condensá para que entre, sin perder información clave)" if mc else ""
            lines.append(f"{p}: {desc}{limit}")
        ex = context_template.get("few_shot_ejemplo")
        if ex:
            lines.append("Ej:" + str(ex)[:500])
        return "\n".join(lines)
    return (
        f"Respondé ÚNICAMENTE con un JSON válido, sin texto extra. Claves: {', '.join(placeholders)}. "
        f"{base_rule}"
    )


def batch_blocks(chunk: List[Dict[str, Any]]) -> str:
    parts = [
        'Respondé ÚNICAMENTE con un JSON válido. Claves numéricas "0","1","2"… '
        'Cada valor = objeto con las claves de ESE bloque. No mezcles bloques.\n'
        'REGLA FUNDAMENTAL: incluí TODA la información de cada bloque. '
        'No omitás párrafos ni datos. Podés reformular pero NO eliminar contenido.\n'
    ]
    for i, job in enumerate(chunk):
        ph = [p.lstrip("#").lower() for p in (job.get("placeholders") or []) if (p or "").strip()]
        if not ph:
            parts.append(f'BLOQUE {i} (clave "{i}" en el JSON): {{}}')
            continue
        sub = slide_placeholders(ph, job.get("context_template"))
        txt = (job.get("text") or "")[:4800]
        parts.append(f'BLOQUE {i} (clave "{i}" en el JSON):\n{sub}\n---\n{txt}')
    return "\n\n".join(parts)
