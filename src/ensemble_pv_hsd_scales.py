"""Fixed same-checkpoint 640+960 union/NMS; no fitting or confidence averaging."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from audit_pv_hsd_predictions import xyxy, run as audit
from compare_pv_hsd_inference_sizes import compatible
from evaluate_pv_hsd_tiled import nms


def merge_image(predictions):
    boxes=[];scores=[]
    for p in predictions:
        boxes.append(xyxy(p['bbox']));score=p['score']
        if not np.isfinite(score) or not 0<=score<=1:raise ValueError('Invalid score')
        scores.append(score)
    return [dict(predictions[i]) for i in nms(boxes,scores,.5,300)]


def run(args):
    if args.output.exists():raise FileExistsError(args.output)
    a,b=(json.loads(p.read_text()) for p in (args.reference,args.candidate))
    compatible(a,b)
    raw=args.annotations.read_bytes();data=json.loads(raw)
    ids={image['id'] for image in data['images']};cats={c['id'] for c in data['categories']}
    if (a['annotation_sha256']!=hashlib.sha256(raw).hexdigest() or len(cats)!=1
            or sorted(ids)!=sorted(a['image_ids']) or set(a['image_sha256'])!={str(i) for i in ids}):
        raise ValueError('Annotation/image identity mismatch')
    by_image={i:[] for i in ids}
    for cache in (a,b):
        for p in cache['predictions']:
            if p['image_id'] not in ids or p['category_id'] not in cats:raise ValueError('Unknown image/category')
            by_image[p['image_id']].append(p)
    predictions=[p for i in sorted(ids) for p in merge_image(by_image[i])]
    if not predictions:raise ValueError('No merged detections')
    cache={**a,'mode':'tiled_multiscale_union_nms','imgsz':[640,960],
           'protocol':'Fixed 640+960 union, NMS .5, retain survivor scores; stable ties prefer 640',
           'predictions':predictions,'source_cache_sha256':{
               str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.reference,args.candidate)}}
    # Persist exact merged predictions before computing either metric family.
    args.output.mkdir(parents=True)
    (args.output/'predictions.json').write_text(json.dumps(cache)+'\n')
    diagnostic=audit(argparse.Namespace(annotations=args.annotations,predictions=args.output/'predictions.json',output=args.output/'fixed_points.json'))
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    coco=COCO(str(args.annotations));detected=coco.loadRes(predictions)
    evaluation=COCOeval(coco,detected,'bbox')
    evaluation.evaluate();evaluation.accumulate();evaluation.summarize()
    report={'protocol':cache['protocol'],'source_cache_sha256':cache['source_cache_sha256'],
            'map50_95':float(evaluation.stats[0]),'map50':float(evaluation.stats[1]),
            'map75':float(evaluation.stats[2]),'recall_100':float(evaluation.stats[8]),
            'fixed_points':{k:{a:b for a,b in v.items() if a!='per_image'} for k,v in diagnostic['operating_points'].items()},
            'test_status':'not read','limitations':[
                'Repeated validation; not a new site/flight evaluation.',
                'Two strongly correlated scales of one checkpoint; scores are not calibrated.',
                'Requires both inference passes; cache merge runtime is not model latency.',
                'No threshold/model selected on this result; empty labels are not certified healthy.']}
    (args.output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));return report

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('annotations','reference','candidate','output'):p.add_argument('--'+name,type=Path,required=True)
    run(p.parse_args())
