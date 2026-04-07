from __future__ import annotations

from typing import Optional

from llm.client import generate
from llm.config import interpret_char_limit, max_output_tokens, model as resolve_model
from llm.prompts import interpret_default


# Envía el texto a Gemini con una instrucción libre y devuelve la interpretación en texto plano.
def ask_gemini(
    text: str,
    instruction: str = "Qué entiendes de este texto?",
    model: Optional[str] = None,
) -> str:
    if not (text or "").strip():
        return ""
    m = resolve_model(model)
    body = text.strip()[: interpret_char_limit()]
    return generate(
        m,
        interpret_default(instruction, body),
        temperature=0.25,
        max_output_tokens=max_output_tokens("interpret"),
        json_mode=False,
    )
