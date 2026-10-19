import unittest

import numpy as np

from train_reunion_time_features import add_time_features


class ReunionTimeFeatureTests(unittest.TestCase):
    def test_utc_timestamp_crosses_local_midnight_without_phase_jump(self):
        # 20:01 UTC is 00:01 on the following day in La Reunion.
        end = np.datetime64("2022-01-01T20:01:00", "ns").astype(np.int64)
        x = np.zeros((1, 14, 3), dtype=np.float32)
        augmented = add_time_features(x, np.array([end], dtype=np.int64))
        expected_minutes = np.array([1439, 0, 1])
        angle = 2 * np.pi * expected_minutes / 1440
        self.assertEqual(augmented.shape, (1, 16, 3))
        np.testing.assert_allclose(augmented[0, 14], np.sin(angle), atol=1e-7)
        np.testing.assert_allclose(augmented[0, 15], np.cos(angle), atol=1e-7)


if __name__ == "__main__":
    unittest.main()
