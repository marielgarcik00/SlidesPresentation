"""
Cliente Gemini (google-genai) compartido: generación con reintentos y rate limit.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from google import genai
from google.genai import types

from llm.config import api_key
from llm.rate_limit import wait_for_slot

logger = logging.getLogger(__name__)

_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=api_key())
    return _client


def close_client() -> None:
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
        _client = None


def _is_quota(e: Exception) -> bool:
    s = (str(e) or "").lower()
    return "429" in str(e) or "resource exhausted" in s or "quota" in s


def generate(
    model: str,
    contents: str,
    *,
    temperature: float = 0.0,
    max_output_tokens: int = 8192,
    json_mode: bool = False,
    max_retries: int = 4,
) -> str:
    """
    Una llamada a generate_content con throttle y reintentos ante 429.
    """
    kw: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if json_mode:
        kw["response_mime_type"] = "application/json"
    cfg = types.GenerateContentConfig(**kw)
    client = get_client()
    last: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            wait_for_slot(model)
            r = client.models.generate_content(
                model=model,
                contents=contents[:200_000],
                config=cfg,
            )
            try:
                return (r.text or "").strip()
            except Exception:
                parts = getattr(getattr(getattr(r, "candidates", [None])[0], "content", None), "parts", None) or []
                return "".join(getattr(p, "text", "") or "" for p in parts).strip()
        except Exception as e:
            last = e
            if _is_quota(e) and attempt < max_retries - 1:
                delay = 65.0 if attempt == 0 else min(120.0, 20.0 * (attempt + 1))
                logger.warning("Gemini cuota/429: reintento en %.0fs (%s/%s)", delay, attempt + 2, max_retries)
                time.sleep(delay)
                continue
            raise
    raise last or RuntimeError("Gemini")
