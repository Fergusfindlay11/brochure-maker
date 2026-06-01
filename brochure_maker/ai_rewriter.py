"""AI Content Rewrite — calls Claude via OpenRouter to rewrite text."""

import os
import httpx
from dotenv import load_dotenv
from pathlib import Path as _Path

load_dotenv(_Path(__file__).resolve().parent.parent / ".env", override=True)
MODEL = "anthropic/claude-sonnet-4-6"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

PROMPTS = {
    "rewrite": "Rewrite the following text while keeping the same meaning. Make it clear and well-written. Return ONLY the rewritten text, no preamble.",
    "concise": "Make the following text more concise. Cut unnecessary words while preserving all key information. Return ONLY the concise version, no preamble.",
    "expand": "Expand the following text with more detail and description while keeping the same tone. Return ONLY the expanded text, no preamble.",
    "formal": "Rewrite the following text in a formal, professional tone suitable for a property brochure. Return ONLY the formal version, no preamble.",
    "persuasive": "Rewrite the following text to be more persuasive and compelling, ideal for selling a property. Return ONLY the persuasive version, no preamble.",
}


async def rewrite_text(text: str, action: str) -> str:
    """Rewrite text using AI.

    Args:
        text: The original text to rewrite.
        action: One of 'rewrite', 'concise', 'expand', 'formal', 'persuasive'.

    Returns:
        The rewritten text string.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in environment")

    system_prompt = PROMPTS.get(action, PROMPTS["rewrite"])

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                "max_tokens": 1024,
                "temperature": 0.7,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
