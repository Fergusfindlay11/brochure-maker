"""Assess whether extracted PDF text was reconstructed into editable concepts."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any


def build_text_reconstruction_report(project_dir: str | Path, *, html_path: str | Path | None = None) -> dict[str, Any]:
    """Compare semantic PDF-derived contact fields with rendered HTML blocks.

    DOM text recall alone is not enough for brochures: a phone number can exist
    as separate absolute PDF fragments and still be unusable/edit visually badly.
    This assessor checks that contact details are reconstructed as one semantic
    editable block per person.
    """
    project_path = Path(project_dir).expanduser().resolve()
    html_file = Path(html_path).expanduser().resolve() if html_path else project_path / "brochure.html"
    metadata = _load_json(project_path / "exact_metadata.json")
    field_config = metadata.get("layout", {}).get("field_config", {}) if isinstance(metadata, dict) else {}
    contacts = [
        contact
        for contact in field_config.get("contacts") or []
        if isinstance(contact, dict) and not _looks_like_footer_contact(contact)
    ]
    rendered = _extract_rendered_text_blocks(html_file.read_text(encoding="utf-8", errors="replace") if html_file.exists() else "")
    pages: dict[int, dict[str, Any]] = {}
    all_defects: list[dict[str, Any]] = []
    for contact in contacts:
        page_number = int(((contact.get("anchor") or {}) if isinstance(contact.get("anchor"), dict) else {}).get("page_num") or 0)
        page = pages.setdefault(
            page_number,
            {
                "schema": "brochure-maker.page-text-reconstruction.v1",
                "page_number": page_number,
                "expected_contacts": [],
                "rendered_contact_blocks": [],
                "defects": [],
                "accepted": True,
            },
        )
        expected = _expected_contact(contact)
        page["expected_contacts"].append(expected)
        target_id = (contact.get("targets") or [""])[0]
        block = rendered.get(target_id, {})
        page["rendered_contact_blocks"].append(
            {
                "save_id": target_id,
                "text": block.get("text") or "",
                "semantic": bool(block.get("semantic")),
                "hidden_targets": contact.get("hide_targets") or [],
            }
        )
        defects = _contact_defects(page_number, expected, block)
        if defects:
            page["defects"].extend(defects)
            all_defects.extend(defects)

    page_reports = []
    for page_number in sorted(pages):
        page = pages[page_number]
        page["accepted"] = not any(defect.get("severity") in {"blocker", "major"} for defect in page["defects"])
        page_reports.append(page)
    return {
        "schema": "brochure-maker.text-reconstruction.v1",
        "project_id": project_path.name,
        "accepted": not any(defect.get("severity") in {"blocker", "major"} for defect in all_defects),
        "pages": page_reports,
        "blockers": [
            defect["issue"]
            for defect in all_defects
            if defect.get("severity") in {"blocker", "major"}
        ],
        "defects": all_defects,
        "method": {
            "contacts": "name/phone/email must appear together in the target semantic editable block",
            "failure_classes": ["typography/text-reconstruction", "contacts/grouping", "layout-overlap"],
        },
    }


def _expected_contact(contact: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": contact.get("key"),
        "name": str(contact.get("name") or contact.get("label") or "").strip(),
        "phone": str(contact.get("phone") or "").strip(),
        "email": str(contact.get("email") or "").strip(),
        "target": (contact.get("targets") or [""])[0],
    }


def _contact_defects(page_number: int, expected: dict[str, Any], block: dict[str, Any]) -> list[dict[str, Any]]:
    defects: list[dict[str, Any]] = []
    text = str(block.get("text") or "")
    normal = _normalise_text(text)
    phone_digits = _digits(expected.get("phone") or "")
    rendered_digits = _digits(text)
    email = str(expected.get("email") or "").lower().replace(" ", "")
    name = _normalise_text(str(expected.get("name") or ""))
    target = str(expected.get("target") or "")
    if name and name not in normal:
        defects.append(_defect(page_number, "Contact name is missing from semantic editable block", target, "contacts/grouping"))
    if phone_digits and phone_digits not in rendered_digits:
        defects.append(
            _defect(
                page_number,
                "Contact phone digits are missing from the semantic editable block",
                target,
                "contacts/grouping",
                evidence=f"expected={expected.get('phone')} rendered={text[:120]}",
            )
        )
    if email and email not in _normalise_email_text(text):
        defects.append(
            _defect(
                page_number,
                "Contact email is missing from the semantic editable block",
                target,
                "contacts/grouping",
                evidence=f"expected={expected.get('email')} rendered={text[:120]}",
            )
        )
    if (phone_digits or email) and not bool(block.get("semantic")):
        defects.append(
            _defect(
                page_number,
                "Contact details are still raw PDF fragments rather than one semantic editable block",
                target,
                "typography/text-reconstruction",
                severity="major",
            )
        )
    return defects


def _defect(
    page_number: int,
    issue: str,
    target: str,
    subsystem: str,
    *,
    severity: str = "major",
    evidence: str | None = None,
) -> dict[str, Any]:
    return {
        "page": page_number,
        "severity": severity,
        "issue": issue,
        "evidence": evidence or f"target={target}",
        "likely_cause": "PDF text spans were not reconstructed into a semantic contact group before rendering/export",
        "repair_subsystem": subsystem,
        "recommended_tool": "PDF text spans plus Browser screenshot and DOM bounding boxes",
        "failure_class": "contacts/grouping",
    }


def _extract_rendered_text_blocks(html_value: str) -> dict[str, dict[str, Any]]:
    blocks: dict[str, dict[str, Any]] = {}
    pattern = re.compile(
        r'(<p\b(?=[^>]*\bdata-save-id="([^"]+)")[^>]*>)(.*?)(</p>)',
        flags=re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html_value):
        tag, save_id, inner, _close = match.groups()
        blocks[save_id] = {
            "save_id": save_id,
            "semantic": "data-contact-semantic-block=" in tag,
            "text": _html_to_text(inner),
        }
    return blocks


def _html_to_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    return re.sub(r"[ \t\r\f\v]+", " ", value).strip()


def _looks_like_footer_contact(contact: dict[str, Any]) -> bool:
    compact = _compact(" ".join(str(contact.get(key) or "") for key in ("name", "email", "phone", "value")))
    return any(token in compact for token in ("designedandproduced", "designandproduction", "cre8te", "stuartchapmandesign"))


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _normalise_email_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
