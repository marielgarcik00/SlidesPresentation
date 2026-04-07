"""Parseo tolerante de JSON en respuestas del modelo."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List


# Elimina bloques de código markdown (```json ... ```) si los hay, para dejar JSON limpio.
def _strip_markdown(raw: str) -> str:
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    return raw


# Parsea la respuesta de Gemini como un objeto JSON ({...}). Tolerante a texto extra y backticks.
def parse_object(raw: str) -> Dict[str, Any]:
    raw = _strip_markdown((raw or "").strip())
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        a, b = raw.find("{"), raw.rfind("}") + 1
        if a >= 0 and b > a:
            try:
                out = json.loads(raw[a:b])
                return out if isinstance(out, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


# Parsea la respuesta de Gemini como un array JSON ([...]). También acepta {parts: [...]} como wrapper.
def parse_array(raw: str) -> List[Dict[str, Any]]:
    raw = _strip_markdown((raw or "").strip())
    try:
        out = json.loads(raw)
        if isinstance(out, list):
            return [x for x in out if isinstance(x, dict)]
        if isinstance(out, dict) and "parts" in out:
            p = out["parts"]
            return [x for x in p if isinstance(x, dict)] if isinstance(p, list) else []
    except json.JSONDecodeError:
        pass
    start, end = raw.find("["), raw.rfind("]") + 1
    if start >= 0 and end > start:
        try:
            out = json.loads(raw[start:end])
            if isinstance(out, list):
                return [x for x in out if isinstance(x, dict)]
        except json.JSONDecodeError:
            pass
    return []
