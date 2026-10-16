import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("sources", Path(__file__).resolve().parents[1]/"tools/prepare_step2.py")
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


def family(fid, assignment="development_only"):
    return dict(family_id=fid, leakage_block="same_unresolved_recording", active_assignment=assignment)


class SourceBoundaries(unittest.TestCase):
    def test_same_origin_cannot_cross_assignments(self):
        with self.assertRaises(ValueError):
            sources.validate_families([family("clip1", "train"), family("clip2", "test")], [])
        sources.validate_families([family("clip1"), family("clip2")], [dict(family_ids=["clip1", "clip2"])])

    def test_unknown_family_and_duplicate_refused(self):
        with self.assertRaises(ValueError):
            sources.validate_families([family("one")], [dict(family_ids=["missing"])])
        with self.assertRaises(ValueError):
            sources.validate_families([family("one"), family("one")], [])

    def test_identical_media_cannot_hide_in_distinct_blocks(self):
        a, b = family("one"), family("two")
        b["leakage_block"] = "another_recording"
        assets = [dict(family_ids=[fid], role="original_media", sha256="same_bytes") for fid in ("one", "two")]
        with self.assertRaises(ValueError):
            sources.validate_families([a, b], assets)

    def test_output_reuse_and_escape_refused(self):
        with tempfile.TemporaryDirectory(dir=str(sources.STAGE)) as directory:
            with self.assertRaises(ValueError):
                sources.build(Path(directory))
        with self.assertRaises(ValueError):
            sources.build(sources.ROOT/"not-stage4")


if __name__ == "__main__":
    unittest.main()
