"""
Cliente Gemini (google-genai) compartido: generación con reintentos y rate limit.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from google import genai
from google.genai import types

from llm.config import vertex_credentials_path_resolved
from llm.rate_limit import wait_for_slot

logger = logging.getLogger(__name__)

_client: Optional[genai.Client] = None


# Carga las credenciales de cuenta de servicio para Vertex AI desde el archivo indicado en el .env.
def _load_vertex_service_account_credentials():
    path = vertex_credentials_path_resolved()
    if not path:
        return None
    if not os.path.exists(path):
        raise RuntimeError(f"GEMINI_VERTEX_CREDENTIALS_PATH no existe: {path}")
    from google.oauth2 import service_account

    scopes = ("https://www.googleapis.com/auth/cloud-platform",)
    return service_account.Credentials.from_service_account_file(path, scopes=scopes)


# Devuelve el cliente Gemini singleton (lo crea la primera vez con project/location del .env).
def get_client() -> genai.Client:
    global _client
    if _client is None:
        kwargs: dict = {"vertexai": True}
        creds = _load_vertex_service_account_credentials()
        if creds is not None:
            kwargs["credentials"] = creds
        project = (os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or "").strip()
        location = (os.getenv("GOOGLE_CLOUD_LOCATION") or os.getenv("VERTEX_LOCATION") or "").strip()
        if project:
            kwargs["project"] = project
        if location:
            kwargs["location"] = location
        _client = genai.Client(**kwargs)
        logger.info("Cliente LLM: Vertex AI (proyecto=%s, ubicación=%s)", project or "(ADC/env)", location or "default SDK")
    return _client


# Detecta si una excepción es de cuota/rate limit (429, RESOURCE_EXHAUSTED).
def _is_quota(e: Exception) -> bool:
    s = (str(e) or "").lower()
    return "429" in s or "resource exhausted" in s or "quota" in s


# Llama a Gemini con throttle RPM y reintentos automáticos ante errores de cuota (429). Devuelve el texto generado.
def generate(
    model: str,
    contents: str,
    *,
    temperature: float = 0.0,
    max_output_tokens: int = 8192,
    json_mode: bool = False,
    max_retries: int = 4,
) -> str:
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
