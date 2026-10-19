import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from benchmark_gpvs_modes import CHANNELS, load_windows, subtract_healthy_reference


class GPVSProtocolTests(unittest.TestCase):
    def test_middle_third_excluded_and_reference_uses_only_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "source.zip"
            with ZipFile(archive, "w") as bundle:
                for fault in range(8):
                    for mode in "LM":
                        time = np.arange(900, dtype=float) / 100
                        values = np.ones((900, len(CHANNELS)))
                        if fault:
                            values[300:600] = 1000  # Must be discarded, not used as a baseline.
                            values[600:] = 2
                        frame = pd.DataFrame(values, columns=CHANNELS)
                        frame.insert(0, "Time", time)
                        bundle.writestr(f"CSV_Files/F{fault}{mode}.csv", frame.to_csv(index=False))
            x, y, modes, files, prefix, audit = load_windows(archive, .2)
            self.assertEqual(set(modes), {"L", "M"})
            self.assertTrue(np.all(y[files == "F0L"] == 0))
            self.assertGreaterEqual(audit["F1L"]["excluded"], 15)
            self.assertTrue(np.all(y[(files == "F1L") & prefix] == 0))
            self.assertTrue(np.all(y[(files == "F1L") & ~prefix] == 1))
            adjusted = subtract_healthy_reference(x, files, prefix)
            self.assertTrue(np.allclose(adjusted[(files == "F1L") & prefix], 0))
            self.assertGreater(float(adjusted[(files == "F1L") & ~prefix, 0].mean()), .5)


if __name__ == "__main__":
    unittest.main()
