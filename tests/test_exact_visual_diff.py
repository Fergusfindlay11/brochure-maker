from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from brochure_maker.exact_visual_diff import (
    attach_visual_diff_to_browser_qa,
    compare_images,
    _render_clean_export_page,
    _run_chrome_screenshot,
)


class TestExactVisualDiff(unittest.TestCase):
    def test_compare_images_scores_identical_images_as_100(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            original = base / "original.png"
            generated = base / "generated.png"
            diff = base / "diff.png"
            Image.new("RGB", (12, 10), "#ffea00").save(original)
            Image.new("RGB", (12, 10), "#ffea00").save(generated)

            score, mae = compare_images(original, generated, diff)

            self.assertEqual(score, 100)
            self.assertEqual(mae, 0)
            self.assertTrue(diff.exists())

    def test_compare_images_crops_blank_frame_margin_before_scoring(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            original = base / "original.png"
            generated = base / "generated.png"
            diff = base / "diff.png"
            Image.new("RGB", (10, 8), "#008ec2").save(original)
            wide = Image.new("RGB", (20, 8), "#ffffff")
            wide.paste(Image.new("RGB", (10, 8), "#008ec2"), (0, 0))
            wide.save(generated)

            score, mae = compare_images(original, generated, diff)

            self.assertEqual(score, 100)
            self.assertEqual(mae, 0)

    def test_attach_visual_diff_to_browser_qa(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "project"
            project_dir.mkdir()
            (project_dir / "browser_qa.json").write_text(
                json.dumps({"editor": {"pageCount": 1}}),
                encoding="utf-8",
            )
            visual = Path(temp_dir) / "visual-diff.json"
            visual.write_text(
                json.dumps(
                    {
                        "score": 97.5,
                        "accepted": True,
                        "method": "test",
                        "blockers": [],
                        "pages": [{"page_number": 1, "score": 97.5, "diff": "/tmp/diff.png"}],
                    }
                ),
                encoding="utf-8",
            )

            qa_path = attach_visual_diff_to_browser_qa(project_dir, visual)

            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertEqual(qa["visual_diff"]["score"], 97.5)
            self.assertEqual(qa["visual_diff"]["pages"][0]["page_number"], 1)

    def test_render_clean_export_page_retries_headless_chrome_timeout(self):
        clean_html = """
        <html><body><main class="exact-pages">
          <div class="exact-page-frame" data-page-num="1"><section class="exact-page" id="page1"></section></div>
        </main></body></html>
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "page.png"

            with mock.patch("brochure_maker.exact_visual_diff.CHROME_CLI", "/Applications/Fake Chrome"), mock.patch(
                "brochure_maker.exact_visual_diff._run_chrome_screenshot",
                side_effect=[
                    {"ok": False, "message": "timed out after 90s"},
                    {"ok": True, "message": "screenshot written"},
                ],
            ) as screenshot:
                _render_clean_export_page(
                    clean_html,
                    1,
                    output,
                    page_width=320,
                    page_height=240,
                    base_url="http://127.0.0.1:8000",
                )

            self.assertEqual(screenshot.call_count, 2)
            calls = [call.args[0] for call in screenshot.call_args_list]
            self.assertIn("--headless=new", calls[0])
            self.assertIn("--headless", calls[1])
            self.assertTrue(any(str(item).startswith("--user-data-dir=") for item in calls[1]))

    def test_render_clean_export_page_accepts_screenshot_written_before_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "page.png"

            class FakePopen:
                returncode = None

                def __init__(self, cmd, **kwargs):
                    self.cmd = cmd
                    self.terminated = False
                    self.killed = False

                def poll(self):
                    return None

                def terminate(self):
                    self.terminated = True
                    self.returncode = -15

                def kill(self):
                    self.killed = True
                    self.returncode = -9

                def wait(self, timeout=None):
                    return self.returncode

                def communicate(self, timeout=None):
                    return "", ""

            def fake_popen(cmd, **kwargs):
                screenshot_arg = next(item for item in cmd if str(item).startswith("--screenshot="))
                Path(str(screenshot_arg).split("=", 1)[1]).write_bytes(b"png")
                return FakePopen(cmd, **kwargs)

            with mock.patch("subprocess.Popen", side_effect=fake_popen) as popen:
                result = _run_chrome_screenshot(["chrome", "--screenshot=" + str(output)], output, timeout_seconds=1)

            self.assertTrue(output.exists())
            self.assertTrue(result["ok"])
            self.assertEqual(popen.call_count, 1)
