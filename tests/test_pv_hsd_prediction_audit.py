import sys
from pathlib import Path
import unittest
import argparse
import hashlib
import json
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from audit_pv_hsd_predictions import diagnose, xyxy, run

class PredictionAuditTests(unittest.TestCase):
    def test_duplicate_localization_and_background_are_distinct(self):
        annotations=[{'id':1,'bbox':[10,10,10,10]}]
        predictions=[{'bbox':b,'score':s} for b,s in [([10,10,10,10],.9),([10,10,10,10],.8),([16,10,10,10],.7),([50,50,10,10],.6)]]
        r=diagnose(annotations,predictions,.25,.5)
        self.assertEqual((r['true_positive'],r['false_positive'],r['false_negative']),(1,3,0))
        self.assertEqual(r['false_positive_types'],{'duplicate':1,'localization':1,'background':1})

    def test_confidence_threshold_preserves_misses_and_empty_images(self):
        a=[{'id':5,'bbox':[0,0,10,10]}]
        p=[{'bbox':[0,0,10,10],'score':.4}]
        self.assertEqual(diagnose(a,p,.5,.5)['missed_annotation_ids'],[5])
        self.assertEqual(diagnose([],p,.25,.5)['false_positive'],1)
        self.assertEqual(diagnose(a,[],.25,.5)['false_negative'],1)

    def test_second_best_unmatched_target_can_match(self):
        a=[{'id':1,'bbox':[0,0,10,10]},{'id':2,'bbox':[2,0,10,10]}]
        p=[{'bbox':[0,0,10,10],'score':.9},{'bbox':[0,0,10,10],'score':.8}]
        self.assertEqual(diagnose(a,p,.25,.5)['true_positive'],2)
        for bad in ([0,0,0,10],[0,0,float('nan'),10]):
            with self.assertRaises(ValueError):xyxy(bad)

    def test_complete_empty_image_coverage_and_cache_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); annotation=root/'annotations.json'; cache=root/'cache.json'
            annotation.write_text(json.dumps({'images':[{'id':1},{'id':2}],
                'categories':[{'id':0}], 'annotations':[{'id':3,'image_id':1,'category_id':0,'bbox':[0,0,10,10]}]}))
            payload={'annotation_sha256':hashlib.sha256(annotation.read_bytes()).hexdigest(),
                     'test_status':'not read','image_ids':[1,2],'image_sha256':{'1':'a','2':'b'},
                     'confidence_threshold':.001,'predictions':[
                         {'image_id':1,'category_id':0,'bbox':[0,0,10,10],'score':.9},
                         {'image_id':2,'category_id':0,'bbox':[0,0,10,10],'score':.8}]}
            cache.write_text(json.dumps(payload))
            args=argparse.Namespace(annotations=annotation,predictions=cache,output=root/'audit.json')
            with patch('builtins.print'):
                report=run(args)
            row=report['operating_points']['conf0.25_iou0.5']
            self.assertEqual((row['true_positive'],row['false_positive'],row['false_negative']),(1,1,0))
            self.assertEqual(row['empty_label_images_with_detection'],1)
            payload['image_ids']=[1];cache.write_text(json.dumps(payload));args.output=root/'bad.json'
            with self.assertRaises(ValueError):run(args)

if __name__=='__main__':unittest.main()
