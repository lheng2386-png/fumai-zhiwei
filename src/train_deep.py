"""Train-only masked pretraining, supervised fine tuning and matched baselines."""
import argparse
import copy
import json
from pathlib import Path
import random
import time
import numpy as np
from scipy.io import loadmat
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import accuracy_score,classification_report,confusion_matrix,f1_score
import torch
from torch import nn
from torch.utils.data import DataLoader,TensorDataset
from deep_model import BASE_FEATURES,FEATURES,TemporalEncoder,make_features
from opencem import sha256


def window_indices(length,window=24,stride=24,purge=100):
    """Split raw positions first. No overlapping raw rows across partitions."""
    if window<2 or stride<window or purge<window:
        raise ValueError('Nonoverlapping windows and purge >= window are required')
    a,b=int(length*.75),int(length*.8)
    ranges=[(0,a-purge),(a+purge,b-purge),(b+purge,length)]
    result=[]
    for start,end in ranges:
        starts=np.arange(start,end-window+1,stride,dtype=int)
        if len(starts)<10:
            raise ValueError('Insufficient windows after chronological split')
        result.append(starts[:,None]+np.arange(window))
    return result


def prepare(raw_dir,window=24):
    amb=loadmat(raw_dir/'dataset_amb.mat');elec=loadmat(raw_dir/'dataset_elec.mat')
    raw=np.column_stack([(amb[name] if name in amb else elec[name]).ravel() for name in BASE_FEATURES])
    labels=amb['f_nv'].ravel().astype(int)
    indices=window_indices(len(raw),window,window,max(100,window))
    # Preserve raw positions: invalid rows never move a partition boundary.
    indices=[idx[np.isfinite(raw[idx]).all(axis=(1,2))&np.isin(labels[idx[:,-1]],range(5))] for idx in indices]
    features=make_features(raw[np.isfinite(raw).all(axis=1)]) if np.isfinite(raw).all() else None
    if features is None:
        safe=raw.copy();safe[~np.isfinite(safe)]=0
        # Invalid rows only occupy their original positions; their windows are excluded above.
        features=make_features(safe)
    train=features[indices[0]].reshape(-1,len(FEATURES))
    mean=train.mean(axis=0,dtype=np.float64).astype(np.float32)
    scale=train.std(axis=0,dtype=np.float64).astype(np.float32)
    scale=np.maximum(scale,1e-5)
    x=[np.transpose((features[idx]-mean)/scale,(0,2,1)).copy() for idx in indices]
    y=[labels[idx[:,-1]] for idx in indices]
    if set(y[0])!=set(range(5)):
        raise ValueError('Training partition must contain all five classes')
    return x,y,indices,mean,scale


def seed_all(seed):
    random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)


def loader(x,y=None,shuffle=False):
    arrays=[torch.from_numpy(x)]
    if y is not None:arrays.append(torch.from_numpy(y).long())
    return DataLoader(TensorDataset(*arrays),batch_size=256,shuffle=shuffle,num_workers=0)


@torch.no_grad()
def predict(model,x,device):
    model.eval()
    return np.concatenate([model(batch[0].to(device)).cpu().numpy() for batch in loader(x)])


def pretrain(model,x,device,epochs):
    optimizer=torch.optim.AdamW(model.parameters(),lr=1e-3,weight_decay=1e-4)
    history=[]
    for epoch in range(epochs):
        model.train();total=0.;count=0
        for (batch,) in loader(x,shuffle=True):
            batch=batch.to(device)
            mask=torch.rand_like(batch)<.25
            corrupted=torch.where(mask,torch.zeros_like(batch),batch)+.02*torch.randn_like(batch)
            reconstructed=model(corrupted,reconstruct=True)
            loss=((reconstructed-batch).square()*mask).sum()/mask.sum().clamp(min=1)
            optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1.)
            optimizer.step();total+=loss.item()*len(batch);count+=len(batch)
        history.append(total/count)
        print(json.dumps({'stage':'masked_pretrain','epoch':epoch+1,'masked_mse':history[-1]}),flush=True)
    return history


def finetune(model,x,y,device,epochs,tag):
    counts=np.bincount(y[0],minlength=5)
    weight=1/np.sqrt(np.maximum(counts,1));weight/=weight.mean()
    criterion=nn.CrossEntropyLoss(weight=torch.tensor(weight,dtype=torch.float32,device=device))
    optimizer=torch.optim.AdamW(model.parameters(),lr=1e-3,weight_decay=1e-4)
    best=-1.;state=None;history=[];stale=0
    for epoch in range(epochs):
        model.train();loss_sum=0.;count=0
        for batch,target in loader(x[0],y[0],shuffle=True):
            batch,target=batch.to(device),target.to(device)
            loss=criterion(model(batch),target)
            optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1.)
            optimizer.step();loss_sum+=loss.item()*len(batch);count+=len(batch)
        predicted=predict(model,x[1],device).argmax(axis=1)
        score=float(f1_score(y[1],predicted,labels=range(5),average='macro',zero_division=0))
        row={'stage':tag,'epoch':epoch+1,'train_loss':loss_sum/count,'validation_macro_f1':score}
        history.append(row);print(json.dumps(row),flush=True)
        if score>best:
            best=score;state={k:v.detach().cpu().clone() for k,v in model.state_dict().items()};stale=0
        else:stale+=1
        if stale>=5:break
    model.load_state_dict(state)
    return best,history


def metrics(y,predicted):
    return {'accuracy':float(accuracy_score(y,predicted)),
        'macro_f1':float(f1_score(y,predicted,labels=range(5),average='macro',zero_division=0)),
        'classification_report':classification_report(y,predicted,labels=range(5),output_dict=True,zero_division=0),
        'confusion_matrix':confusion_matrix(y,predicted,labels=range(5)).tolist()}


def run(raw_dir,output,model_dir,device_name,epochs,pretrain_epochs,seed=2026):
    started=time.time();torch.set_num_threads(2);seed_all(seed)
    device=torch.device(device_name)
    x,y,indices,mean,scale=prepare(raw_dir)
    output.mkdir(parents=True,exist_ok=True);model_dir.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(output/'split_endpoints.npz',**{name:idx[:,[0,-1]] for name,idx in zip(['train','validation','test'],indices)})
    results={};validation={};histories={};candidates={}
    tree_x=[np.column_stack([z.mean(axis=-1),z.std(axis=-1),z[:,:,-1]]) for z in x]
    tree=ExtraTreesClassifier(n_estimators=100,max_depth=18,min_samples_leaf=5,
                              class_weight='balanced',random_state=seed,n_jobs=2)
    tree.fit(tree_x[0],y[0])
    validation['extra_trees_matched']=float(f1_score(y[1],tree.predict(tree_x[1]),labels=range(5),average='macro',zero_division=0))
    # Equal initialization for the two neural experiments; only pretraining differs.
    seed_all(seed);initial=copy.deepcopy(TemporalEncoder().state_dict())
    for tag in ['scratch_temporal_cnn','masked_pretrained_temporal_cnn']:
        seed_all(seed);model=TemporalEncoder().to(device);model.load_state_dict(initial)
        if tag.startswith('masked'):
            histories['masked_pretraining']=pretrain(model,x[0],device,pretrain_epochs)
            torch.save({'state_dict':{k:v.cpu() for k,v in model.state_dict().items()}},model_dir/'masked_pretrained.pt')
        seed_all(seed)
        best,history=finetune(model,x,y,device,epochs,tag)
        validation[tag]=best;histories[tag]=history
        # Retain candidates; choose on validation before accessing held-out labels.
        candidates[tag]=copy.deepcopy(model).cpu()
        torch.save({'state_dict':candidates[tag].state_dict(),'mean':torch.from_numpy(mean),
                    'scale':torch.from_numpy(scale),'features':FEATURES,'window':24,'width':32},model_dir/f'{tag}.pt')
    winner=max(validation,key=validation.get)
    results['extra_trees_matched']=metrics(y[2],tree.predict(tree_x[2]))
    for tag,model in candidates.items():
        predicted=predict(model.to(device),x[2],device).argmax(axis=1)
        results[tag]=metrics(y[2],predicted)
    import joblib
    joblib.dump({'model':tree,'mean':mean,'scale':scale,'features':FEATURES},model_dir/'extra_trees_matched.joblib')
    report={'seed':seed,'device':str(device),'torch':torch.__version__,
            'protocol':'raw ordered rows 75/5/20; 100-row boundary purge; nonoverlapping 24-row windows; last-row label; train-only scaling and pretraining; validation-only checkpoint/model selection',
            'split_windows':{name:len(z) for name,z in zip(['train','validation','test'],x)},
            'class_counts':{name:np.bincount(z,minlength=5).tolist() for name,z in zip(['train','validation','test'],y)},
            'validation_macro_f1':validation,'selected_by_validation':winner,
            'test_results':results,'history':histories,'elapsed_seconds':time.time()-started,
            'input_sha256':{name:sha256(raw_dir/name) for name in ['dataset_amb.mat','dataset_elec.mat']},
            'limitations':['Single-plant row-ordered evaluation, no verified independent event/day or cross-plant generalization.',
                           'Raw rows have no timestamps: 24 rows are not asserted to represent a particular duration.',
                           'New protocol differs from earlier 80/20 baseline; compare only the matched baselines here.',
                           'Domain pretraining uses this dataset training partition only, not a general foundation model.']}
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='history'},ensure_ascii=False,indent=2),flush=True)
    return report


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser()
    p.add_argument('--raw-dir',type=Path,default=root/'data/raw/costa2020')
    p.add_argument('--output',type=Path,default=root/'reports/deep')
    p.add_argument('--model-dir',type=Path,default=root/'models/deep')
    p.add_argument('--device',choices=['cpu','mps','cuda'],default='cpu')
    p.add_argument('--seed',type=int,default=2026)
    p.add_argument('--epochs',type=int,default=16)
    p.add_argument('--pretrain-epochs',type=int,default=6)
    a=p.parse_args()
    if a.epochs<1 or a.pretrain_epochs<1:p.error('Epoch counts must be positive')
    run(a.raw_dir,a.output,a.model_dir,a.device,a.epochs,a.pretrain_epochs,a.seed)
