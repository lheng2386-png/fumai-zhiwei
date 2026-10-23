#!/usr/bin/env python3
"""Deploy pinned model-ingestion backend; preserve frontend, Agent, model and SMTP configuration."""
from pathlib import Path, PurePosixPath
import argparse, fcntl, io, json, os, re, shlex, shutil, sqlite3, subprocess, tarfile, time, urllib.request


def link(target,path):
    temporary=path.with_name(path.name+'.monitor-next')
    if temporary.is_symlink():temporary.unlink()
    temporary.symlink_to(target);os.replace(temporary,path)


def deploy(commit,expected):
    assert re.fullmatch('[0-9a-f]{40}',commit) and re.fullmatch('[0-9a-f]{40}',expected)
    root=Path('/srv/fumai-zhiwei/notifications');current=root/'current';old=current.resolve()
    assert old.name==expected,'Current notification release changed; inspect before deploying'
    with (root/'.deploy-monitoring.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        site=Path('/etc/nginx/sites-enabled/fumai-zhiwei');oldsite=site.read_bytes()
        assert b'# BEGIN FUMAI MONITORING' not in oldsite,'Frontend route changes must already be absent'
        envfile=Path('/etc/fumai-notifications.env');oldenv=envfile.read_bytes();config={}
        for line in oldenv.decode().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1);config[key]=' '.join(shlex.split(value))
        backup=Path('/var/backups/fumai-notifications')/('monitoring-backend-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()));backup.mkdir(parents=True,mode=0o700)
        (backup/'notification.env').write_bytes(oldenv);(backup/'notification.env').chmod(0o600)
        agent=Path('/srv/fumai-zhiwei/agent/current').resolve();frontend=Path('/srv/fumai-zhiwei/frontend/current').resolve()
        # The API service also serves auth/notification static files; keep their exact previous bytes.
        public_assets={}
        for name in ('index.html','app.js','agent.js','notifications.html','notifications.js','login.html','auth-guard.js'):
            with urllib.request.urlopen('http://127.0.0.1/'+name,timeout=5) as response:public_assets[name]=response.read()
        release=root/'releases'/commit;assert not release.exists(),'Release exists; inspect rather than overwrite';release.mkdir()
        with urllib.request.urlopen('https://codeload.github.com/lheng2386-png/fumai-zhiwei/tar.gz/'+commit,timeout=40) as response:payload=response.read(32*1024*1024+1)
        assert len(payload)<=32*1024*1024
        total=0
        with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
            for member in archive:
                parts=PurePosixPath(member.name).parts
                if len(parts)<2 or member.isdir():continue
                relative=PurePosixPath(*parts[1:])
                if not str(relative).startswith('notifications/'):continue
                assert member.isfile() and not relative.is_absolute() and '..' not in relative.parts
                total+=member.size;assert total<16*1024*1024
                target=release.joinpath(*relative.parts);target.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(member) as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)
                target.chmod(0o644)
        shutil.copytree(old/'frontend',release/'frontend')
        subprocess.run(['/opt/fumai-node/bin/node','--test'],cwd=release/'notifications',check=True,timeout=60)
        print('MONITORING_BACKEND_PREFLIGHT_PASS',flush=True)
        assert current.resolve()==old and envfile.read_bytes()==oldenv and site.read_bytes()==oldsite,'Concurrent deployment detected'
        stopped=False;switched=False
        try:
            subprocess.run(['systemctl','stop','fumai-notification-api','fumai-notification-worker'],check=True,timeout=30);stopped=True
            with sqlite3.connect(config['FUMAI_DB_PATH']) as source,sqlite3.connect(backup/'notifications.sqlite') as dest:source.backup(dest)
            (backup/'notifications.sqlite').chmod(0o600)
            link(release,current);switched=True
            subprocess.run(['systemctl','start','fumai-notification-api','fumai-notification-worker'],check=True,timeout=30)
            for _ in range(25):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8787/api/health',timeout=2) as response:health=json.load(response)
                    if health.get('equipment_monitor_logs') and health.get('model_result_ingestion')=='competition-inference-v1':break
                except Exception:pass
                time.sleep(1)
            else:raise RuntimeError('Monitoring backend health failed')
            for name,expected_asset in public_assets.items():
                with urllib.request.urlopen('http://127.0.0.1/'+name,timeout=5) as response:assert response.read()==expected_asset,name+' changed unexpectedly'
            with urllib.request.urlopen('http://127.0.0.1/agent-api/health',timeout=5) as response:assert json.load(response)['mcp_tools']==5
            assert agent==Path('/srv/fumai-zhiwei/agent/current').resolve() and frontend==Path('/srv/fumai-zhiwei/frontend/current').resolve()
            assert envfile.read_bytes()==oldenv and site.read_bytes()==oldsite,'Configuration changed unexpectedly'
            assert subprocess.check_output(['systemctl','is-active','fumai-notification-worker'],text=True).strip()=='active'
            print('MONITORING_BACKEND_DEPLOY_PASS',json.dumps({'commit':commit,'frontend_bytes_preserved':True,'nginx_and_smtp_preserved':True,'database_backup':str(backup),'model_deployment_attempted':False}),flush=True)
        except Exception:
            if stopped:
                subprocess.run(['systemctl','stop','fumai-notification-api','fumai-notification-worker'],check=False,timeout=30)
                if switched:link(old,current)
                subprocess.run(['systemctl','start','fumai-notification-api','fumai-notification-worker'],check=False,timeout=30)
            raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--commit',required=True);p.add_argument('--expected-current',required=True)
    args=p.parse_args();deploy(args.commit,args.expected_current)
