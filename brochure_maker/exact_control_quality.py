"""Semantic quality checks for generated exact global controls."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_GLOBAL_CONTROL_GROUPS = (
    "palette",
    "typography",
    "logo",
    "amenity_icons",
    "images",
    "map",
    "agents",
)


def write_control_quality_report(
    project_dir: str | Path,
    output_path: str | Path,
) -> Path:
    report = build_control_quality_report(project_dir)
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return path


def build_control_quality_report(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir).expanduser().resolve()
    graph = _load_json(project_path / "brochure.design.json")
    html = _read_text(project_path / "brochure.html")
    if not graph and not html:
        return {
            "schema": "brochure-maker.control-quality.v1",
            "project_id": project_path.name,
            "accepted": True,
            "method": "not-applicable-non-exact-fixture",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "blockers": [],
            "findings": [],
        }

    findings: list[dict[str, Any]] = []
    findings.extend(_global_control_findings(graph))
    findings.extend(_typography_findings(graph))
    findings.extend(_field_quality_findings(html))
    findings.extend(_replaceable_source_findings(graph))
    blockers = [
        str(finding.get("issue") or "Control quality finding")
        for finding in findings
        if finding.get("severity") in {"blocker", "major"}
    ]
    return {
        "schema": "brochure-maker.control-quality.v1",
        "project_id": project_path.name,
        "accepted": not blockers,
        "method": "design-graph-and-fields-panel-audit",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "blockers": sorted(dict.fromkeys(blockers)),
        "findings": findings,
    }


def _global_control_findings(graph: dict[str, Any]) -> list[dict[str, Any]]:
    tokens = graph.get("theme_tokens") if isinstance(graph.get("theme_tokens"), dict) else {}
    controls = tokens.get("global_controls") if isinstance(tokens.get("global_controls"), dict) else {}
    missing = [group for group in EXPECTED_GLOBAL_CONTROL_GROUPS if not controls.get(group)]
    if not missing:
        return []
    return [
        {
            "severity": "blocker",
            "issue": f"Missing global control groups: {', '.join(missing)}",
            "repair_subsystem": "design-graph/global-controls",
            "evidence": f"global_controls keys={sorted(controls.keys())}",
        }
    ]


def _typography_findings(graph: dict[str, Any]) -> list[dict[str, Any]]:
    tokens = graph.get("theme_tokens") if isinstance(graph.get("theme_tokens"), dict) else {}
    typography = tokens.get("typography") if isinstance(tokens.get("typography"), dict) else {}
    roles = typography.get("editor_roles") if isinstance(typography.get("editor_roles"), dict) else {}
    findings: list[dict[str, Any]] = []
    if not roles:
        return [
            {
                "severity": "blocker",
                "issue": "Missing global typography roles",
                "repair_subsystem": "design-graph/typography",
                "evidence": "theme_tokens.typography.editor_roles is empty",
            }
        ]
    for role, config in roles.items():
        if not isinstance(config, dict):
            continue
        required = ["cssVar", "fontFamily"]
        for style_key in ("fontSize", "lineHeight", "fontWeight", "letterSpacing"):
            if _role_has_pdf_typography_evidence(config, style_key):
                required.append(style_key)
        missing = [key for key in required if not str(config.get(key) or "").strip()]
        if missing:
            findings.append(
                {
                    "severity": "major",
                    "issue": f"Typography role {role} is missing {', '.join(missing)}",
                    "repair_subsystem": "design-graph/typography",
                    "evidence": config,
                }
            )
    return findings


def _role_has_pdf_typography_evidence(config: dict[str, Any], style_key: str) -> bool:
    """Only require richer typography controls when extraction evidence named them."""
    evidence = config.get("sourceStyleEvidence")
    if isinstance(evidence, dict) and evidence.get(style_key):
        return True
    for container_name in ("pdfEvidence", "observedStyle", "sourceStyle"):
        container = config.get(container_name)
        if isinstance(container, dict) and style_key in container:
            return True
    return False


def _field_quality_findings(html: str) -> list[dict[str, Any]]:
    if not html:
        return []
    fields = _field_rows(html)
    findings: list[dict[str, Any]] = []
    target_owner: dict[str, str] = {}
    for field in fields:
        field_id = field.get("field") or field.get("label") or "unknown"
        value = str(field.get("value") or "").strip()
        section = str(field.get("section") or "")
        if section == "amenities" and _looks_like_noisy_amenity(value):
            findings.append(
                {
                    "severity": "major",
                    "issue": f"Noisy amenity/global field label: {value}",
                    "repair_subsystem": "controls/semantic-field-extraction",
                    "evidence": field,
                }
            )
        targets = [target.strip() for target in str(field.get("targets") or "").split(",") if target.strip()]
        if not targets:
            continue
        for target in targets:
            previous = target_owner.get(target)
            if previous and previous != field_id:
                findings.append(
                    {
                        "severity": "major",
                        "issue": f"Duplicate global control target: {target}",
                        "repair_subsystem": "controls/target-mapping",
                        "evidence": {"target": target, "fields": [previous, field_id]},
                    }
                )
            else:
                target_owner[target] = field_id
    return findings


def _field_rows(html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    section = ""
    for match in re.finditer(r'<section\b[^>]*data-field-section=["\']([^"\']+)["\'][\s\S]*?</section>', html, flags=re.IGNORECASE):
        section = match.group(1)
        block = match.group(0)
        for field_match in re.finditer(
            r'<(?P<tag>input|textarea)\b(?P<attrs>[^>]*)>(?P<body>[\s\S]*?</textarea>)?',
            block,
            flags=re.IGNORECASE,
        ):
            attrs = field_match.group("attrs") or ""
            if "data-exact-field" not in attrs:
                continue
            tag = field_match.group("tag").lower()
            field = _attr(attrs, "data-exact-field")
            targets = _attr(attrs, "data-targets")
            if tag == "textarea":
                body = field_match.group("body") or ""
                value = re.sub(r"</textarea>\s*$", "", body, flags=re.IGNORECASE)
            else:
                value = _attr(attrs, "value")
            label = _nearest_label(block, field_match.start())
            rows.append(
                {
                    "section": section,
                    "field": field,
                    "label": label,
                    "value": _unescape_html(value).strip(),
                    "targets": targets,
                }
            )
    return rows


def _looks_like_noisy_amenity(value: str) -> bool:
    compact = re.sub(r"[^A-Za-z0-9]+", "", value).upper()
    if not compact:
        return False
    if len(compact) <= 3:
        return True
    if re.fullmatch(r"[A-Z]{1,4}(?:\s+[A-Z]{1,4}){0,2}", value.strip()) and len(compact) <= 8:
        return True
    road_fragments = {"RD", "ST", "NORTH", "SOUTH", "EAST", "WEST", "OVER", "ITH", "FLY"}
    return compact in road_fragments


def _replaceable_source_findings(graph: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for page in graph.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_number = page.get("page_number")
        for element in page.get("elements") or []:
            if not isinstance(element, dict) or not element.get("replaceable"):
                continue
            if not element.get("source_attribution"):
                findings.append(
                    {
                        "severity": "major",
                        "issue": f"Replaceable element lacks source attribution on page {page_number}",
                        "repair_subsystem": "design-graph/source-attribution",
                        "evidence": {
                            "page": page_number,
                            "id": element.get("id"),
                            "role": element.get("role"),
                        },
                    }
                )
    return findings


def _attr(attrs: str, name: str) -> str:
    match = re.search(rf'\b{re.escape(name)}=["\']([^"\']*)', attrs, flags=re.IGNORECASE)
    return _unescape_html(match.group(1)) if match else ""


def _nearest_label(block: str, offset: int) -> str:
    before = block[:offset]
    matches = list(re.finditer(r"<label\b[^>]*>([\s\S]*?)</label>", before, flags=re.IGNORECASE))
    if not matches:
        return ""
    return _unescape_html(re.sub(r"<[^>]+>", "", matches[-1].group(1))).strip()


def _unescape_html(value: str) -> str:
    return (
        value.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
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
    parser = argparse.ArgumentParser(description="Audit exact global control semantic quality.")
    parser.add_argument("project_dir")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    output = args.output or str(Path(args.project_dir) / "control_quality.json")
    path = write_control_quality_report(args.project_dir, output)
    print(path)


if __name__ == "__main__":
    main()
