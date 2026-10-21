"""Fixed operating-point detection diagnostics, not an alternative COCO AP metric."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from evaluate_pv_hsd_tiled import iou_with


def xyxy(box):
    if len(box) != 4 or not np.isfinite(box).all() or box[2] <= 0 or box[3] <= 0:
        raise ValueError('Expected finite positive COCO xywh box')
    x,y,w,h = box
    return [x,y,x+w,y+h]


def diagnose(annotations, predictions, confidence, match_iou):
    """Score ordered greedy, one-to-one matches at a fixed IoU and confidence."""
    ground = np.asarray([xyxy(a['bbox']) for a in annotations]).reshape(-1,4)
    selected = sorted([p for p in predictions if p['score'] >= confidence], key=lambda p:-p['score'])
    matched = set(); errors = {'duplicate':0, 'localization':0, 'background':0}
    for p in selected:
        box = np.asarray(xyxy(p['bbox']))
        overlaps = iou_with(box,ground) if len(ground) else np.zeros(0)
        eligible = [i for i in range(len(ground)) if i not in matched and overlaps[i] >= match_iou]
        if eligible:
            matched.add(max(eligible,key=lambda i:overlaps[i]))
        elif len(overlaps) and overlaps.max() >= match_iou:
            errors['duplicate'] += 1
        elif len(overlaps) and overlaps.max() >= .1:
            errors['localization'] += 1
        else:
            errors['background'] += 1
    missed = [a['id'] for i,a in enumerate(annotations) if i not in matched]
    return {'true_positive':len(matched),'false_positive':sum(errors.values()),
            'false_negative':len(missed),'false_positive_types':errors,'missed_annotation_ids':missed}


def run(args):
    if args.output.exists(): raise FileExistsError(args.output)
    raw = args.annotations.read_bytes(); data = json.loads(raw)
    cache = json.loads(args.predictions.read_text())
    if (cache['annotation_sha256'] != hashlib.sha256(raw).hexdigest()
            or cache['test_status'] != 'not read'
            or sorted(cache['image_ids']) != sorted(i['id'] for i in data['images'])
            or set(cache['image_sha256']) != {str(i['id']) for i in data['images']}):
        raise ValueError('Cache annotation/image identity mismatch')
    ids = {i['id'] for i in data['images']}
    cats = {c['id'] for c in data['categories']}
    if len(cats) != 1: raise ValueError('This diagnostic expects one defect category')
    annotations = {i:[] for i in ids}; predictions = {i:[] for i in ids}
    for a in data['annotations']:
        if a['image_id'] not in ids or a['category_id'] not in cats or a.get('iscrowd',0) or a.get('ignore',0):
            raise ValueError('Unsupported annotation')
        xyxy(a['bbox']); annotations[a['image_id']].append(a)
    for p in cache['predictions']:
        if (p['image_id'] not in ids or p['category_id'] not in cats or not np.isfinite(p['score'])
                or not 0 <= p['score'] <= 1): raise ValueError('Invalid prediction')
        xyxy(p['bbox']); predictions[p['image_id']].append(p)
    result = {'protocol':'Fixed confidence/IoU score-greedy one-to-one diagnostic; not COCO AP',
              'prediction_cache_sha256':hashlib.sha256(args.predictions.read_bytes()).hexdigest(),
              'annotation_sha256':cache['annotation_sha256'],'operating_points':{},
              'limitations':['Validation repeatedly used in development; no new holdout.',
                             'Empty annotation images are not certified healthy.',
                             'Localization/background labels use a fixed IoU .1 diagnostic boundary.',
                             'Greedy matches at fixed IoU are not COCO AP/AR or field operating rates.']}
    for confidence in (.25,.5):
        if cache['confidence_threshold'] > confidence: raise ValueError('Cache omitted necessary low scores')
        for threshold in (.5,.75):
            rows = {str(i):diagnose(annotations[i],predictions[i],confidence,threshold) for i in sorted(ids)}
            counts = {key:sum(row[key] for row in rows.values()) for key in ('true_positive','false_positive','false_negative')}
            tp,fp,fn = (counts[k] for k in ('true_positive','false_positive','false_negative'))
            result['operating_points'][f'conf{confidence}_iou{threshold}'] = {
                **counts,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,
                'empty_label_images':sum(not annotations[i] for i in ids),
                'empty_label_images_with_detection':sum(not annotations[i] and rows[str(i)]['false_positive']>0 for i in ids),
                'false_positive_types':{k:sum(row['false_positive_types'][k] for row in rows.values()) for k in ('duplicate','localization','background')},
                'per_image':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:{a:b for a,b in v.items() if a!='per_image'} for k,v in result['operating_points'].items()},indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--annotations',type=Path,required=True)
    p.add_argument('--predictions',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())
