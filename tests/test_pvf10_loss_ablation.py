from pathlib import Path
import sys
import unittest
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from report_pvf10_loss_ablation import check_pair

class LossAblationTests(unittest.TestCase):
    def test_only_class_weight_can_differ(self):
        a={'archive_sha256':'data','seed':2026,'test_status':'not read','architecture':'convnext_tiny',
           'input_size':[224,224],'pretrained_sha256':'weights','training_controls':{
               'epochs':25,'patience':6,'batch_size':64,'backbone_lr':2e-5,'head_lr':3e-4,
               'ema_decay':0.,'augmentation':'robust'}}
        b=deepcopy(a);b['training_controls']['class_weight_power']=0
        check_pair(a,b,'data',2026)
        for key,val in [('augmentation','baseline'),('class_weight_power',.5),('epochs',30)]:
            bad=deepcopy(b);bad['training_controls'][key]=val
            with self.assertRaises(ValueError):check_pair(a,bad,'data',2026)
        bad=deepcopy(b);bad['adapted_backbone_sha256']='ssl'
        with self.assertRaises(ValueError):check_pair(a,bad,'data',2026)

    def test_mixup_pair_rejects_other_loss_changes(self):
        a={'archive_sha256':'data','seed':2026,'test_status':'not read','architecture':'convnext_tiny',
           'input_size':[224,224],'pretrained_sha256':'weights','training_controls':{
               'epochs':25,'patience':6,'batch_size':64,'backbone_lr':2e-5,'head_lr':3e-4,
               'ema_decay':0.,'augmentation':'robust'}}
        b=deepcopy(a);b['training_controls']['mixup_alpha']=.2
        check_pair(a,b,'data',2026,'mixup')
        for key,val in [('class_weight_power',0.),('binary_loss_weight',.5),('mixup_alpha',.4)]:
            bad=deepcopy(b);bad['training_controls'][key]=val
            with self.assertRaises(ValueError):check_pair(a,bad,'data',2026,'mixup')

if __name__=='__main__':unittest.main()
