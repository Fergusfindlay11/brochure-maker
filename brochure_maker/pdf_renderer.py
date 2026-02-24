"""Server-side PDF export using Playwright headless Chromium."""

import asyncio
import os
from pathlib import Path
from typing import Optional

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


async def render_pdf(
    html_content: str,
    output_path: Optional[str] = None,
) -> bytes:
    """Render HTML to PDF using headless Chromium.

    Args:
        html_content: The clean HTML string to render.
        output_path: Optional file path to save the PDF.

    Returns:
        PDF bytes.
    """
    if not HAS_PLAYWRIGHT:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && python -m playwright install chromium"
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Set the HTML content
        await page.set_content(html_content, wait_until="networkidle")

        # Wait for fonts to load
        await page.wait_for_timeout(1000)

        # Generate PDF with exact slide dimensions
        pdf_bytes = await page.pdf(
            width="1200px",
            height="750px",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )

        await browser.close()

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)

    return pdf_bytes
