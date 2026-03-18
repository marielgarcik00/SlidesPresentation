# Compatibilidad: la lógica vive en el paquete `llm/` (modular).
from llm import (
    ask_gemini,
    ask_gemini_batch_for_slides,
    ask_gemini_for_slide,
    ask_gemini_title_and_subtitles,
    segment_text_into_parts,
)

__all__ = [
    "ask_gemini",
    "ask_gemini_title_and_subtitles",
    "ask_gemini_batch_for_slides",
    "ask_gemini_for_slide",
    "segment_text_into_parts",
]
