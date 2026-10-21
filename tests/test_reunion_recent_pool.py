import sys
from pathlib import Path
import unittest
import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lstm_model import RNNPV
from train_reunion import make_model


class RecentPoolTests(unittest.TestCase):
    def test_only_readout_pool_and_statistics_use_last_five(self):
        model=RNNPV(channels=8,recent_steps=5).eval()
        seen={}
        def stat_hook(module,args):seen['stats']=args[0].detach().clone()
        def attention_hook(module,args):seen['attention_length']=args[0].shape[1]
        model.statistics[0].register_forward_pre_hook(stat_hook)
        model.attention.register_forward_pre_hook(attention_hook)
        x=torch.randn(4,8,15,requires_grad=True)
        out=model(x);out.square().mean().backward()
        recent=x.detach()[:,:,-5:]
        expected=torch.cat((recent.mean(-1),recent.std(-1,correction=0),recent[:,:,-1]),1)
        torch.testing.assert_close(seen['stats'],expected)
        self.assertEqual(seen['attention_length'],5)
        self.assertEqual(out.shape,(4,2))
        self.assertTrue(torch.isfinite(x.grad).all())
        self.assertGreater(float(x.grad[:,:,:-5].abs().sum()),0) # RNN retains earlier context.

    def test_same_parameter_count_and_explicit_architecture_contract(self):
        torch.manual_seed(2);base=make_model('rnn',8)
        torch.manual_seed(2);recent=make_model('rnn_recent',8)
        self.assertEqual(sum(p.numel() for p in base.parameters()),sum(p.numel() for p in recent.parameters()))
        for k,v in base.state_dict().items():torch.testing.assert_close(v,recent.state_dict()[k])
        self.assertIsNone(base.recent_steps)
        self.assertEqual(recent.recent_steps,5)
        with self.assertRaises(ValueError):RNNPV(recent_steps=0)

if __name__=='__main__':unittest.main()
