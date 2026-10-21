"""Recompute paired COCO scores from immutable same-checkpoint inference caches."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from audit_pv_hsd_predictions import diagnose, xyxy


def compatible(a,b):
    for key in ('checkpoint_sha256','annotation_sha256','image_sha256','image_ids',
                'confidence_threshold','final_nms_iou','mode','inference_device','nms_device','test_status'):
        if key not in a or key not in b or a[key]!=b[key]:
            raise ValueError(f'Unmatched inference comparison: {key}')
    if a['test_status']!='not read' or a['imgsz']!=640 or b['imgsz']!=960:
        raise ValueError('Expected validation-only 640/960 pair')


def run(args):
    if args.output.exists():raise FileExistsError(args.output)
    raw=args.annotations.read_bytes(); annotations=json.loads(raw)
    files={'640':args.reference,'960':args.candidate}
    caches={k:json.loads(v.read_text()) for k,v in files.items()}
    compatible(caches['640'],caches['960'])
    ids={image['id'] for image in annotations['images']}
    cats={c['id'] for c in annotations['categories']}
    if len(cats)!=1:raise ValueError('Expected one defect class')
    for cache in caches.values():
        if (cache['annotation_sha256']!=hashlib.sha256(raw).hexdigest()
                or sorted(cache['image_ids'])!=sorted(ids)
                or set(cache['image_sha256'])!={str(i) for i in ids}):
            raise ValueError('Cached validation identity mismatch')
        for p in cache['predictions']:
            if (p['image_id'] not in ids or p['category_id'] not in cats
                    or not math.isfinite(p['score']) or not 0<=p['score']<=1):
                raise ValueError('Invalid prediction image/category/score')
            xyxy(p['bbox'])
    gt={i:[] for i in ids}
    for a in annotations['annotations']:
        if a.get('iscrowd',0) or a.get('ignore',0):raise ValueError('Unsupported crowd/ignore annotation')
        gt[a['image_id']].append(a)
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    coco=COCO(str(args.annotations))
    result={'protocol':'Same checkpoint and image hashes, only inference size 640 vs 960',
            'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files.values()},
            'annotation_sha256':hashlib.sha256(raw).hexdigest(),'sizes':{},
            'limitations':['Repeated validation, no new site or flight.',
                           'Larger input interpolates source pixels, not higher sensor resolution.',
                           'No model/threshold selected on this comparison.',
                           'Empty annotation images are not certified healthy.',
                           'Fixed-IoU greedy diagnostic is separate from COCO AP/AR.']}
    diagnostics={}
    for size,cache in caches.items():
        detected=coco.loadRes(cache['predictions'])
        evaluation=COCOeval(coco,detected,'bbox')
        evaluation.evaluate();evaluation.accumulate();evaluation.summarize()
        by_image={i:[] for i in ids}
        for p in cache['predictions']:by_image[p['image_id']].append(p)
        points={}
        for conf in (.25,.5):
            if cache['confidence_threshold']>conf:raise ValueError('Cache missing required scores')
            for iou in (.5,.75):
                rows={i:diagnose(gt[i],by_image[i],conf,iou) for i in ids}
                key=f'conf{conf}_iou{iou}';diagnostics[(size,key)]=rows
                points[key]={name:sum(r[name] for r in rows.values())
                             for name in ('true_positive','false_positive','false_negative')}
                points[key]['empty_label_images_with_detection']=sum(not gt[i] and rows[i]['false_positive']>0 for i in ids)
        result['sizes'][size]={'map50_95':float(evaluation.stats[0]),'map50':float(evaluation.stats[1]),
                               'map75':float(evaluation.stats[2]),'recall_100':float(evaluation.stats[8]),
                               'fixed_points':points}
    result['paired_miss_transitions']={}
    for key in result['sizes']['640']['fixed_points']:
        a={v for r in diagnostics[('640',key)].values() for v in r['missed_annotation_ids']}
        b={v for r in diagnostics[('960',key)].values() for v in r['missed_annotation_ids']}
        result['paired_miss_transitions'][key]={'missed_both':len(a&b),'recovered_at_960':len(a-b),
                                               'newly_missed_at_960':len(b-a)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['sizes'],indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--annotations',type=Path,required=True)
    p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())
