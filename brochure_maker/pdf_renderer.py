"""Server-side PDF export using Playwright headless Chromium."""

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

def _playwright_chromium_executable() -> Optional[str]:
    """Locate Playwright's bundled Chromium so headless tooling works even when
    no system Chrome is installed (the common case in CI/containers)."""
    import glob

    roots = [
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "",
        "/opt/pw-browsers",
        str(Path.home() / ".cache" / "ms-playwright"),
        "/root/.cache/ms-playwright",
    ]
    patterns = (
        "chromium-*/chrome-linux/chrome",
        "chromium_headless_shell-*/chrome-linux/headless_shell",
        "chromium-*/chrome-linux/headless_shell",
    )
    for root in roots:
        if not root:
            continue
        for pattern in patterns:
            for candidate in sorted(glob.glob(os.path.join(root, pattern)), reverse=True):
                if Path(candidate).exists():
                    return candidate
    return None


CHROME_CLI = next(
    (
        candidate
        for candidate in (
            shutil.which("google-chrome"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            _playwright_chromium_executable(),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        )
        if candidate and Path(candidate).exists()
    ),
    None,
)
HAS_CHROME_CLI = CHROME_CLI is not None
HAS_PDF_RENDERER = HAS_PLAYWRIGHT or HAS_CHROME_CLI


async def render_pdf(
    html_content: str,
    output_path: Optional[str] = None,
    width_px: Optional[int] = None,
    height_px: Optional[int] = None,
    base_url: Optional[str] = None,
) -> bytes:
    """Render HTML to PDF using headless Chromium.

    Args:
        html_content: The clean HTML string to render.
        output_path: Optional file path to save the PDF.

    Returns:
        PDF bytes.
    """
    if not HAS_PDF_RENDERER:
        raise RuntimeError(
            "No PDF renderer is available. Install Playwright or Google Chrome/Chromium."
        )

    prepared_html = _prepare_html_for_render(html_content, base_url=base_url, width_px=width_px, height_px=height_px)

    if not HAS_PLAYWRIGHT:
        return await asyncio.to_thread(
            _render_pdf_with_chrome_cli,
            prepared_html,
            output_path=output_path,
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = await browser.new_page()

        # Set the HTML content
        await page.set_content(prepared_html, wait_until="networkidle")

        # Wait for fonts to load
        await page.wait_for_timeout(1000)

        # Generate PDF with exact slide dimensions
        pdf_bytes = await page.pdf(
            width=f"{int(width_px or 1200)}px",
            height=f"{int(height_px or 750)}px",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )

        await browser.close()

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)

    return pdf_bytes


def _prepare_html_for_render(
    html_content: str,
    *,
    base_url: Optional[str],
    width_px: Optional[int],
    height_px: Optional[int],
) -> str:
    html = html_content
    if base_url:
        base = base_url.rstrip("/") + "/"

        def absolute_root_url(match: re.Match[str]) -> str:
            prefix, path = match.groups()
            return f"{prefix}{base}{path.lstrip('/')}"

        html = re.sub(r'((?:src|href)=["\'])(/(?:api|static)/[^"\']*)', absolute_root_url, html)
        html = re.sub(r'(url\(["\']?)(/(?:api|static)/[^"\'\)]+)', absolute_root_url, html)
        html = re.sub(r'(&quot;)(/(?:api|static)/[^&"]*)', absolute_root_url, html)

    page_css = ""
    if width_px and height_px:
        page_css = (
            "<style id=\"pdf-render-page-size\">"
            f"@page {{ size: {int(width_px)}px {int(height_px)}px; margin: 0; }}"
            "html, body { margin: 0 !important; }"
            "</style>"
        )
    base_tag = f'<base href="{base_url.rstrip("/") + "/" if base_url else ""}">' if base_url else ""
    injection = base_tag + page_css
    if injection and "</head>" in html.lower():
        html = re.sub(r"</head>", injection + "</head>", html, count=1, flags=re.IGNORECASE)
    elif injection:
        html = injection + html
    return html


def _render_pdf_with_chrome_cli(
    html_content: str,
    *,
    output_path: Optional[str],
) -> bytes:
    if not CHROME_CLI:
        raise RuntimeError("Google Chrome/Chromium is not available for PDF export.")

    with tempfile.TemporaryDirectory(prefix="brochure_pdf_export_") as tmp:
        tmp_dir = Path(tmp)
        html_path = tmp_dir / "export.html"
        pdf_path = Path(output_path) if output_path else tmp_dir / "export.pdf"
        html_path.write_text(html_content, encoding="utf-8")
        pdf_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(CHROME_CLI),
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--run-all-compositor-stages-before-draw",
            f"--print-to-pdf={pdf_path}",
            f"file://{html_path}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=90)
        if result.returncode != 0 or not pdf_path.exists():
            message = (result.stderr or result.stdout or "Chrome PDF export failed").strip()
            raise RuntimeError(message)
        pdf_bytes = pdf_path.read_bytes()

    if output_path:
        Path(output_path).write_bytes(pdf_bytes)
    return pdf_bytes
