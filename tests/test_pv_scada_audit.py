import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audit_pv_scada_mendeley import run_lengths


class ScadaAuditTests(unittest.TestCase):
    def test_fault_episodes_do_not_merge_across_normal_rows(self):
        self.assertEqual(run_lengths(["FAULT", "FAULT", "NORMAL", "FAULT",
                                      "NORMAL", "FAULT", "FAULT", "FAULT"],
                                     "FAULT"), [2, 1, 3])


if __name__ == "__main__":
    unittest.main()
