"""AI Chat — conversational brochure editing via Claude Sonnet 4.6."""

from __future__ import annotations

import json
import os

import httpx
from dotenv import load_dotenv
from pathlib import Path as _Path

load_dotenv(_Path(__file__).resolve().parent.parent / ".env", override=True)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-sonnet-4-6"

SYSTEM_PROMPT = """You are an AI assistant that edits HTML brochures based on user instructions.

You receive the current text content of each slide in the brochure, structured by slide ID and element class/tag.

CONTEXT MODES:
- If you see "=== ACTIVE SLIDE: slideN ===" the user is focused on that specific slide. Prioritise edits to that slide unless they explicitly reference other slides.
- If you see "SELECTED TEXT:" information, the user likely wants to edit that specific text or element. Focus your edits on the selected element.
- If you see "--- OTHER SLIDES (summary only) ---" those slides are listed briefly; you can still edit them if the user asks, but the active slide is the primary target.
- If all slides have full content (no ACTIVE marker), the user is in "all slides" mode — treat them equally.

When the user asks you to make changes, return a JSON response with this exact format:
{
  "reply": "A brief human-readable description of what you changed",
  "edits": [
    {
      "slide_id": "slide1",
      "selector": "#slide1 h1",
      "new_html": "The new HTML content for this element"
    }
  ]
}

Rules:
- Return ONLY valid JSON, no markdown code fences
- The "selector" must be a valid CSS selector that uniquely targets the element within the brochure
- Build selectors using the slide ID and element tag/class, e.g. "#slide1 h1", "#slide2 .body-text", "#slide3 .subtitle"
- "new_html" is the replacement innerHTML (can include <br>, <em>, <strong>, etc.)
- If the user asks a question without requesting changes, return edits as an empty array
- Keep edits minimal — only change what was requested
- Preserve existing HTML formatting (line breaks, emphasis) unless asked to change it
- You can make multiple edits across different slides in one response
- If the user is vague, make your best judgment and explain in the reply what you did"""


async def chat_with_brochure(
    message: str,
    brochure_context: list,
    history: list | None = None,
    selection_context: dict | None = None,
    active_slide_id: str | None = None,
) -> dict:
    """Process a chat message and return AI response with optional edits."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in environment")

    # Build context string from brochure data
    context_lines = []

    if active_slide_id:
        # Focused mode: active slide first, then summaries
        summary_lines = []
        for slide in brochure_context:
            sid = slide.get("id", "unknown")
            if slide.get("is_active"):
                context_lines.append(f"\n=== ACTIVE SLIDE: {sid} ===")
                for el in slide.get("elements", []):
                    classes = el.get("classes", "")
                    tag = el.get("tag", "div")
                    text = el.get("text", "")
                    context_lines.append(f'  <{tag} class="{classes}"> {text}')
            elif slide.get("summary_only"):
                slide_type = slide.get("slide_type", "unknown")
                summary_lines.append(f"  [{sid}: {slide_type} slide]")
            else:
                context_lines.append(f"\n--- {sid} ---")
                for el in slide.get("elements", []):
                    classes = el.get("classes", "")
                    tag = el.get("tag", "div")
                    text = el.get("text", "")
                    context_lines.append(f'  <{tag} class="{classes}"> {text}')

        if summary_lines:
            context_lines.append("\n--- OTHER SLIDES (summary only) ---")
            context_lines.extend(summary_lines)
    else:
        # All-slides mode (original behavior)
        for slide in brochure_context:
            sid = slide.get("id", "unknown")
            context_lines.append(f"\n--- {sid} ---")
            for el in slide.get("elements", []):
                classes = el.get("classes", "")
                tag = el.get("tag", "div")
                text = el.get("text", "")
                context_lines.append(f'  <{tag} class="{classes}"> {text}')

    context_str = "\n".join(context_lines)

    # Build selection context string
    selection_str = ""
    if selection_context:
        sel_text = selection_context.get("selected_text", "")
        el_tag = selection_context.get("element_tag", "div")
        el_class = selection_context.get("element_class", "")
        sel_slide = selection_context.get("slide_id", "unknown")
        full_text = selection_context.get("full_element_text", "")
        selection_str = (
            f'\n\nSELECTED TEXT: "{sel_text}"'
            f'\nIn element: <{el_tag} class="{el_class}">'
            f"\nIn slide: {sel_slide}"
            f"\nFull element text: {full_text}"
        )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add conversation history for continuity (last 10 exchanges)
    if history:
        for msg in history[-10:]:
            role = msg.get("role", "")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": msg.get("content", "")})

    # Add current message with brochure context
    user_content = (
        f"Current brochure content:\n{context_str}"
        f"{selection_str}\n\n"
        f"User request: {message}"
    )
    messages.append({"role": "user", "content": user_content})

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": messages,
                "max_tokens": 4096,
                "temperature": 0.3,
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()

    # Strip markdown fences if present
    if content.startswith("```"):
        lines = content.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        result = {"reply": content, "edits": []}

    if "reply" not in result:
        result["reply"] = "Done."
    if "edits" not in result:
        result["edits"] = []

    return result
