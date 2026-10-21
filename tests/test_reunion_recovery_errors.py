import sys
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from audit_reunion_recovery_errors import recovery_groups, describe


class RecoveryAuditTests(unittest.TestCase):
    def test_context_boundary_and_day_reset(self):
        start=pd.Timestamp('2022-02-14 10:00',tz='Indian/Reunion').value
        t=start+np.array([0,5,10,15,1440])*60_000_000_000
        y=np.array([1,0,0,0,0])
        recent,elapsed=recovery_groups(y,t,15)
        np.testing.assert_array_equal(recent,[False,True,True,False,False])
        self.assertTrue(np.isnan(elapsed[-1]))

    def test_false_positives_are_not_relabelled_or_hidden(self):
        y=np.array([1,0,0,0]);t=np.arange(4,dtype=np.int64)*300_000_000_000
        r=describe(y,np.array([.9,.9,.8,.7]),t,.5,10)
        self.assertEqual(r['all_normal_false_alarms'],3)
        self.assertEqual(r['normal_with_sampled_fault_in_context']['alarms'],1)
        self.assertEqual(r['other_normal_endpoints']['alarms'],2)
        self.assertEqual(len(r['false_alert_rows']),3)

    def test_future_fault_cannot_explain_prior_false_positive(self):
        recent,_=recovery_groups(np.array([0,0,1]),np.arange(3)*300_000_000_000,30)
        self.assertFalse(recent.any())
        with self.assertRaises(ValueError):recovery_groups([0,1],[10,5],15)

if __name__=='__main__':unittest.main()
