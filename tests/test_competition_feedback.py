import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from competition_feedback import save,history,job_result

class CompetitionFeedbackTests(unittest.TestCase):
    def test_append_only_and_original_prediction_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);identifier='a'*32;folder=root/identifier;folder.mkdir()
            path=folder/'result.json';path.write_text(json.dumps({'schema_version':'competition-inference-v1','evidence':[{'decision':'anomaly'}]}))
            original=path.read_bytes()
            save(root,identifier,'needs_investigation','reviewer','Check thermal frame')
            save(root,identifier,'expected_operation','reviewer','Inspection found expected operation')
            rows=history(root,identifier)
            self.assertEqual([r['status'] for r in rows],['needs_investigation','expected_operation'])
            self.assertTrue(all(r['matches_current_report'] for r in rows))
            self.assertEqual(path.read_bytes(),original)
            path.write_text(json.dumps({'evidence':[{'decision':'normal'}]}))
            self.assertTrue(all(not r['matches_current_report'] for r in history(root,identifier)))

    def test_unknown_path_incomplete_job_and_empty_note_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for identifier in ('../escape','bad','a'*32):
                with self.assertRaises((ValueError,FileNotFoundError)):job_result(root,identifier)
            folder=root/('a'*32);folder.mkdir();(folder/'error.json').write_text('{}')
            with self.assertRaises(FileNotFoundError):save(root,'a'*32,'data_issue','a','reason')
            with self.assertRaises(ValueError):save(root,'a'*32,'data_issue','a',' ')

if __name__=='__main__':unittest.main()
