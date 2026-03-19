"""
Limitador global de ritmo (sync) para no exceder RPM del free tier.
Un solo hilo de espera por proceso (adecuado para uvicorn 1 worker).
"""
from __future__ import annotations

import logging
import threading
import time

from llm.config import seconds_between_calls

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_next_allowed_monotonic: float = 0.0


def wait_for_slot(model_name: str) -> None:
    """Espera el mínimo necesario antes de iniciar otra llamada a la API."""
    global _next_allowed_monotonic
    interval = seconds_between_calls(model_name)
    with _lock:
        now = time.monotonic()
        wait = _next_allowed_monotonic - now
        if wait > 0:
            logger.debug("RPM: esperando %.2fs antes de llamar a Gemini", wait)
            time.sleep(wait)
        _next_allowed_monotonic = time.monotonic() + interval
