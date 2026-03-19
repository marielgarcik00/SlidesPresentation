from __future__ import annotations

from typing import Any, Dict, List, Optional

from llm.client import generate
from llm.config import batch_chunk_size, max_output_tokens, model as resolve_model
from llm.json_utils import parse_object
from llm.prompts import batch_blocks, slide_placeholders


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
    instr = slide_placeholders(ph, context_template)
    prompt = f"{instr}\n\n{(text or '')[:5500]}"
    raw = generate(
        m,
        prompt,
        temperature=0.0,
        max_output_tokens=max_output_tokens("slide"),
        json_mode=True,
    )
    parsed = parse_object(raw)
    pl = {str(k).lower(): v for k, v in parsed.items()}
    out = {p: "" for p in ph}
    for k in out:
        if k in pl:
            v = pl[k]
            out[k] = (v.strip()[:1500] if isinstance(v, str) else str(v).strip()[:1500])
    return out


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


def _batch_chunk(chunk: List[Dict[str, Any]], m: str) -> List[Dict[str, str]]:
    prompt = batch_blocks(chunk)
    raw = generate(
        m,
        prompt,
        temperature=0.0,
        max_output_tokens=max_output_tokens("batch"),
        json_mode=True,
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
        if not isinstance(obj, dict):
            obj = {}
        pl = {str(k).lower(): v for k, v in obj.items()}
        for k in out:
            if k in pl:
                v = pl[k]
                out[k] = v.strip()[:1500] if isinstance(v, str) else str(v).strip()[:1500]
        results.append(out)
    return results
