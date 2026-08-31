import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_markdown_links", ROOT / "scripts" / "check_markdown_links.py"
)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class MarkdownLinkTests(unittest.TestCase):
    def test_repository_markdown_links_are_current(self):
        report = CHECKER.check(ROOT)
        self.assertEqual(report["missing_local_targets"], [])
        self.assertEqual(report["retired_root_links"], [])
        self.assertEqual(report["status"], "passed")

    def test_missing_and_retired_root_targets_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "PUZZLE_GIF_ENVIRONMENT_PLAN.md").write_text("retired\n", encoding="utf-8")
            (root / "README.md").write_text(
                "[missing](missing.md)\n"
                "[retired](PUZZLE_GIF_ENVIRONMENT_PLAN.md)\n",
                encoding="utf-8",
            )
            report = CHECKER.check(root)
            self.assertEqual(len(report["missing_local_targets"]), 1)
            self.assertEqual(len(report["retired_root_links"]), 1)
            self.assertEqual(report["status"], "failed")

    def test_external_anchor_and_code_links_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "[web](https://example.com/a) [section](#local)\n"
                "`[not a link](missing.md)`\n"
                "```md\n[fenced](also-missing.md)\n```\n",
                encoding="utf-8",
            )
            report = CHECKER.check(root)
            self.assertEqual(report["local_links_checked"], 0)
            self.assertEqual(report["status"], "passed")


if __name__ == "__main__":
    unittest.main()
