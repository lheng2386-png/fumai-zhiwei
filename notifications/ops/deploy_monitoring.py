#!/usr/bin/env python3
"""Deploy pinned result-ingestion/UI code without changing Agent, model service or SMTP secrets."""
from pathlib import Path, PurePosixPath
import argparse, fcntl, io, json, os, re, shlex, shutil, sqlite3, subprocess, tarfile, time, urllib.request


def link(target, path):
    temporary = path.with_name(path.name + '.monitor-next')
    if temporary.is_symlink(): temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, path)


def deploy(commit, expected):
    assert re.fullmatch('[0-9a-f]{40}', commit) and re.fullmatch('[0-9a-f]{40}', expected)
    root=Path('/srv/fumai-zhiwei/notifications');current=root/'current';old=current.resolve()
    assert old.name==expected, 'Current notification release changed; inspect it before deploying'
    with (root/'.deploy-monitoring.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        site=Path('/etc/nginx/sites-enabled/fumai-zhiwei');oldsite=site.read_text()
        envfile=Path('/etc/fumai-notifications.env');oldenv=envfile.read_bytes()
        config={}
        for line in oldenv.decode().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1);config[key]=' '.join(shlex.split(value))
        backup=Path('/var/backups/fumai-notifications')/('monitoring-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
        backup.mkdir(parents=True,mode=0o700)
        (backup/'notification.env').write_bytes(oldenv);(backup/'notification.env').chmod(0o600)
        (backup/'nginx.conf').write_text(oldsite)
        agent=Path('/srv/fumai-zhiwei/agent/current').resolve()
        frontend=Path('/srv/fumai-zhiwei/frontend/current').resolve()
        static=['index.html','login.html','login.js','login.css','auth-guard.js','notifications.html',
                'notifications.js','notification-admin.css','app.js','monitoring.js','monitoring.css']
        release=root/'releases'/commit
        assert not release.exists(), 'Release already exists; inspect it rather than overwriting'
        release.mkdir()
        with urllib.request.urlopen('https://codeload.github.com/lheng2386-png/fumai-zhiwei/tar.gz/'+commit,timeout=40) as response:payload=response.read(32*1024*1024+1)
        assert len(payload)<=32*1024*1024
        total=0
        with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
            for member in archive:
                parts=PurePosixPath(member.name).parts
                if len(parts)<2 or member.isdir():continue
                relative=PurePosixPath(*parts[1:]);name=str(relative)
                if not(name.startswith('notifications/') or name in ['frontend/dist/'+name for name in static]):continue
                assert member.isfile() and not relative.is_absolute() and '..' not in relative.parts
                total+=member.size;assert total<16*1024*1024
                target=release.joinpath(*relative.parts);target.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(member) as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)
                target.chmod(0o644)
        subprocess.run(['/opt/fumai-node/bin/node','--test'],cwd=release/'notifications',check=True,timeout=60)
        for name in ('app.js','monitoring.js'):
            subprocess.run(['/opt/fumai-node/bin/node','--check',str(release/'frontend/dist'/name)],check=True,timeout=10)
        print('MONITORING_PREFLIGHT_PASS',flush=True)
        assert current.resolve()==old and envfile.read_bytes()==oldenv and site.read_text()==oldsite, 'Concurrent deployment detected'
        switched=False;stopped=False
        try:
            subprocess.run(['systemctl','stop','fumai-notification-api','fumai-notification-worker'],check=True,timeout=30);stopped=True
            with sqlite3.connect(config['FUMAI_DB_PATH']) as source,sqlite3.connect(backup/'notifications.sqlite') as dest:source.backup(dest)
            (backup/'notifications.sqlite').chmod(0o600)
            # Keep current agent.js/agent.css/intent.js served from their own release.
            addition='''    # BEGIN FUMAI MONITORING
    location ~ ^/(app\\.js|monitoring\\.js|monitoring\\.css)$ {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        add_header Cache-Control "no-store" always;
    }
    # END FUMAI MONITORING

'''
            assert '# BEGIN FUMAI MONITORING' not in oldsite
            assert '# BEGIN FUMAI NOTIFICATIONS' in oldsite
            site.write_text(oldsite.replace('    # BEGIN FUMAI NOTIFICATIONS',addition+'    # BEGIN FUMAI NOTIFICATIONS',1))
            subprocess.run(['nginx','-t'],check=True,timeout=10)
            link(release,current);switched=True
            subprocess.run(['systemctl','start','fumai-notification-api','fumai-notification-worker'],check=True,timeout=30)
            for _ in range(25):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8787/api/health',timeout=2) as response:health=json.load(response)
                    if health.get('equipment_monitor_logs') and health.get('model_result_ingestion')=='competition-inference-v1':break
                except Exception:pass
                time.sleep(1)
            else:raise RuntimeError('Monitoring health failed')
            subprocess.run(['systemctl','reload','nginx'],check=True,timeout=10)
            for name in ('app.js','monitoring.js','monitoring.css','index.html'):
                with urllib.request.urlopen('http://127.0.0.1/'+name,timeout=5) as response:assert response.read()==(release/'frontend/dist'/name).read_bytes(),name+' mismatch'
            with urllib.request.urlopen('http://127.0.0.1/agent-api/health',timeout=5) as response:assert json.load(response)['mcp_tools']==5
            assert agent==Path('/srv/fumai-zhiwei/agent/current').resolve()
            assert frontend==Path('/srv/fumai-zhiwei/frontend/current').resolve()
            assert envfile.read_bytes()==oldenv,'SMTP configuration changed unexpectedly'
            assert subprocess.check_output(['systemctl','is-active','fumai-notification-worker'],text=True).strip()=='active'
            print('MONITORING_DEPLOY_PASS',json.dumps({'commit':commit,'agent_preserved':agent.name,'smtp_configuration_preserved':True,'database_backup':str(backup),'model_deployment_attempted':False}),flush=True)
        except Exception:
            if stopped:
                subprocess.run(['systemctl','stop','fumai-notification-api','fumai-notification-worker'],check=False,timeout=30)
                if switched:link(old,current)
                site.write_text(oldsite)
                subprocess.run(['nginx','-t'],check=False,timeout=10)
                subprocess.run(['systemctl','start','fumai-notification-api','fumai-notification-worker'],check=False,timeout=30)
                subprocess.run(['systemctl','reload','nginx'],check=False,timeout=10)
            raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--commit',required=True);p.add_argument('--expected-current',required=True)
    args=p.parse_args();deploy(args.commit,args.expected_current)
