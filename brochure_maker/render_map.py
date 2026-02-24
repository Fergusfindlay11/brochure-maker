"""Standalone script: renders HTML via Playwright and outputs PNG to stdout.

Called as a subprocess to avoid event-loop / subprocess conflicts
inside uvicorn on Windows.

Usage:  python render_map.py <width> <height> < input.html > output.png
Env:    PLAYWRIGHT_BROWSERS_PATH — optional path to browser install directory
"""

import sys


def main() -> None:
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 940
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 750

    html = sys.stdin.buffer.read().decode("utf-8")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(html)
        try:
            page.wait_for_function(
                "document.title === 'MAP_READY'", timeout=15000
            )
        except Exception:
            import time
            time.sleep(3)
        png_bytes = page.screenshot(type="png")
        browser.close()

    sys.stdout.buffer.write(png_bytes)


if __name__ == "__main__":
    main()
