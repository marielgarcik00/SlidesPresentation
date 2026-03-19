"""Parseo tolerante de JSON en respuestas del modelo."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List


def parse_object(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
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


def parse_array(raw: str) -> List[Dict[str, Any]]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
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
