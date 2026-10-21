"""Create a portable trusted weight directory without committing binary weights."""
import argparse
import json
from pathlib import Path
import shutil
from competition_infer import verified_file,sha256


def package(bundle_path,root,output):
    if output.exists():raise FileExistsError(output)
    bundle=json.loads(bundle_path.read_text())
    files=[]
    for task,spec in bundle['models'].items():
        for key in ('checkpoint','normalization'):
            if key not in spec:continue
            source=verified_file(root,spec[key])
            suffix='.npz' if key=='normalization' else '.pt'
            target=Path('weights')/(task+'_'+key+suffix)
            files.append((source,target,spec[key]['sha256']))
            spec[key]['path']=target.as_posix()
    # Validate all sources before creating any deliverable.
    output.mkdir(parents=True)
    for source,target,digest in files:
        destination=output/target;destination.parent.mkdir(exist_ok=True)
        shutil.copy2(source,destination)
        if sha256(destination)!=digest:raise ValueError('Copied weight integrity check failed')
    config=output/'competition_models_v1.json'
    config.write_text(json.dumps(bundle,indent=2)+'\n')
    (output/'README.txt').write_text('Trusted competition model files. Keep the project source repository separately.\n'
        'Run competition_infer.py with --bundle <this-dir>/competition_models_v1.json --root <this-dir>.\n'
        'Check SHA-256 via config; no files should be downloaded on first inference.\n'
        'Detection model uses trusted local Ultralytics pickle; do not replace it with unknown files.\n')
    print(json.dumps({'output':str(output),'artifacts':len(files),'config_sha256':sha256(config)}))
    return config

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--bundle',type=Path,default=Path('configs/competition_models_v1.json'))
    p.add_argument('--root',type=Path,default=Path('.'))
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();package(args.bundle,args.root,args.output)
