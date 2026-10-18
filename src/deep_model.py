"""Original compact temporal encoder; see DEEP_LEARNING.md for references."""
import numpy as np
import torch
from torch import nn

BASE_FEATURES=['vdc1','vdc2','idc1','idc2','irr','pvt']
FEATURES=BASE_FEATURES+['pdc1','pdc2','pdc_total','v_ratio','i_ratio','v_diff','i_diff']


def make_features(raw):
    raw=np.asarray(raw,dtype=np.float32)
    if raw.ndim!=2 or raw.shape[1]!=6 or not np.isfinite(raw).all():
        raise ValueError('Expected finite [rows, 6] raw features')
    v1,v2,i1,i2,irr,temp=raw.T
    vr=np.divide(v1,v2,out=np.ones_like(v1),where=np.abs(v2)>1e-6)
    ir=np.divide(i1,i2,out=np.ones_like(i1),where=np.abs(i2)>1e-6)
    return np.column_stack([raw,v1*i1,v2*i2,v1*i1+v2*i2,
        np.clip(vr,0,5),np.clip(ir,0,5),np.abs(v1-v2),np.abs(i1-i2)]).astype(np.float32)


class ResidualBlock(nn.Module):
    def __init__(self,width,dilation):
        super().__init__()
        self.layers=nn.Sequential(nn.Conv1d(width,width,3,padding=dilation,dilation=dilation),
            nn.GroupNorm(4,width),nn.GELU(),nn.Conv1d(width,width,1))

    def forward(self,x):
        return x+self.layers(x)


class TemporalEncoder(nn.Module):
    def __init__(self,channels=13,width=32):
        super().__init__()
        self.encoder=nn.Sequential(nn.Conv1d(channels,width,1),nn.GELU(),
            *[ResidualBlock(width,d) for d in (1,2,4)])
        self.decoder=nn.Conv1d(width,channels,1)
        self.classifier=nn.Sequential(nn.Linear(width*2,width),nn.GELU(),nn.Dropout(.1),nn.Linear(width,5))

    def forward(self,x,reconstruct=False):
        encoded=self.encoder(x)
        if reconstruct:
            return self.decoder(encoded)
        return self.classifier(torch.cat([encoded.mean(dim=-1),encoded[:,:,-1]],dim=1))
