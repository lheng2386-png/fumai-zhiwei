import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from finetune_pv_hsd_resolution import training_controls

class ResolutionTests(unittest.TestCase):
    def test_same_optimizer_budget_and_augmentation_except_resolution(self):
        a,b=training_controls(640,20,2026),training_controls(960,20,2026)
        self.assertEqual(a.pop('imgsz'),640);self.assertEqual(b.pop('imgsz'),960)
        self.assertEqual(a,b)
        self.assertEqual(a['epochs'],a['patience'])
        self.assertEqual((a['mosaic'],a['mixup']),(0,0))
        for size,epochs in [(800,20),(640,0)]:
            with self.assertRaises(ValueError):training_controls(size,epochs,2026)

if __name__=='__main__':unittest.main()
