from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from competition_evidence import assess


def record(task,decision='anomaly',**kwargs):
    return {'evidence_id':task,'plant_id':'p1','asset_id':'a1','observed_at':'2026-10-21T10:00:00Z',
            'task':task,'observation_id':task,'model_version':'research-v1','threshold_version':'fixed-v1',
            'quality':'ready','decision':decision,'data_kind':'synthetic_demo',**kwargs}


def check(rows):return assess(rows,'p1','a1','2026-10-21T10:10:00Z')

class CompetitionEvidenceTests(unittest.TestCase):
    def test_corroboration_needs_two_modalities_and_distinct_observations(self):
        self.assertEqual(check([record('timeseries'),record('classification')])['status'],'corroborated_review')
        self.assertEqual(check([record('classification'),record('detection')])['status'],'single_source_review')
        self.assertEqual(check([record('timeseries',observation_id='same'),record('classification',observation_id='same')])['status'],'single_source_review')

    def test_wrong_asset_future_stale_and_bad_quality_cannot_support(self):
        rows=[record('timeseries'),record('classification',asset_id='other'),
              record('detection',observed_at='2026-10-21T10:11:00Z')]
        result=check(rows)
        self.assertEqual(result['status'],'single_source_review')
        self.assertEqual({r['reason'] for r in result['excluded']},{'different_asset','future_observation'})
        self.assertEqual(check([record('classification',observed_at='2026-10-21T09:00:00Z')])['status'],'insufficient_evidence')
        self.assertEqual(check([record('timeseries',quality='blocked',quality_reason='missing')])['status'],'insufficient_evidence')

    def test_conflict_requires_review_and_unknown_is_not_normal(self):
        self.assertEqual(check([record('timeseries'),record('classification','normal')])['status'],'conflict_review')
        self.assertEqual(check([])['status'],'insufficient_evidence')
        self.assertEqual(check([record('timeseries','uncertain')])['status'],'insufficient_evidence')
        self.assertEqual(check([record('timeseries','normal')])['status'],'normal_observed')
        self.assertEqual(check([record('timeseries','normal'),record('classification','uncertain')])['status'],'insufficient_evidence')

    def test_old_task_evidence_cannot_outvote_new_result(self):
        rows=[record('timeseries',evidence_id='old',observed_at='2026-10-21T09:55:00Z'),
              record('timeseries','normal',evidence_id='new'),record('classification','normal')]
        result=check(rows)
        self.assertEqual(result['status'],'normal_observed')
        self.assertIn({'evidence_id':'old','reason':'superseded_same_task'},result['excluded'])
        with self.assertRaises(ValueError):check([record('timeseries',evidence_id='a'),record('timeseries',evidence_id='b')])

    def test_policy_is_order_invariant_and_does_not_modify_inputs(self):
        rows=[record('timeseries'),record('classification')];original=deepcopy(rows)
        self.assertEqual(check(rows),check(rows[::-1]));self.assertEqual(rows,original)
        self.assertIsNone(check(rows)['automatic_control_action'])
        self.assertTrue(check(rows)['demo_only'])
        with self.assertRaises(ValueError):check([record('timeseries',data_kind='unknown')])
        with self.assertRaises(ValueError):check([record('timeseries',score=float('nan'))])
        with self.assertRaises(ValueError):check([record('timeseries',observed_at='2026-10-21 10:00')])
        with self.assertRaises(ValueError):check([record('timeseries'),record('timeseries')])

    def test_pair_window_cannot_bridge_unrelated_times(self):
        rows=[record('timeseries',observed_at='2026-10-21T09:45:00Z'),record('classification')]
        result=assess(rows,'p1','a1','2026-10-21T10:10:00Z',30,10)
        self.assertEqual(result['status'],'single_source_review')
        self.assertIn({'evidence_id':'timeseries','reason':'outside_pair_time_window'},result['excluded'])

if __name__=='__main__':unittest.main()
