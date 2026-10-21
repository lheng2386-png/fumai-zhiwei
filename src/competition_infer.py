"""Pinned, local competition inference for three research model tasks.

No weight downloads or training. All checkpoints must match a local bundle's
SHA-256. Detection pickle loading is restricted to the user's trusted bundle.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time
import numpy as np
from competition_evidence import validate, utc_time


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def verified_file(root,spec):
    path=(root/spec['path']).resolve()
    if not path.is_file() or sha256(path)!=spec['sha256']:
        raise ValueError(f'Pinned model artifact missing or changed: {spec["path"]}')
    return path


def image_input(path,expected_size=None):
    from PIL import Image,ImageOps
    with Image.open(path) as source:
        image=ImageOps.exif_transpose(source).convert('RGB');image.load()
    values=np.asarray(image,dtype=np.float32)/255.
    if expected_size is not None and image.size!=tuple(expected_size):
        return image,{'ready':False,'reason':'unsupported_source_image_size','size':list(image.size)}
    # A constant crop has no spatial diagnostic information. No tuned blur threshold.
    spatial_std=float(values.std(axis=(0,1),dtype=np.float64).max())
    gradient=float(np.abs(np.diff(values,axis=0)).mean()+np.abs(np.diff(values,axis=1)).mean()) if min(image.size)>1 else 0.
    return image,{'ready':min(image.size)>=2 and spatial_std>1e-6,
                  'reason':'ready' if min(image.size)>=2 and spatial_std>1e-6 else 'constant_or_empty_spatial_information',
                  'size':list(image.size),'spatial_std':spatial_std,'gradient_proxy':gradient,
                  'note':'Gradient proxy is not a calibrated blur detector.'}


def classify(path,checkpoint,spec,device):
    import torch
    from train_pvf10_transfer import build_model,normalize
    image,quality=image_input(path)
    if not quality['ready']:return [{'quality':quality,'decision':'uncertain','details':{}}]
    saved=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if saved['architecture']!='convnext_tiny' or saved['classes']!=spec['classes'] or saved['input_size']!=224:
        raise ValueError('Classification checkpoint contract mismatch')
    model,_,_=build_model('convnext_tiny',len(saved['classes']),pretrained=False)
    model.load_state_dict(saved['state_dict'],strict=True);model.eval().to(device)
    # Training starts from 112x112 public crops; preserve that source processing.
    from PIL import Image
    image=image.resize((112,112),Image.Resampling.BILINEAR)
    x=torch.from_numpy(np.array(image).transpose(2,0,1).copy()).float().div(255).unsqueeze(0).to(device)
    with torch.no_grad():p=model(normalize(x,224)).float().softmax(-1)[0].cpu().numpy()
    index=int(p.argmax());confidence=float(p[index]);label=saved['classes'][index]
    decision='uncertain' if confidence<spec['min_class_score'] else 'normal' if label=='healthy panel' else 'anomaly'
    return [{'quality':quality,'decision':decision,'score':confidence,
             'details':{'predicted_class':label,'class_probabilities':dict(zip(saved['classes'],map(float,p))),
                        'min_class_score':spec['min_class_score'],'source_processing':'RGB -> 112x112 -> model 224'}}]


def detect(path,checkpoint,spec,device):
    from ultralytics import YOLO
    from evaluate_pv_hsd_tiled import inference_views,restore_box,touches_internal_edge,nms
    image,quality=image_input(path,(640,512))
    if not quality['ready']:return [{'quality':quality,'decision':'uncertain','details':{}}]
    if str(device)=='mps':
        raise ValueError('Detection demo supports cpu/cuda; MPS needs explicit CPU NMS adapter')
    model=YOLO(str(checkpoint))
    views,origins=inference_views(image,'tiled')
    results=model.predict(views,device=device,imgsz=640,conf=spec['confidence_threshold'],
                          iou=.7,max_det=300,batch=4,verbose=False)
    if len(results)!=4:raise ValueError('Detector view count mismatch')
    boxes=[];scores=[]
    for origin,result in zip(origins,results):
        for box,score in zip(result.boxes.xyxy.cpu().numpy(),result.boxes.conf.cpu().numpy()):
            if touches_internal_edge(box,origin):continue
            box=restore_box(box,origin)
            if box[2]<=box[0] or box[3]<=box[1]:continue
            boxes.append(box);scores.append(float(score))
    kept=nms(boxes,scores,.5,300)
    detections=[{'bbox_xyxy':boxes[i],'score':scores[i],'class':'hotspot'} for i in kept]
    # Absence of a hotspot box does not establish overall healthy operation.
    return [{'quality':quality,'decision':'anomaly' if detections else 'uncertain',
             **({'score':max(d['score'] for d in detections)} if detections else {}),
             'details':{'detections':detections,'count':len(detections),'coordinates':'original_image_pixels',
                        'confidence_threshold':spec['confidence_threshold'],
                        'empty_detection_meaning':'No accepted hotspot boxes; not a health certificate.'}}]


def timeseries(path,checkpoint,spec,device,root):
    import torch
    import pandas as pd
    from prepare_reunion import FEATURES
    from predict_reunion import make_window_inputs
    from reunion_quality_gate import assess_windows
    from train_reunion import make_model,predict
    normalization=verified_file(root,spec['normalization'])
    with np.load(normalization,allow_pickle=False) as d:mean,scale=d['mean'],d['scale']
    if mean.shape!=(14,) or scale.shape!=(14,) or not np.isfinite(mean).all() or not np.isfinite(scale).all() or (scale<=0).any():
        raise ValueError('Invalid pinned fold normalization')
    frame=pd.read_csv(path)
    # Require explicit derived columns to agree with measured constituents;
    # missing constituents also invalidate their derived ratios.
    for derived,numerator,denominator,offset in [('power_ratio','faulted_Pg','control_Pg',50.),('dc_current_ratio','faulted_Ia','control_Ia',.2)]:
        required={derived,numerator,denominator}
        if not required.issubset(frame.columns):raise ValueError('Missing raw/derived time-series columns')
        a,b,c=(pd.to_numeric(frame[k],errors='coerce').to_numpy(float) for k in (numerator,denominator,derived))
        with np.errstate(divide='ignore',invalid='ignore'):expected=a/(b+offset)
        valid=np.isfinite(a)&np.isfinite(b)&np.isfinite(expected)
        if not np.allclose(c[valid],expected[valid],atol=1e-5,rtol=1e-4,equal_nan=False):
            raise ValueError('Derived ratios disagree with physical inputs')
        frame.loc[~valid,derived]=np.nan
    x,mask,rows=make_window_inputs(frame,mean,scale)
    gate=assess_windows(x,mask)
    saved=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if (saved['architecture']!=spec['architecture'] or saved['channels']!=len(spec['feature_indices'])
            or saved.get('context_minutes')!=spec['context_minutes']):raise ValueError('Time-series contract mismatch')
    model=make_model(saved['architecture'],saved['channels']).to(device)
    model.load_state_dict(saved['state_dict'],strict=True);model.eval()
    accepted=[i for i,row in enumerate(rows) if row['status'] is None and gate[i]=='ready']
    scores={}
    if accepted:
        selected=x[accepted][:,spec['feature_indices'],-spec['context_minutes']:]
        values=predict(model,np.ascontiguousarray(selected),torch.device(device),256)
        scores=dict(zip(accepted,map(float,values)))
    output=[]
    for i,row in enumerate(rows):
        reason=row['status'] or gate[i]
        ready=i in scores
        output.append({'observed_at':row['end_time_utc'],
            'quality':{'ready':ready,'reason':reason,'observed_fraction':row['observed_fraction']},
            'decision':('anomaly' if scores[i]>=spec['threshold'] else 'normal') if ready else 'uncertain',
            **({'score':scores[i]} if ready else {}),
            'details':{'context_minutes':spec['context_minutes'],'threshold':spec['threshold'],
                       'features':[FEATURES[k] for k in spec['feature_indices']],
                       'source_window_minutes':30,'reference_inverter_required':True}})
    return output


def run(args):
    import torch
    torch.set_num_threads(min(torch.get_num_threads(),8))
    if args.output.exists():raise FileExistsError(args.output)
    bundle=json.loads(args.bundle.read_text());spec=bundle['models'][args.task]
    root=args.root.resolve();checkpoint=verified_file(root,spec['checkpoint'])
    if args.task!='timeseries':utc_time(args.observed_at)
    if not args.input.is_file():raise FileNotFoundError(args.input)
    started=time.monotonic();source_hash=sha256(args.input)
    if args.task=='timeseries':results=timeseries(args.input,checkpoint,spec,args.device,root)
    elif args.task=='classification':results=classify(args.input,checkpoint,spec,args.device)
    else:results=detect(args.input,checkpoint,spec,args.device)
    evidence=[]
    for i,item in enumerate(results):
        observed=item.get('observed_at',args.observed_at)
        observation=args.observation_id if len(results)==1 else f'{args.observation_id}:{i}'
        identity=f'{args.task}|{args.plant_id}|{args.asset_id}|{observation}|{observed}|{spec["checkpoint"]["sha256"]}|{source_hash}|{spec["threshold_version"]}'
        record={'evidence_id':hashlib.sha256(identity.encode()).hexdigest()[:24],
            'plant_id':args.plant_id,'asset_id':args.asset_id,'observed_at':observed,'task':args.task,
            'observation_id':observation,'model_version':spec['checkpoint']['sha256'],
            'threshold_version':spec['threshold_version'],'input_sha256':source_hash,'quality':'ready' if item['quality']['ready'] else 'blocked',
            'quality_reason':item['quality']['reason'],'decision':item['decision'],'data_kind':args.data_kind,
            **({'score':item['score']} if 'score' in item else {})}
        validate(record);evidence.append({**record,'quality_details':item['quality'],'model_output':item['details']})
    report={'schema_version':'competition-inference-v1','task':args.task,'bundle_version':bundle['version'],
            'bundle_sha256':sha256(args.bundle),'input_sha256':source_hash,'device':args.device,
            'elapsed_seconds':time.monotonic()-started,'evidence':evidence,
            'observed_time_source':'input_csv' if args.task=='timeseries' else 'caller_supplied_capture_time',
            'note':'Research model output for competition review; scores are not calibrated fault probabilities.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'task':args.task,'rows':len(evidence),'decisions':[r['decision'] for r in evidence]},ensure_ascii=False))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--task',choices=('timeseries','classification','detection'),required=True)
    p.add_argument('--bundle',type=Path,default=Path('configs/competition_models_v1.json'))
    p.add_argument('--root',type=Path,default=Path('.'))
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--plant-id',required=True);p.add_argument('--asset-id',required=True)
    p.add_argument('--observation-id',required=True)
    p.add_argument('--observed-at',help='Actual capture time with timezone, required for images')
    p.add_argument('--data-kind',choices=('public_research','site_observation','synthetic_demo'),required=True)
    p.add_argument('--device',choices=('cpu','mps','cuda'),default='cpu')
    args=p.parse_args()
    if args.task!='timeseries' and args.observed_at is None:p.error('Image evidence requires --observed-at')
    run(args)
