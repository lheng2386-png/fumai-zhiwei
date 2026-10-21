import sys
from pathlib import Path
import unittest
import torch
from torch import nn
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from train_reunion import make_model
from lstm_model import RNNPV

class GruTests(unittest.TestCase):
    def test_gate_parameters_checkpoint_roundtrip_and_full_history_gradient(self):
        model=make_model('gru',8).eval()
        self.assertIsInstance(model.encoder,nn.GRU)
        self.assertTrue(model.encoder.bidirectional)
        for steps in (15,30):
            x=torch.randn(3,8,steps,requires_grad=True)
            score=model(x)
            self.assertEqual(score.shape,(3,2));score.square().mean().backward()
            self.assertTrue(torch.isfinite(x.grad).all())
            self.assertGreater(x.grad[:,:,:-5].abs().sum().item(),0)
        clone=make_model('gru',8).eval();clone.load_state_dict(model.state_dict(),strict=True)
        torch.testing.assert_close(model(x),clone(x))
        with self.assertRaises(RuntimeError):make_model('rnn',8).load_state_dict(model.state_dict(),strict=True)

    def test_rnn_default_encoder_keeps_original_rng_initialization(self):
        torch.manual_seed(2031)
        model=RNNPV(channels=8)
        torch.manual_seed(2031)
        projection=nn.Linear(8,64)
        encoder=nn.RNN(64,64,num_layers=2,batch_first=True,bidirectional=True,nonlinearity='tanh',dropout=.1)
        torch.testing.assert_close(model.input.weight,projection.weight)
        for k,v in encoder.state_dict().items():torch.testing.assert_close(v,model.encoder.state_dict()[k])
        with self.assertRaises(ValueError):RNNPV(cell_type='unknown')

if __name__=='__main__':unittest.main()
