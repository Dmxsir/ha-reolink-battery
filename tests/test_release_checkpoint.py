from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "custom_components" / "reolink_battery" / "manifest.json"
CURRENT = ROOT / "CHECKPOINT.md"
ARCHIVE_DIR = ROOT / "docs" / "checkpoints"


class ReleaseCheckpointTests(unittest.TestCase):
    def test_checkpoint_matches_manifest_version_and_archive(self) -> None:
        version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
        expected_header = f"**Checkpoint version:** v{version}"
        archive = ARCHIVE_DIR / f"v{version}.md"

        self.assertTrue(CURRENT.is_file(), "root CHECKPOINT.md is required")
        self.assertTrue(
            archive.is_file(),
            f"release checkpoint archive is required: {archive.relative_to(ROOT)}",
        )

        current_text = CURRENT.read_text(encoding="utf-8")
        archive_text = archive.read_text(encoding="utf-8")

        self.assertIn(
            expected_header,
            current_text,
            "CHECKPOINT.md must declare the manifest version",
        )
        self.assertEqual(
            current_text,
            archive_text,
            "root CHECKPOINT.md and release archive must be identical",
        )


if __name__ == "__main__":
    unittest.main()
