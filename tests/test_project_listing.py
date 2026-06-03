import json
import os
import time

import app


def test_project_updated_at_uses_generated_artifacts(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    source = project_dir / "source.pdf"
    brochure = project_dir / "brochure.html"
    source.write_text("pdf", encoding="utf-8")
    brochure.write_text("html", encoding="utf-8")
    old_time = time.time() - 200
    new_time = time.time() - 20
    os.utime(source, (old_time, old_time))
    os.utime(brochure, (new_time, new_time))
    os.utime(project_dir, (old_time, old_time))

    assert app._project_updated_at(project_dir) == brochure.stat().st_mtime


def test_project_updated_at_ignores_incidental_directory_mtime(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    brochure = project_dir / "brochure.html"
    brochure.write_text("html", encoding="utf-8")
    artifact_time = time.time() - 200
    incidental_time = time.time() - 20
    os.utime(brochure, (artifact_time, artifact_time))
    os.utime(project_dir, (incidental_time, incidental_time))

    assert app._project_updated_at(project_dir) == brochure.stat().st_mtime


def test_project_qa_summary_reads_benchmark_report(tmp_path, monkeypatch):
    reports = tmp_path / "evals" / "reports"
    run_dir = reports / "abc123"
    run_dir.mkdir(parents=True)
    (run_dir / "benchmark-run.json").write_text(
        json.dumps({"accepted": True, "scores": {"critic": 99.4}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "EVAL_REPORTS_DIR", reports)

    assert app._load_project_qa_summary("abc123") == {
        "qa_status": "passed",
        "qa_accepted": True,
        "qa_score": 99.4,
    }
