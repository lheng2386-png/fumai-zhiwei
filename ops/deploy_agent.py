#!/usr/bin/env python3
"""Deploy an exact public Git commit; preserve data/config; roll back code on health failure.

Bootstrap the service user, nginx include and environment separately (docs/INDUSTRIAL_AGENT.md).
Run backend tests before switching. The existing frontend deployer performs its own health rollback.
"""
import argparse
import fcntl
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath


ROOT = Path('/srv/fumai-zhiwei/agent')


def link(target, path):
    if path.exists() and not path.is_symlink():
        raise ValueError('Refusing to replace a real directory')
    temporary = path.with_name(path.name+'.next')
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, path)


def health(commit):
    for attempt in range(15):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8091/agent-api/health',timeout=3) as response:
                result=json.load(response)
            if result['status']=='ok' and result['release']==commit:
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(1)
    raise RuntimeError('Agent health or release check failed')


def deploy(commit):
    if not re.fullmatch('[0-9a-f]{40}',commit):
        raise ValueError('A full lowercase Git commit SHA is required')
    releases=ROOT/'releases'; releases.mkdir(parents=True,exist_ok=True,mode=0o755)
    current=ROOT/'current'
    with (ROOT/'.deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        old=current.resolve() if current.is_symlink() else None
        if old and old.parent != releases.resolve():
            raise ValueError('Current agent is outside the release directory')
        frontend_root=Path('/srv/fumai-zhiwei/frontend')
        frontend_current=frontend_root/'current'
        old_frontend=frontend_current.resolve() if frontend_current.is_symlink() else None
        if old_frontend and old_frontend.parent != (frontend_root/'releases').resolve():
            raise ValueError('Current frontend is outside the release directory')
        release=releases/commit
        if not release.exists():
            with urllib.request.urlopen(f'https://codeload.github.com/lheng2386-png/fumai-zhiwei/tar.gz/{commit}',timeout=30) as response:
                payload=response.read(24*1024*1024+1)
            if len(payload)>24*1024*1024:
                raise ValueError('Archive too large')
            stage=Path(tempfile.mkdtemp(prefix='.agent-',dir=releases))
            try:
                total=0; seen=set()
                with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
                    for member in archive:
                        parts=PurePosixPath(member.name).parts
                        if len(parts)<3 or parts[1]!='agent' or member.isdir():
                            continue
                        relative=PurePosixPath(*parts[1:])
                        if relative.name=='.gitignore':
                            continue
                        if not member.isfile() or '..' in relative.parts or relative.is_absolute() or str(relative) in seen:
                            raise ValueError('Unsafe agent archive entry')
                        seen.add(str(relative)); total+=member.size
                        if member.size<0 or total>4*1024*1024 or relative.suffix not in {'.py','.json','.md'}:
                            raise ValueError('Unsupported agent archive content')
                        target=stage.joinpath(*relative.parts);target.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
                        with archive.extractfile(member) as src,target.open('xb') as dst:
                            shutil.copyfileobj(src,dst)
                        target.chmod(0o644)
                for required in ('core.py','server.py','store.py','model.py','business_snapshot.py','demo-snapshot.json'):
                    if not (stage/'agent'/required).is_file():
                        raise ValueError('Required agent file missing')
                subprocess.run(['/usr/bin/python3','-m','unittest','discover','-s','agent/tests','-v'],cwd=stage,check=True,timeout=60)
                (stage/'.agent-release').write_text(commit+'\n');stage.chmod(0o755);stage.rename(release)
            finally:
                if stage.exists() and stage.parent.resolve()==releases.resolve() and stage.name.startswith('.agent-'):
                    shutil.rmtree(stage)
        link(release,current)
        try:
            subprocess.run(['systemctl','restart','fumai-agent'],check=True,timeout=20)
            health(commit)
            subprocess.run(['/usr/bin/python3','/srv/fumai-zhiwei/ops/deploy_frontend.py','--commit',commit],check=True,timeout=120)
            for asset in ('agent.js','agent.css','intent.js'):
                with urllib.request.urlopen('http://127.0.0.1/'+asset,timeout=5) as response:
                    if response.status!=200 or not response.read(64):
                        raise RuntimeError('Agent UI asset unavailable')
        except Exception:
            if old_frontend:
                link(old_frontend,frontend_current)
            if old:
                link(old,current);subprocess.run(['systemctl','restart','fumai-agent'],check=False,timeout=20)
            else:
                subprocess.run(['systemctl','stop','fumai-agent'],check=False,timeout=20)
            raise
        if old and old!=release:
            link(old,ROOT/'previous')
        result={'status':'deployed','component':'agent_and_frontend','commit':commit,'previous_agent':old.name if old else None}
        with (ROOT/'deployment-history.jsonl').open('a') as log:
            log.write(json.dumps(result)+'\n')
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--commit',required=True)
    print(json.dumps(deploy(parser.parse_args().commit),indent=2))
