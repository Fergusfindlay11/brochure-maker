"""Send PDF data to Claude Sonnet via OpenRouter and get structured brochure analysis."""

import json
import os
from typing import AsyncIterator

import httpx
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-sonnet-4"

SYSTEM_PROMPT = """You are a brochure analysis expert. You receive images of each page of a PDF brochure along with extracted text content. Your job is to analyse the brochure and produce a structured JSON output that describes the brochure's content, layout, and style.

## Your Task
Analyse each page and:
1. Identify its purpose/type (cover, about/description, highlights/features, photo gallery, floor plan, services/managed, location/area, travel/transport, contacts/back cover, or generic)
2. Extract all text content structured by role (heading, subheading, body, caption, tagline, legal text, contact info)
3. Describe any images present and what they show
4. Identify the overall colour scheme, typography style, and visual tone

## Output Format
Return ONLY valid JSON with this exact structure (no markdown, no code fences):
{
  "brochure_name": "Name of the property/building",
  "location": "Area/city",
  "tagline": "Main tagline if present",
  "colour_scheme": {
    "primary": "#hex",
    "primary_dark": "#hex",
    "primary_light": "#hex",
    "text_light": "#hex",
    "text_dark": "#hex",
    "background": "#hex"
  },
  "typography": {
    "heading_style": "serif/sans-serif",
    "body_style": "serif/sans-serif",
    "heading_weight": "normal/bold/900",
    "overall_feel": "description of the typographic feel"
  },
  "slides": [
    {
      "page_num": 1,
      "type": "cover",
      "content": {
        "heading": "Main title",
        "subheading": "Subtitle text",
        "tagline": "Tagline if any",
        "logo_description": "Description of logo/mark if visible"
      }
    },
    {
      "page_num": 2,
      "type": "text_and_photos",
      "content": {
        "heading": "Section heading",
        "body_text": "Full paragraph text",
        "images": [
          {"description": "What the image shows", "caption": "Caption text if any", "position": "top-right/bottom-left/etc"}
        ]
      }
    },
    {
      "page_num": 3,
      "type": "highlights_grid",
      "content": {
        "heading": "Highlights",
        "features": [
          {"icon_hint": "warehouse/shower/bicycle/lift/lightning/plug/chair/breeam", "text": "Feature description"}
        ]
      }
    },
    {
      "page_num": 4,
      "type": "photo_gallery",
      "content": {
        "images": [
          {"description": "What the image shows", "caption": "Caption", "size": "large/small"}
        ]
      }
    },
    {
      "page_num": 5,
      "type": "floor_plan",
      "content": {
        "heading": "Floor name",
        "size_label": "Square footage",
        "specs": ["28 x Workstations", "1 x Kitchen"],
        "legend": [
          {"colour": "blue", "label": "Office space"},
          {"colour": "yellow", "label": "Showers"}
        ],
        "note": "Footnote text"
      }
    },
    {
      "page_num": 6,
      "type": "services_grid",
      "content": {
        "heading": "Fully Managed",
        "intro_text": "Description text",
        "services": [
          {"icon_hint": "key/rates/gear/wifi/lightning/broom", "label": "Service name"}
        ],
        "note": "Footnote",
        "images": [{"description": "Image desc", "caption": "Caption"}]
      }
    },
    {
      "page_num": 7,
      "type": "location",
      "content": {
        "heading": "Where ideas flourish",
        "body_text": "Description of the area",
        "images": [
          {"description": "What the image shows", "caption": "Caption"}
        ]
      }
    },
    {
      "page_num": 8,
      "type": "travel_map",
      "content": {
        "heading": "Travel times",
        "stations": [
          {
            "name": "KING'S CROSS ST. PANCRAS",
            "time": "12 Mins",
            "lines": ["thameslink", "northern", "victoria", "circle", "hammersmith", "metropolitan"]
          }
        ],
        "note": "Times from Google"
      }
    },
    {
      "page_num": 9,
      "type": "contacts",
      "content": {
        "contacts": [
          {"name": "Alice Logan", "email": "alice@example.com", "phone": "07720 070417"}
        ],
        "agency": "JLL",
        "legal_text": "Disclaimer text",
        "marketing_credit": "Marketing by..."
      }
    }
  ]
}

## Important Rules
- Return ONLY the JSON, no explanations or markdown
- Use the exact slide type names listed above
- For icon_hint, use descriptive keywords that map to common icons (warehouse, shower, bicycle, lift, lightning, plug, chair, key, rates, gear, wifi, broom, leaf, solar, lock, gym, train, parking)
- For transport lines, use lowercase identifiers: bakerloo, central, circle, district, dlr, elizabeth, hammersmith, jubilee, metropolitan, northern, overground, piccadilly, thameslink, victoria, waterloo, national, tram
- Extract ALL text faithfully - don't summarise or paraphrase
- Describe images in enough detail that someone could source a replacement photo
"""


async def analyse_brochure(pdf_data: dict) -> dict:
    """Send PDF page data to Claude Sonnet via OpenRouter and get analysis."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key or api_key == "your_openrouter_api_key_here":
        raise ValueError("OPENROUTER_API_KEY not set in .env file")

    # Build the user message with page images and text
    content_parts = [
        {
            "type": "text",
            "text": f"Analyse this {pdf_data['page_count']}-page brochure. Here are renders of each page followed by the extracted text content.\n\n"
        }
    ]

    for page in pdf_data["pages"]:
        # Add page image
        content_parts.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{page['render_base64']}"
            }
        })

        # Add extracted text summary for this page
        text_summary = _build_text_summary(page)
        content_parts.append({
            "type": "text",
            "text": f"\n--- Page {page['page_num']} extracted text ---\n{text_summary}\n"
        })

    # Add dominant colours info
    if pdf_data.get("dominant_colours"):
        content_parts.append({
            "type": "text",
            "text": f"\n--- Dominant colours across document ---\n{', '.join(pdf_data['dominant_colours'][:8])}\n"
        })

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:8000",
                "X-Title": "Brochure Maker",
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content_parts},
                ],
                "max_tokens": 8000,
                "temperature": 0.1,
            },
        )

    if response.status_code != 200:
        error_detail = response.text
        raise RuntimeError(f"OpenRouter API error ({response.status_code}): {error_detail}")

    result = response.json()
    content = result["choices"][0]["message"]["content"]

    # Parse JSON from the response (strip any markdown fences if present)
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

    try:
        analysis = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse AI response as JSON: {e}\nRaw: {content[:500]}")

    return analysis


def _build_text_summary(page: dict) -> str:
    """Build a readable text summary from extracted text blocks."""
    if not page.get("text_blocks"):
        return "(No text extracted)"

    # Group by approximate size to identify headings vs body
    lines = []
    for block in page["text_blocks"]:
        size = block.get("size", 12)
        text = block.get("text", "")
        font = block.get("font", "")

        if size > 20:
            lines.append(f"[HEADING size={size}] {text}")
        elif size > 14:
            lines.append(f"[SUBHEADING size={size}] {text}")
        else:
            lines.append(text)

    return "\n".join(lines)


async def analyse_brochure_streaming(pdf_data: dict) -> AsyncIterator[str]:
    """Stream analysis progress updates."""
    yield "event: status\ndata: Starting AI analysis...\n\n"

    yield "event: status\ndata: Sending pages to Claude Sonnet...\n\n"

    try:
        analysis = await analyse_brochure(pdf_data)
        yield f"event: status\ndata: Analysis complete - {len(analysis.get('slides', []))} slides identified\n\n"
        yield f"event: result\ndata: {json.dumps(analysis)}\n\n"
    except Exception as e:
        yield f"event: error\ndata: {str(e)}\n\n"
