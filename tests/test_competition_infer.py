import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from competition_infer import image_input,verified_file
from package_competition_models import package

class CompetitionInferTests(unittest.TestCase):
    def test_rejects_changed_weight_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);p=root/'model.pt';p.write_bytes(b'pinned')
            spec={'path':'model.pt','sha256':hashlib.sha256(b'pinned').hexdigest()}
            self.assertEqual(verified_file(root,spec),p.resolve())
            p.write_bytes(b'changed')
            with self.assertRaises(ValueError):verified_file(root,spec)

    def test_constant_and_wrong_detector_size_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'image.png'
            Image.new('RGB',(640,512),(150,20,40)).save(p)
            _,quality=image_input(p,(640,512))
            self.assertFalse(quality['ready'])
            values=np.zeros((112,112,3),np.uint8);values[20:80,30:70]=255
            Image.fromarray(values).save(p)
            _,quality=image_input(p);self.assertTrue(quality['ready'])
            self.assertGreater(quality['gradient_proxy'],0.)
            _,quality=image_input(p,(640,512));self.assertFalse(quality['ready'])
            self.assertEqual(quality['reason'],'unsupported_source_image_size')

    def test_package_is_portable_and_integrity_failure_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            p=source/'weights.pt';p.write_bytes(b'fixture')
            spec={'version':'fixture','models':{'classification':{'checkpoint':{
                'path':'weights.pt','sha256':hashlib.sha256(b'fixture').hexdigest()}}}}
            config=source/'config.json';config.write_text(json.dumps(spec))
            result=package(config,source,root/'portable')
            saved=json.loads(result.read_text())['models']['classification']['checkpoint']
            self.assertEqual(verified_file(root/'portable',saved).read_bytes(),b'fixture')
            self.assertEqual(json.loads(config.read_text()),spec)
            p.write_bytes(b'changed')
            with self.assertRaises(ValueError):package(config,source,root/'bad')
            self.assertFalse((root/'bad').exists())

if __name__=='__main__':unittest.main()
