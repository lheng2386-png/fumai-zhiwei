from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from compare_pv_hsd_inference_sizes import compatible

class InferenceSizesTests(unittest.TestCase):
    def test_only_size_can_differ(self):
        a={'checkpoint_sha256':'model','annotation_sha256':'labels','image_sha256':{'1':'pixel'},
           'image_ids':[1],'confidence_threshold':.001,'final_nms_iou':.5,'mode':'tiled',
           'inference_device':'mps','nms_device':'cpu','test_status':'not read','imgsz':640}
        b=deepcopy(a);b['imgsz']=960
        compatible(a,b)
        for key,val in [('checkpoint_sha256','other'),('image_sha256',{'1':'other'}),
                        ('confidence_threshold',.25),('inference_device','cuda'),('imgsz',800)]:
            bad=deepcopy(b);bad[key]=val
            with self.assertRaises(ValueError):compatible(a,bad)
        bad=deepcopy(b);del bad['nms_device']
        with self.assertRaises(ValueError):compatible(a,bad)

if __name__=='__main__':unittest.main()
