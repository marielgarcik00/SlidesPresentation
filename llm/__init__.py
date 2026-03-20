"""
Capa LLM modular: cliente Vertex AI (Gemini), límites RPM, interpretación, segmentación y relleno de slides.
"""
from llm.interpret import ask_gemini, ask_gemini_title_and_subtitles
from llm.segmentation import segment_text_into_parts
from llm.slide_fill import ask_gemini_batch_for_slides, ask_gemini_for_slide

__all__ = [
    "ask_gemini",
    "ask_gemini_title_and_subtitles",
    "ask_gemini_batch_for_slides",
    "ask_gemini_for_slide",
    "segment_text_into_parts",
]
