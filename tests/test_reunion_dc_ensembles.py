from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from compare_reunion_dc_ensembles import mean_scores,inner_plan

class DcEnsembleTests(unittest.TestCase):
    def test_fixed_equal_weight_scores(self):
        np.testing.assert_allclose(mean_scores([[.1,.8],[.3,.6]]),[.2,.7])
        for p in ([],[[float('nan')]],[[1.1]],[[.1],[.2,.3]]):
            with self.assertRaises(ValueError):mean_scores(p)

    def test_inner_constraints_are_not_fabricated_when_infeasible(self):
        y=np.array([0,0,1,1]);score=np.array([.9,.1,.8,.95])
        result=inner_plan(y,score)
        self.assertIsNone(result['constrained'])
        self.assertTrue(0<=result['threshold']<=1)
        feasible=inner_plan(y,np.array([.1,.2,.8,.9]))
        self.assertEqual(feasible['constrained']['fault_recall'],1.)

if __name__=='__main__':unittest.main()
