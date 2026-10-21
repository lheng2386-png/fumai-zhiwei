import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ensemble_pv_hsd_scales import merge_image

class ScaleEnsembleTests(unittest.TestCase):
    def test_duplicate_suppressed_and_complementary_box_kept(self):
        a={'image_id':1,'category_id':0,'bbox':[10,10,10,10],'score':.8}
        b={**a,'score':.9};c={**a,'bbox':[50,50,10,10],'score':.7}
        self.assertEqual(merge_image([a,b,c]),[b,c]);self.assertEqual(a['score'],.8)

    def test_stable_ties_and_invalid_scores(self):
        a={'image_id':1,'category_id':0,'bbox':[10,10,10,10],'score':.8,'origin':'640'}
        b={**a,'origin':'960'}
        self.assertEqual(merge_image([a,b]),[a]);self.assertEqual(merge_image([]),[])
        for score in [float('nan'),-.1,1.1]:
            with self.assertRaises(ValueError):merge_image([{**a,'score':score}])

if __name__=='__main__':unittest.main()
