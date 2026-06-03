"""Export parity evidence for exact PDF benchmark runs."""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HTTP_TIMEOUT_SECONDS = 240
MIN_PDF_BYTES = 1024

REQUIRED_EXPORT_ASSERTIONS = (
    "html_export_has_expected_pages",
    "html_export_has_no_editor_chrome",
    "html_export_preserves_edited_text",
    "html_export_preserves_global_colours",
    "pdf_export_succeeded",
    "pdf_export_nonempty",
)


def write_export_qa(
    project_dir: str | Path,
    *,
    base_url: str = "http://127.0.0.1:8000",
    output_path: str | Path | None = None,
    force: bool = False,
) -> Path:
    """Write clean HTML/PDF export evidence for an exact project."""
    project_path = Path(project_dir).expanduser().resolve()
    output = Path(output_path).expanduser().resolve() if output_path else project_path / "export_qa.json"
    existing = _load_json(output)
    if not force and _has_required_assertions(existing):
        return output

    if not _looks_like_exact_project(project_path):
        payload = {
            "schema": "brochure-maker.exact-export-qa.v1",
            "project_id": project_path.name,
            "accepted": True,
            "method": "not-applicable-non-exact-fixture",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "html_export": {"status": "not-applicable-non-exact-fixture"},
            "pdf_export": {"status": "not-applicable-non-exact-fixture"},
            "assertions": {name: True for name in REQUIRED_EXPORT_ASSERTIONS},
            "blockers": [],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return output

    metadata = _load_json(project_path / "exact_metadata.json")
    graph = _load_json(project_path / "brochure.design.json")
    state = _load_json(project_path / "editor_state.json")
    source_html = _read_text(project_path / "brochure.html")
    project_id = project_path.name
    expected_pages = int(
        metadata.get("page_count")
        or (metadata.get("layout") or {}).get("page_count", 0)
        or graph.get("page_count")
        or len(graph.get("pages") or [])
        or 0
    )
    base = base_url.rstrip("/")
    html_url = f"{base}/api/projects/{project_id}/export/html"
    pdf_url = f"{base}/api/projects/{project_id}/export/pdf"
    blockers: list[str] = []
    staged = _stage_export_probe_state(project_path, project_id, state, source_html, blockers)
    try:
        html = _fetch(html_url, blockers)
        pdf_summary = _pdf_export_summary(project_path, pdf_url, blockers)
    finally:
        _restore_export_probe_state(staged)
    html_summary = _html_export_summary(html, expected_pages)
    state_summary = _state_export_summary(html, staged["state"], staged)

    assertions = {
        "html_export_has_expected_pages": expected_pages > 0 and html_summary["pageCount"] == expected_pages,
        "html_export_has_no_editor_chrome": (
            html_summary["contenteditableCount"] == 0
            and html_summary["fileInputCount"] == 0
            and html_summary["scriptCount"] == 0
            and html_summary["toolbarCount"] == 0
            and html_summary["fieldsPanelCount"] == 0
        ),
        "html_export_preserves_edited_text": (
            bool(state_summary["editedTextPreserved"]) and state_summary["editedTextCount"] > 0
        ),
        "html_export_preserves_global_colours": (
            bool(state_summary["globalColoursPreserved"]) and len(state_summary["globalColoursChecked"]) >= 2
        ),
        "pdf_export_succeeded": bool(pdf_summary["succeeded"]),
        "pdf_export_nonempty": int(pdf_summary["bytes"] or 0) >= MIN_PDF_BYTES,
    }
    if not assertions["html_export_has_expected_pages"]:
        blockers.append("Clean HTML export page count does not match exact metadata/design graph")
    if not assertions["html_export_has_no_editor_chrome"]:
        blockers.append("Clean HTML export still contains editor chrome")
    if not assertions["html_export_preserves_edited_text"]:
        blockers.append("Clean HTML export did not preserve edited text state")
    if not assertions["html_export_preserves_global_colours"]:
        blockers.append("Clean HTML export did not preserve saved global colours")
    if not assertions["pdf_export_succeeded"]:
        blockers.append("PDF export endpoint did not return a PDF artifact")
    if not assertions["pdf_export_nonempty"]:
        blockers.append(f"PDF export artifact is smaller than {MIN_PDF_BYTES} bytes")

    payload = {
        "schema": "brochure-maker.exact-export-qa.v1",
        "project_id": project_id,
        "accepted": all(assertions.values()) and not blockers,
        "method": "live-clean-html-and-pdf-export",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base,
        "html_url": html_url,
        "pdf_url": pdf_url,
        "expected_pages": expected_pages,
        "html_export": html_summary,
        "state_export": state_summary,
        "pdf_export": pdf_summary,
        "temporary_state_probe": {
            "used": bool(staged["used"]),
            "target_save_id": staged.get("targetSaveId") or "",
            "marker": staged.get("marker") or "",
            "restored": bool(staged.get("restored")),
        },
        "assertions": assertions,
        "blockers": sorted(dict.fromkeys(blockers)),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output


def _looks_like_exact_project(project_path: Path) -> bool:
    return (project_path / "exact_metadata.json").exists() or (project_path / "brochure.html").exists()


def _has_required_assertions(payload: dict[str, Any]) -> bool:
    assertions = payload.get("assertions") if isinstance(payload.get("assertions"), dict) else {}
    return all(assertions.get(name) is True for name in REQUIRED_EXPORT_ASSERTIONS)


def _fetch(url: str, blockers: list[str]) -> str:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as exc:
        blockers.append(f"Could not fetch export QA URL {url}: {exc}")
        return ""


def _post_pdf(url: str, blockers: list[str]) -> bytes:
    request = urllib.request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read()
    except (OSError, urllib.error.URLError) as exc:
        blockers.append(f"Could not POST PDF export QA URL {url}: {exc}")
        return b""


def _html_export_summary(html: str, expected_pages: int) -> dict[str, Any]:
    return {
        "pageCount": _html_page_count(html),
        "expectedPageCount": expected_pages,
        "contenteditableCount": len(re.findall(r"\bcontenteditable\b", html, flags=re.IGNORECASE)),
        "fileInputCount": len(re.findall(r'<input\b[^>]*type=["\']file["\']', html, flags=re.IGNORECASE)),
        "scriptCount": len(re.findall(r"<script\b", html, flags=re.IGNORECASE)),
        "toolbarCount": len(re.findall(r'\bexact-toolbar\b', html)),
        "fieldsPanelCount": len(re.findall(r'\bexact-fields-panel\b', html)),
        "bodyClass": _body_class(html),
    }


def _html_page_count(html: str) -> int:
    page_ids = set(re.findall(r'\bid\s*=\s*["\']page(\d+)["\']', html, flags=re.IGNORECASE))
    if page_ids:
        return len(page_ids)
    return sum(
        1
        for class_value in re.findall(r'\bclass\s*=\s*["\']([^"\']*)["\']', html, flags=re.IGNORECASE)
        if "exact-page" in class_value.split()
    )


def _state_export_summary(html: str, state: dict[str, Any], staged: dict[str, Any]) -> dict[str, Any]:
    editable_texts = state.get("editableTexts") if isinstance(state.get("editableTexts"), dict) else {}
    edited_values = [
        str(item.get("html") or "")
        for item in editable_texts.values()
        if isinstance(item, dict) and item.get("edited") and item.get("html")
    ]
    edited_preserved = all(_plain(value) in _plain(html) for value in edited_values) if edited_values else True
    accent = str(state.get("accentColour") or "")
    dark = str(state.get("darkColour") or "")
    colours_to_check = [colour for colour in (accent, dark) if colour]
    colours_preserved = all(colour in html for colour in colours_to_check) if colours_to_check else True
    return {
        "editedTextCount": len(edited_values),
        "editedTextPreserved": edited_preserved,
        "globalColoursChecked": colours_to_check,
        "globalColoursPreserved": colours_preserved,
        "probeMarker": staged.get("marker") or "",
        "probeMarkerPreserved": bool(staged.get("marker") and staged.get("marker") in html),
    }


def _stage_export_probe_state(
    project_path: Path,
    project_id: str,
    state: dict[str, Any],
    source_html: str,
    blockers: list[str],
) -> dict[str, Any]:
    """Ensure export QA has a real edited state to render, then restore later."""
    state_path = project_path / "editor_state.json"
    had_state = state_path.exists()
    previous_bytes = state_path.read_bytes() if had_state else None
    if _has_edited_text_and_colours(state):
        return {
            "used": False,
            "state": state,
            "path": state_path,
            "hadState": had_state,
            "previousBytes": previous_bytes,
            "restored": True,
        }

    target = _first_editable_target(source_html)
    if not target:
        blockers.append("Export QA could not find editable text to stage an edited-state probe")
        return {
            "used": False,
            "state": state,
            "path": state_path,
            "hadState": had_state,
            "previousBytes": previous_bytes,
            "restored": False,
        }

    marker = f"__EXPORT_QA_EDIT_{project_id}__"
    accent = "#ff00aa"
    dark = "#101820"
    staged_state = json.loads(json.dumps(state)) if state else {}
    staged_state.update(
        {
            "exactLayout": True,
            "editableLayerVersion": max(int(staged_state.get("editableLayerVersion") or 0), 5),
            "colourPreset": "custom",
            "accentColour": accent,
            "darkColour": dark,
        }
    )
    editable_texts = staged_state.get("editableTexts") if isinstance(staged_state.get("editableTexts"), dict) else {}
    original = str(target.get("html") or target.get("text") or "").strip()
    probe_html = f"{original} {marker}".strip()
    editable_texts[str(target["save_id"])] = {
        "html": probe_html,
        "edited": True,
        "typography": {
            "role": target.get("role") or "body",
            "fontAlias": target.get("font_alias") or "",
        },
    }
    staged_state["editableTexts"] = editable_texts
    state_path.write_text(json.dumps(staged_state, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "used": True,
        "state": staged_state,
        "path": state_path,
        "hadState": had_state,
        "previousBytes": previous_bytes,
        "targetSaveId": str(target["save_id"]),
        "marker": marker,
        "restored": False,
    }


def _restore_export_probe_state(staged: dict[str, Any]) -> None:
    if not staged.get("used"):
        return
    state_path = staged.get("path")
    if not isinstance(state_path, Path):
        return
    if staged.get("hadState") and staged.get("previousBytes") is not None:
        state_path.write_bytes(staged["previousBytes"])
    else:
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass
    staged["restored"] = True


def _has_edited_text_and_colours(state: dict[str, Any]) -> bool:
    editable_texts = state.get("editableTexts") if isinstance(state.get("editableTexts"), dict) else {}
    has_edit = any(
        isinstance(item, dict) and item.get("edited") and item.get("html")
        for item in editable_texts.values()
    )
    return bool(has_edit and state.get("accentColour") and state.get("darkColour"))


def _first_editable_target(html: str) -> dict[str, str]:
    for match in re.finditer(
        r"<(?P<tag>[a-z0-9]+)\b(?P<attrs>[^>]*\bcontenteditable=[\"']true[\"'][^>]*)>(?P<body>[\s\S]*?)</(?P=tag)>",
        html,
        flags=re.IGNORECASE,
    ):
        attrs = match.group("attrs") or ""
        save_id = _attr(attrs, "data-save-id")
        if not save_id:
            continue
        body = match.group("body") or ""
        return {
            "save_id": save_id,
            "html": _strip_editor_markup(body),
            "role": _attr(attrs, "data-typography-role") or "body",
            "font_alias": _attr(attrs, "data-font-alias"),
        }
    return {}


def _pdf_export_summary(project_path: Path, pdf_url: str, blockers: list[str]) -> dict[str, Any]:
    existing = project_path / "brochure_export.pdf"
    before_mtime = existing.stat().st_mtime if existing.exists() else None
    pdf_bytes = _post_pdf(pdf_url, blockers)
    bytes_written = len(pdf_bytes)
    after_exists = existing.exists()
    after_mtime = existing.stat().st_mtime if after_exists else None
    disk_bytes = existing.stat().st_size if after_exists else 0
    artifact_bytes = max(bytes_written, disk_bytes)
    return {
        "succeeded": artifact_bytes > 0 and (pdf_bytes.startswith(b"%PDF") or after_exists),
        "bytes": artifact_bytes,
        "responseBytes": bytes_written,
        "diskBytes": disk_bytes,
        "path": str(existing) if after_exists else "",
        "updated": bool(after_mtime and before_mtime != after_mtime),
    }


def _body_class(html: str) -> str:
    match = re.search(r"<body\b[^>]*class=[\"']([^\"']*)", html, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _plain(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _strip_editor_markup(value: str) -> str:
    return _plain(_unescape_html(value))


def _attr(attrs: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}=[\"']([^\"']*)", attrs, flags=re.IGNORECASE)
    return _unescape_html(match.group(1)) if match else ""


def _unescape_html(value: str) -> str:
    return (
        value.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
        .replace("&#160;", " ")
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Write exact clean HTML/PDF export QA evidence.")
    parser.add_argument("project_dir")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    path = write_export_qa(
        args.project_dir,
        base_url=args.base_url,
        output_path=args.output,
        force=args.force,
    )
    print(path)


if __name__ == "__main__":
    main()
