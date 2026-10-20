from pathlib import Path
import sys
import unittest
from copy import deepcopy
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from compare_pvf10_adaptation import compatible


class AdaptationProtocolTests(unittest.TestCase):
    def test_matching_seed_and_controls_required_and_missing_zero_aux_is_compatible(self):
        baseline = {'archive_sha256': 'same', 'seed': 2026, 'test_status': 'not read',
                    'architecture': 'convnext_tiny', 'input_size': [224, 224],
                    'pretrained_sha256': 'imagenet',
                    'training_controls': {'epochs': 25, 'patience': 6, 'batch_size': 64,
                                          'backbone_lr': 2e-5, 'head_lr': 3e-4,
                                          'ema_decay': 0., 'augmentation': 'robust'}}
        adapted = deepcopy(baseline)
        adapted['adapted_backbone_sha256'] = 'adapted'
        adapted['training_controls']['binary_loss_weight'] = 0.
        compatible(baseline, adapted, 'same', 2026)
        for key, value in (('augmentation', 'baseline'), ('binary_loss_weight', .5), ('epochs', 50)):
            bad = deepcopy(adapted); bad['training_controls'][key] = value
            with self.assertRaises(ValueError):
                compatible(baseline, bad, 'same', 2026)
        bad = deepcopy(adapted); bad['seed'] = 2027
        with self.assertRaises(ValueError):
            compatible(baseline, bad, 'same', 2026)

if __name__ == '__main__':
    unittest.main()
