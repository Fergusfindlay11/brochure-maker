"""Render and compare exact-layout clean exports against source page renders."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from PIL import Image, ImageChops, ImageEnhance, ImageStat

from brochure_maker.pdf_renderer import CHROME_CLI


def build_visual_diff(
    project_dir: str | Path,
    output_dir: str | Path,
    *,
    base_url: str = "http://127.0.0.1:8000",
    clean_html: str | None = None,
    page_limit: int | None = None,
    page_numbers: list[int] | tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Create source/generated/diff PNGs and return a visual-fidelity report."""
    if not CHROME_CLI:
        raise RuntimeError("Google Chrome/Chromium is required for exact visual diff rendering.")

    project_path = Path(project_dir).expanduser().resolve()
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    metadata = _load_json(project_path / "exact_metadata.json")
    layout = metadata.get("layout") if isinstance(metadata.get("layout"), dict) else {}
    page_count = int(layout.get("page_count") or metadata.get("page_count") or 0)
    page_width = int(layout.get("page_width") or 1200)
    page_height = int(layout.get("page_height") or 750)
    if page_count <= 0:
        raise RuntimeError("Exact metadata does not include a page count.")
    pages_to_render = _selected_pages(page_count, page_limit=page_limit, page_numbers=page_numbers)

    clean_html = clean_html or _load_clean_export_html(project_path, base_url=base_url)
    page_reports: list[dict[str, Any]] = []
    for page_number in pages_to_render:
        original = _source_page_render(project_path, page_number)
        if not original:
            page_reports.append(
                {
                    "page_number": page_number,
                    "score": 0,
                    "blocker": "Missing source page render",
                }
            )
            continue
        original_out = output_path / f"page-{page_number:03d}-original.png"
        generated_out = output_path / f"page-{page_number:03d}-generated.png"
        diff_out = output_path / f"page-{page_number:03d}-diff.png"
        shutil.copy2(original, original_out)
        render_width, render_height = page_width, page_height
        try:
            with Image.open(original_out) as original_image:
                render_width, render_height = original_image.size
        except OSError:
            pass
        _render_clean_export_page(
            clean_html,
            page_number,
            generated_out,
            page_width=render_width,
            page_height=render_height,
            base_url=base_url,
        )
        score, mae = compare_images(original_out, generated_out, diff_out)
        page_reports.append(
            {
                "page_number": page_number,
                "score": round(score, 3),
                "mean_absolute_error": round(mae, 4),
                "original": str(original_out),
                "generated": str(generated_out),
                "diff": str(diff_out),
            }
        )

    scored_pages = [page for page in page_reports if "score" in page]
    score = sum(float(page["score"]) for page in scored_pages) / len(scored_pages) if scored_pages else 0.0
    blockers = [f"Page {page['page_number']}: {page['blocker']}" for page in page_reports if page.get("blocker")]
    blockers.extend(
        f"Page {page['page_number']}: visual score below 95"
        for page in scored_pages
        if float(page.get("score") or 0) < 95
    )
    return {
        "schema": "brochure-maker.exact-visual-diff.v1",
        "method": "headless-chrome-clean-export-vs-source-render",
        "score": round(score, 3),
        "accepted": score >= 95 and not blockers,
        "blockers": blockers,
        "page_count": len(pages_to_render),
        "project_page_count": page_count,
        "rendered_pages": pages_to_render,
        "page_width": page_width,
        "page_height": page_height,
        "output_dir": str(output_path),
        "pages": page_reports,
    }


def write_visual_diff_report(
    project_dir: str | Path,
    output_dir: str | Path,
    *,
    base_url: str = "http://127.0.0.1:8000",
    clean_html: str | None = None,
    page_limit: int | None = None,
    page_numbers: list[int] | tuple[int, ...] | None = None,
) -> Path:
    """Write visual diff images and a report JSON, returning the report path."""
    output_path = Path(output_dir).expanduser().resolve()
    report = build_visual_diff(
        project_dir,
        output_path,
        base_url=base_url,
        clean_html=clean_html,
        page_limit=page_limit,
        page_numbers=page_numbers,
    )
    report_path = output_path / "visual-diff.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report_path


def attach_visual_diff_to_browser_qa(project_dir: str | Path, visual_report_path: str | Path) -> Path:
    """Attach a visual diff summary to ``browser_qa.json`` for exact eval scoring."""
    project_path = Path(project_dir).expanduser().resolve()
    qa_path = project_path / "browser_qa.json"
    visual_path = Path(visual_report_path).expanduser().resolve()
    qa = _load_json(qa_path)
    report = _load_json(visual_path)
    qa["visual_diff"] = {
        "score": report.get("score", 0),
        "accepted": bool(report.get("accepted")),
        "method": report.get("method"),
        "report": str(visual_path),
        "blockers": report.get("blockers") or [],
        "pages": [
            {
                "page_number": page.get("page_number"),
                "score": page.get("score"),
                "original": page.get("original"),
                "generated": page.get("generated"),
                "diff": page.get("diff"),
            }
            for page in report.get("pages") or []
            if isinstance(page, dict)
        ],
    }
    qa_path.write_text(json.dumps(qa, indent=2, sort_keys=True), encoding="utf-8")
    return qa_path


def compare_images(original_path: str | Path, generated_path: str | Path, diff_path: str | Path) -> tuple[float, float]:
    """Compare two images and save an amplified visual diff."""
    with Image.open(original_path) as original_image, Image.open(generated_path) as generated_image:
        original = original_image.convert("RGB")
        generated = generated_image.convert("RGB")
        if generated.size != original.size:
            generated = _normalise_generated_size(generated, original.size)
        diff = ImageChops.difference(original, generated)
        stat = ImageStat.Stat(diff)
        mae = sum(stat.mean) / len(stat.mean)
        score = max(0.0, 100.0 * (1.0 - (mae / 255.0)))
        amplified = ImageEnhance.Brightness(diff).enhance(5.0)
        amplified.save(diff_path)
        return score, mae


def _normalise_generated_size(generated: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Return a generated render in the source page dimensions.

    Chrome screenshots can include the frame width rather than the actual PDF
    page width when the export's metadata has a wider layout variable than the
    page itself. In that case the left/top page region is the visual truth and
    the extra area is blank margin, so crop before falling back to resize.
    """
    target_width, target_height = target_size
    if generated.width >= target_width and generated.height >= target_height:
        return generated.crop((0, 0, target_width, target_height))
    return generated.resize(target_size, Image.Resampling.LANCZOS)


def _render_clean_export_page(
    clean_html: str,
    page_number: int,
    output_path: Path,
    *,
    page_width: int,
    page_height: int,
    base_url: str,
) -> None:
    page_html = _isolated_page_html(
        clean_html,
        page_number,
        page_width=page_width,
        page_height=page_height,
        base_url=base_url,
    )
    with tempfile.TemporaryDirectory(prefix="exact_visual_diff_") as tmp:
        html_path = Path(tmp) / f"page-{page_number:03d}.html"
        html_path.write_text(page_html, encoding="utf-8")
        errors: list[str] = []
        for attempt, headless_flag in enumerate(("--headless=new", "--headless"), start=1):
            profile_dir = Path(tmp) / f"chrome-profile-{attempt}"
            profile_dir.mkdir(parents=True, exist_ok=True)
            if output_path.exists():
                output_path.unlink()
            cmd = [
                str(CHROME_CLI),
                headless_flag,
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-background-networking",
                "--no-first-run",
                "--no-default-browser-check",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "--run-all-compositor-stages-before-draw",
                f"--user-data-dir={profile_dir}",
                f"--window-size={page_width},{page_height}",
                f"--screenshot={output_path}",
                f"file://{html_path}",
            ]
            result = _run_chrome_screenshot(cmd, output_path, timeout_seconds=90)
            if result["ok"]:
                return
            errors.append(f"attempt {attempt} failed with {headless_flag}: {result['message']}")
        raise RuntimeError("Chrome screenshot failed after retries: " + " | ".join(errors))


def _run_chrome_screenshot(cmd: list[str], output_path: Path, *, timeout_seconds: float) -> dict[str, Any]:
    """Run Chrome and return as soon as its screenshot file is usable.

    On macOS headless Chrome can leave helper processes alive after writing the
    screenshot. Waiting for process exit turns a finished page render into a
    timeout, which makes page-image packet generation much slower than it needs
    to be.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            if _usable_screenshot(output_path):
                _stop_process(proc)
                return {"ok": True, "message": "screenshot written"}
            if proc.poll() is not None:
                break
            time.sleep(0.2)

        if proc.poll() is None:
            _stop_process(proc, kill=True)
            if _usable_screenshot(output_path):
                return {"ok": True, "message": "screenshot written before timeout"}
            return {"ok": False, "message": f"timed out after {timeout_seconds:.0f}s"}

        stdout, stderr = proc.communicate(timeout=1)
        if proc.returncode == 0 and _usable_screenshot(output_path):
            return {"ok": True, "message": "screenshot written"}
        message = (stderr or stdout or "Chrome screenshot failed").strip()
        return {"ok": False, "message": message}
    finally:
        if proc.poll() is None:
            _stop_process(proc, kill=True)


def _usable_screenshot(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


def _stop_process(proc: subprocess.Popen[str], *, kill: bool = False) -> None:
    try:
        proc.kill() if kill else proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            return
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return


def _isolated_page_html(
    clean_html: str,
    page_number: int,
    *,
    page_width: int,
    page_height: int,
    base_url: str,
) -> str:
    soup = BeautifulSoup(clean_html, "html.parser")
    if soup.head is None:
        head = soup.new_tag("head")
        soup.insert(0, head)
    base = soup.new_tag("base", href=base_url.rstrip("/") + "/")
    soup.head.insert(0, base)
    style = soup.new_tag("style")
    style.string = (
        f"html,body{{margin:0!important;width:{page_width}px!important;height:{page_height}px!important;"
        "overflow:hidden!important;background:#fff!important;}}"
        ".exact-pages{align-items:flex-start!important;gap:0!important;margin:0!important;padding:0!important;}"
        f".exact-page-frame{{margin:0!important;width:{page_width}px!important;height:{page_height}px!important;"
        "page-break-after:auto!important;overflow:hidden!important;}}"
        f".exact-page{{box-shadow:none!important;transform:none!important;width:{page_width}px!important;height:{page_height}px!important;}}"
    )
    soup.head.append(style)
    if soup.body:
        classes = set(soup.body.get("class", []))
        classes.add("export-clean")
        soup.body["class"] = sorted(classes)
    main = soup.select_one(".exact-pages")
    frame = soup.select_one(f'.exact-page-frame[data-page-num="{page_number}"]')
    if not main or not frame:
        raise RuntimeError(f"Could not find page {page_number} in clean export HTML.")
    isolated = BeautifulSoup(str(frame), "html.parser")
    main.clear()
    main.append(isolated)
    return str(soup)


def _load_clean_export_html(project_dir: Path, *, base_url: str) -> str:
    project_id = project_dir.name
    url = f"{base_url.rstrip('/')}/api/projects/{project_id}/export/html"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        from app import _prepare_exact_export_html

        return _prepare_exact_export_html(project_dir)


def _source_page_render(project_dir: Path, page_number: int) -> Path | None:
    candidates = [
        project_dir / "exact_assets" / f"page{page_number:03d}-full.png",
        project_dir / "exact_layout_model" / "backgrounds" / f"page-{page_number:03d}.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _selected_pages(
    page_count: int,
    *,
    page_limit: int | None = None,
    page_numbers: list[int] | tuple[int, ...] | None = None,
) -> list[int]:
    if page_numbers is not None:
        selected = sorted({int(page) for page in page_numbers if int(page) > 0})
        return [page for page in selected if page <= page_count]
    if page_limit is not None:
        page_count = min(page_count, int(page_limit))
    return list(range(1, page_count + 1))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build visual diffs for an exact PDF project.")
    parser.add_argument("project_dir", help="Path to a generated exact project")
    parser.add_argument("--output-dir", default="evals/reports/visual", help="Directory for report and PNG artifacts")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Running app base URL for project assets/export HTML")
    parser.add_argument("--page-limit", type=int, default=None)
    parser.add_argument("--page", type=int, action="append", dest="page_numbers", help="Render only this page. Repeat for multiple pages.")
    parser.add_argument("--update-browser-qa", action="store_true", help="Attach the visual diff summary to browser_qa.json")
    parser.add_argument("--write-html-assessment", action="store_true", help="Write html-assessment.json beside visual-diff.json")
    args = parser.parse_args()
    report_path = write_visual_diff_report(
        args.project_dir,
        args.output_dir,
        base_url=args.base_url,
        page_limit=args.page_limit,
        page_numbers=args.page_numbers,
    )
    if args.update_browser_qa:
        attach_visual_diff_to_browser_qa(args.project_dir, report_path)
    if args.write_html_assessment:
        from brochure_maker.exact_html_assessment import (
            attach_html_assessment_to_browser_qa,
            write_html_assessment,
        )

        assessment_path = Path(args.output_dir).expanduser().resolve() / "html-assessment.json"
        write_html_assessment(args.project_dir, assessment_path, visual_report_path=report_path)
        if args.update_browser_qa:
            attach_html_assessment_to_browser_qa(args.project_dir, assessment_path)
    print(report_path)


if __name__ == "__main__":
    main()
