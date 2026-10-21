from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from finetune_pv_hsd_resolution import training_controls
from resume_pv_hsd_resolution import recovery_action

class ResolutionResumeTests(unittest.TestCase):
    def test_requires_matching_epoch_optimizer_and_controls(self):
        expected=training_controls(960,20,2026)
        c={'epoch':1,'optimizer':{'state':{}},'train_args':deepcopy(expected)}
        self.assertEqual(recovery_action(c,2,expected),'resume_training')
        for changed in ({'epoch':0},{'optimizer':None},{'train_args':{**expected,'imgsz':640}}):
            with self.assertRaises(ValueError):recovery_action({**c,**changed},2,expected)

    def test_finished_stripped_checkpoint_is_evaluate_only(self):
        expected=training_controls(960,20,2026)
        c={'epoch':-1,'optimizer':None,'train_args':expected}
        self.assertEqual(recovery_action(c,20,expected),'evaluate_only')
        with self.assertRaises(ValueError):recovery_action(c,19,expected)
        with self.assertRaises(ValueError):recovery_action(c,21,expected)

if __name__=='__main__':unittest.main()
