"""Small boundary checks for the read-only audit, without real media or models."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("audit", Path(__file__).resolve().parents[1]/"tools/audit_step1.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditBoundaries(unittest.TestCase):
    def test_nonfinite_and_length(self):
        with tempfile.TemporaryDirectory(dir=str(audit.ROOT/"阶段4_音频AI")) as directory:
            path = Path(directory)/"x.npy"
            np.save(path, np.array([[1.0, np.nan], [np.inf, -2.0]]))
            before = audit.sha(path)
            result = audit.npy_audit(path)
            self.assertEqual(result["nonfinite_values"], 2)
            self.assertEqual(result["scanned_values"], 4)
            self.assertTrue(result["length_ok"])
            self.assertEqual(before, audit.sha(path))
            with path.open("ab") as stream:
                stream.write(b"trailing")
            self.assertFalse(audit.npy_audit(path)["length_ok"])

    def test_pickle_refused(self):
        with tempfile.TemporaryDirectory(dir=str(audit.ROOT/"阶段4_音频AI")) as directory:
            path = Path(directory)/"object.npy"
            np.save(path, np.array([{"not": "numeric"}], dtype=object))
            with self.assertRaises(ValueError):
                audit.npy_audit(path)

    def test_labels_and_source_isolation(self):
        with tempfile.TemporaryDirectory(dir=str(audit.ROOT/"阶段4_音频AI")) as directory:
            path = Path(directory)/"whistle_y_base_test.npy"
            np.save(path, np.array([[0.0], [1.0], [0.0]]))
            self.assertEqual(audit.npy_audit(path)["label_counts"], {"0.0": 2, "1.0": 1})
        valid = audit.bh.annotation_row("test.wav", 0, 0, {"start": 1, "end": 10}, 10, 10, "hash")
        invalid = audit.bh.annotation_row("test.wav", 1, 0, {"start": 1, "end": 11}, 10, 10, "hash")
        self.assertTrue(valid["valid"])
        self.assertEqual(valid["end_seconds"], 1)
        self.assertFalse(invalid["valid"])
        self.assertIsNone(invalid["end_seconds"])
        self.assertEqual(invalid["raw_label"]["end"], 11)


if __name__ == "__main__":
    unittest.main()
