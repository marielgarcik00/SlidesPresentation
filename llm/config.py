"""
Configuración central: modelo Vertex AI, RPM (throttle entre llamadas), tamaño de batch.
Los límites por defecto se infieren del nombre del modelo si GEMINI_RPM_LIMIT=auto.
Throttle = control de velocidad. Es un mecanismo que frena artificialmente el ritmo de las llamadas para no superar el RPM.
Autenticación: Application Default Credentials o GEMINI_VERTEX_CREDENTIALS_PATH.
Proyecto y región: GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION (el SDK puede inferir
el proyecto desde ADC si no está en el entorno).
"""
from __future__ import annotations

import os
from typing import Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Devuelve la ruta absoluta al JSON de credenciales de Vertex AI (o None si no está configurada).
def vertex_credentials_path_resolved() -> Optional[str]:
    raw = (os.getenv("GEMINI_VERTEX_CREDENTIALS_PATH") or "").strip()
    if not raw:
        return None
    path = raw if os.path.isabs(raw) else os.path.normpath(os.path.join(_PROJECT_ROOT, raw))
    return path


# Resuelve el modelo a usar: primero el parámetro, luego app_config, luego el .env (default: gemini-2.0-flash).
def model(default: Optional[str] = None) -> str:
    if (default or "").strip():
        return default.strip()
    try:
        import app_config

        return str(app_config.DEFAULT_GEMINI_MODEL).strip()
    except Exception:
        return (os.getenv("GEMINI_MODEL") or "gemini-2.0-flash").strip()


# Cuántas slides se agrupan en cada llamada batch a Gemini (entre 3 y 14, configurable con GEMINI_BATCH_CHUNK).
def batch_chunk_size() -> int:
    return max(3, min(14, int(os.getenv("GEMINI_BATCH_CHUNK", "10"))))


# Devuelve el límite de tokens de salida para cada tipo de llamada (interpret, structure, segment, slide, batch).
def max_output_tokens(kind: str = "default") -> int:
    caps = {
        "interpret": int(os.getenv("GEMINI_MAX_TOKENS_INTERPRET", "2048")),
        "structure": int(os.getenv("GEMINI_MAX_TOKENS_STRUCTURE", "2048")),
        "segment": int(os.getenv("GEMINI_MAX_TOKENS_SEGMENT", "8192")),
        "slide": int(os.getenv("GEMINI_MAX_TOKENS_SLIDE", "2048")),
        "batch": int(os.getenv("GEMINI_MAX_TOKENS_BATCH", "8192")),
        "default": int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "8192")),
    }
    return caps.get(kind, caps["default"])


# Límite de caracteres del texto de entrada para el paso de interpretación libre.
def interpret_char_limit() -> int:
    return int(os.getenv("GEMINI_INTERPRET_CHARS", "10000"))


# Límite de caracteres del texto de entrada para el paso de estructuración (content_type, títulos).
def structure_char_limit() -> int:
    return int(os.getenv("GEMINI_STRUCTURE_CHARS", "8000"))


# Límite de caracteres del texto de entrada para el paso de segmentación en partes por slide.
def segment_text_char_limit() -> int:
    return int(os.getenv("GEMINI_SEGMENT_CHARS", "12000"))


# Calcula el RPM efectivo del modelo: lee app_config o .env, con fallback por tipo de modelo (Pro/Flash/Gemma).
def effective_rpm_for_model(model_name: str) -> float:
    try:
        import app_config

        raw = str(getattr(app_config, "DEFAULT_GEMINI_RPM", "") or "").strip().lower()
        if raw and raw != "auto":
            return max(0.5, float(raw))
    except (ValueError, Exception):
        pass
    explicit = (os.getenv("GEMINI_RPM_LIMIT") or "").strip().lower()
    if explicit and explicit != "auto":
        return max(0.5, float(explicit))
    m = (model_name or "").lower()
    if "gemma" in m:
        return 55.0
    if "pro" in m and "flash" not in m:
        return 1.5
    if "lite" in m:
        return 12.0
    return 12.0


# Convierte el RPM en segundos de espera entre llamadas para el throttle.
def seconds_between_calls(model_name: str) -> float:
    rpm = effective_rpm_for_model(model_name)
    return 60.0 / rpm
