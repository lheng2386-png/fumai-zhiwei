from pathlib import Path
import sys
import unittest
import numpy as np
import torch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from evaluate_reunion_sensor_drift import apply_sensor_gain
from reunion_sensor_augmentation import SensorGainAugmentation


class SensorDriftTests(unittest.TestCase):
    def test_gain_is_in_physical_units_and_ratios_recomputed_consistently(self):
        raw = np.ones((2, 14, 30), dtype=np.float32)
        raw[:, 0] = 400; raw[:, 6] = 500; raw[:, 1] = 4; raw[:, 7] = 5
        raw[:, 12] = raw[:, 0] / (raw[:, 6] + 50)
        raw[:, 13] = raw[:, 1] / (raw[:, 7] + .2)
        mean = np.arange(14, dtype=np.float32)
        scale = np.arange(1, 15, dtype=np.float32)
        x = (raw - mean[None, :, None]) / scale[None, :, None]
        original = x.copy(); scaler = {'mean': mean, 'scale': scale}
        changed = apply_sensor_gain(x, scaler, 'control_power_gain_105')
        restored = changed * scale[None, :, None] + mean[None, :, None]
        np.testing.assert_allclose(restored[:, 6], 525, atol=1e-4)
        np.testing.assert_allclose(restored[:, 12], 400 / 575, atol=1e-6)
        np.testing.assert_allclose(restored[:, 13], raw[:, 13], atol=1e-6)
        np.testing.assert_array_equal(x, original)
        np.testing.assert_array_equal(apply_sensor_gain(x, scaler, 'clean'), original)
        current = apply_sensor_gain(x, scaler, 'control_dc_current_gain_105') * scale[None, :, None] + mean[None, :, None]
        np.testing.assert_allclose(current[:, 13], 4 / (5 * 1.05 + .2), atol=1e-6)

    def test_training_transform_matches_physical_gain_and_preserves_input(self):
        ids = (0, 1, 3, 6, 7, 9, 12, 13)
        raw = np.ones((2, 14, 30), dtype=np.float32)
        raw[:, 0] = 400; raw[:, 6] = 500; raw[:, 1] = 4; raw[:, 7] = 5
        raw[:, 12] = 400 / 550; raw[:, 13] = 4 / 5.2
        mean, scale = np.arange(14, dtype=np.float32), np.arange(1, 15, dtype=np.float32)
        x = (raw - mean[None, :, None]) / scale[None, :, None]
        transform = SensorGainAugmentation(mean, scale, ids)
        batch = torch.from_numpy(x[:, ids].copy())
        source = batch.clone()
        gains = torch.ones(2, 4, 1); gains[:, 2] = 1.05
        actual = transform(batch, gains).numpy()
        expected = apply_sensor_gain(x, {'mean': mean, 'scale': scale}, 'control_power_gain_105')[:, ids]
        np.testing.assert_allclose(actual, expected, atol=1e-6)
        torch.testing.assert_close(batch, source)
        with self.assertRaises(ValueError):
            SensorGainAugmentation(mean, scale, (12, 13))

    def test_invalid_inputs_never_produce_scores(self):
        x = np.ones((1, 14, 30), dtype=np.float32)
        for condition, scale in [('unknown', np.ones(14)), ('clean', np.zeros(14))]:
            with self.assertRaises(ValueError):
                apply_sensor_gain(x, {'mean': np.zeros(14), 'scale': scale}, condition)
        x[0, 0, 0] = np.nan
        with self.assertRaises(ValueError):
            apply_sensor_gain(x, {'mean': np.zeros(14), 'scale': np.ones(14)}, 'clean')

if __name__ == '__main__':
    unittest.main()
