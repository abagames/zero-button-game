from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitignoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.repo = Path(self._temporary_directory.name)
        shutil.copyfile(ROOT / ".gitignore", self.repo / ".gitignore")
        subprocess.run(
            ["git", "init", "-q", str(self.repo)],
            check=True,
            capture_output=True,
            text=True,
        )

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def assert_ignored(self, *paths: str) -> None:
        for relative_path in paths:
            with self.subTest(path=relative_path):
                result = subprocess.run(
                    ["git", "check-ignore", "-v", "--no-index", "--", relative_path],
                    cwd=self.repo,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn(relative_path, result.stdout)

    def assert_trackable(self, *paths: str) -> None:
        for relative_path in paths:
            with self.subTest(path=relative_path):
                result = subprocess.run(
                    ["git", "check-ignore", "-q", "--no-index", "--", relative_path],
                    cwd=self.repo,
                )
                self.assertEqual(1, result.returncode)

    def test_output_tree_caches_and_local_state_are_ignored(self) -> None:
        self.assert_ignored(
            "output/problem.json",
            "output/run/metadata.json",
            "output/run/validation.json",
            "output/run/manifest.jsonl",
            "output/run/animation.gif",
            "output/run/preview.mp4",
            "output/run/contact_sheet.png",
            "output/run/frames/frame_0001.ppm",
            "output/run/maze/.maze-000042-deadbeef-abc123/problem.json",
            "output/run/artifact.with-unrecognized-extension",
            "src/zero_button_game/__pycache__/core.cpython-312.pyc",
            ".pytest_cache/v/cache/nodeids",
            ".mypy_cache/3.12/cache.json",
            ".pytype/imports/default.pyi",
            ".ruff_cache/content",
            ".coverage",
            ".coverage.worker-1",
            "coverage.xml",
            "htmlcov/index.html",
            ".venv/bin/python",
            ".env.local",
            ".claude/.cc-writes/session.lock",
        )

    def test_sources_configuration_and_documents_remain_trackable(self) -> None:
        self.assert_trackable(
            "studies/calibration.sealed.json",
            "presets/current/maze-target.json",
            "presets/shared/standard.json",
            "schemas/metadata.schema.json",
            "src/zero_button_game/pipeline.py",
            "scripts/check_markdown_links.py",
            "tests/test_unit.py",
            "README.md",
            "VISUAL_DESIGN.md",
            "pyproject.toml",
            ".env.example",
        )

if __name__ == "__main__":
    unittest.main()
