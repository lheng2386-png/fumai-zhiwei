#!/usr/bin/env bash
set -Eeuo pipefail

COMMIT="${1:-}"
if [[ ! $COMMIT =~ ^[0-9a-f]{40}$ ]]; then
    echo 'Usage: deploy-fumai-notifications.sh FULL_GIT_COMMIT_SHA' >&2
    exit 2
fi
NODE_VERSION='v24.22.1'
NODE_ARCHIVE="node-${NODE_VERSION}-linux-x64.tar.gz"
NODE_ROOT="/opt/node-${NODE_VERSION}-linux-x64"
NODE_LINK='/opt/fumai-node'
APP_ROOT='/srv/fumai-zhiwei/notifications'
RELEASE="$APP_ROOT/releases/$COMMIT"
CURRENT="$APP_ROOT/current"
STATE_ROOT='/var/lib/fumai-notifications'
DB_PATH="$STATE_ROOT/fumai-notifications.sqlite3"
ENV_FILE='/etc/fumai-notifications.env'
API_UNIT='/etc/systemd/system/fumai-notification-api.service'
WORKER_UNIT='/etc/systemd/system/fumai-notification-worker.service'
PUBLIC_ORIGIN='http://47.122.123.147'
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="/var/backups/fumai-notifications/$STAMP"
ARCHIVE_URL="https://codeload.github.com/lheng2386-png/fumai-zhiwei/tar.gz/$COMMIT"

NGINX_CONFIG=''
NGINX_BACKUP=''
OLD_CURRENT=''
CONFIG_CHANGED=0
DEPLOY_SUCCEEDED=0

log() { printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

rollback() {
    local code=$?
    if [[ $DEPLOY_SUCCEEDED -eq 1 ]]; then return 0; fi
    printf '\nDeployment failed (exit %s). Restoring the previous entry points.\n' "$code" >&2
    if [[ $CONFIG_CHANGED -eq 1 && -n "$NGINX_BACKUP" && -f "$NGINX_BACKUP" ]]; then
        cp -a "$NGINX_BACKUP" "$NGINX_CONFIG"
        nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
    fi
    if [[ -n "$OLD_CURRENT" && -d "$OLD_CURRENT" ]]; then
        ln -sfn "$OLD_CURRENT" "$CURRENT"
        systemctl restart fumai-notification-api.service fumai-notification-worker.service || true
    else
        systemctl disable --now fumai-notification-api.service fumai-notification-worker.service >/dev/null 2>&1 || true
        rm -f "$CURRENT"
    fi
    exit "$code"
}
trap rollback ERR

if [[ $(id -u) -ne 0 ]]; then
    echo 'Run this script as root.' >&2
    exit 1
fi

log 'Preflight checks'
for command in nginx systemctl python3 tar sha256sum grep readlink runuser; do
    command -v "$command" >/dev/null || { echo "Missing required command: $command" >&2; exit 1; }
done
systemctl is-active --quiet nginx || { echo 'Nginx is not active.' >&2; exit 1; }
[[ -L /srv/fumai-zhiwei/frontend/current ]] || { echo 'Existing frontend current symlink is missing.' >&2; exit 1; }
[[ -f /srv/fumai-zhiwei/frontend/current/dist/index.html ]] || { echo 'Existing frontend index is missing.' >&2; exit 1; }

mapfile -t nginx_candidates < <(
    grep -RslF 'root /srv/fumai-zhiwei/frontend/current/dist;' /etc/nginx/sites-enabled /etc/nginx/conf.d 2>/dev/null \
        | while read -r item; do readlink -f "$item"; done | sort -u
)
if [[ ${#nginx_candidates[@]} -ne 1 ]]; then
    printf 'Expected one Fumai Nginx config, found %s:\n' "${#nginx_candidates[@]}" >&2
    printf '  %s\n' "${nginx_candidates[@]:-none}" >&2
    exit 1
fi
NGINX_CONFIG="${nginx_candidates[0]}"

if [[ -e "$CURRENT" ]]; then OLD_CURRENT="$(readlink -f "$CURRENT")"; fi
if command -v ss >/dev/null && ss -ltnH '( sport = :8787 )' | grep -q . && ! systemctl is-active --quiet fumai-notification-api.service; then
    echo 'Port 8787 is already used by another service.' >&2
    exit 1
fi

available_kb=$(df -Pk /srv | awk 'NR==2 {print $4}')
if [[ ${available_kb:-0} -lt 350000 ]]; then
    echo 'Less than 350 MB is available under /srv.' >&2
    exit 1
fi

mkdir -p "$BACKUP_ROOT" "$APP_ROOT/releases" "$STATE_ROOT"
NGINX_BACKUP="$BACKUP_ROOT/$(basename "$NGINX_CONFIG")"
cp -a "$NGINX_CONFIG" "$NGINX_BACKUP"
[[ -f "$ENV_FILE" ]] && cp -a "$ENV_FILE" "$BACKUP_ROOT/notification.env" || true
[[ -f "$API_UNIT" ]] && cp -a "$API_UNIT" "$BACKUP_ROOT/fumai-notification-api.service" || true
[[ -f "$WORKER_UNIT" ]] && cp -a "$WORKER_UNIT" "$BACKUP_ROOT/fumai-notification-worker.service" || true

log "Install verified Node.js $NODE_VERSION runtime"
if [[ ! -x "$NODE_ROOT/bin/node" ]]; then
    scratch=$(mktemp -d)
    python3 - "$NODE_VERSION" "$scratch" <<'PY'
import pathlib, sys, urllib.request
version, target = sys.argv[1], pathlib.Path(sys.argv[2])
base = f'https://nodejs.org/download/release/{version}'
for name in (f'node-{version}-linux-x64.tar.gz', 'SHASUMS256.txt'):
    request = urllib.request.Request(f'{base}/{name}', headers={'User-Agent': 'fumai-deploy/1.0'})
    with urllib.request.urlopen(request, timeout=90) as source, (target / name).open('wb') as output:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
PY
    expected=$(awk -v file="$NODE_ARCHIVE" '$2 == file {print $1}' "$scratch/SHASUMS256.txt")
    [[ $expected =~ ^[0-9a-f]{64}$ ]] || { echo 'Node checksum was not found.' >&2; exit 1; }
    printf '%s  %s\n' "$expected" "$scratch/$NODE_ARCHIVE" | sha256sum -c -
    tar -xzf "$scratch/$NODE_ARCHIVE" -C /opt
    rm -rf "$scratch"
fi
ln -sfn "$NODE_ROOT" "$NODE_LINK"
"$NODE_LINK/bin/node" --version | grep -Fx "$NODE_VERSION" >/dev/null

log "Prepare application release $COMMIT"
release_is_valid() {
    [[ -f "$RELEASE/DEPLOYED_COMMIT" ]] &&
    grep -Fx "$COMMIT" "$RELEASE/DEPLOYED_COMMIT" >/dev/null 2>&1 &&
    [[ -f "$RELEASE/notifications/src/migrate.mjs" ]] &&
    [[ -f "$RELEASE/notifications/src/server.mjs" ]] &&
    [[ -f "$RELEASE/frontend/dist/notifications.html" ]]
}

if [[ -e "$RELEASE" ]] && ! release_is_valid; then
    echo 'An incomplete release from an earlier attempt was found; moving it into the deployment backup.'
    mv "$RELEASE" "$BACKUP_ROOT/incomplete-release"
fi

if ! release_is_valid; then
    stage=$(mktemp -d "$APP_ROOT/releases/.stage-XXXXXX")
    archive=$(mktemp)
    python3 - "$ARCHIVE_URL" "$archive" <<'PY'
import pathlib, sys, time, urllib.request
url, target = sys.argv[1], pathlib.Path(sys.argv[2])
last_error = None
for attempt in range(4):
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'fumai-deploy/1.0'})
        with urllib.request.urlopen(request, timeout=90) as source, target.open('wb') as output:
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > 64 * 1024 * 1024:
                    raise RuntimeError('Repository archive exceeds 64 MiB')
                output.write(chunk)
        break
    except Exception as error:
        last_error = error
        target.unlink(missing_ok=True)
        if attempt == 3:
            raise
        time.sleep(2 ** attempt)
PY
    python3 - "$archive" "$stage" "$COMMIT" <<'PY'
import pathlib, shutil, sys, tarfile
archive, stage, commit = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
prefix = f'fumai-zhiwei-{commit}/'
allowed_files = {
    'frontend/dist/index.html',
    'frontend/dist/notifications.html',
    'frontend/dist/notifications.js',
    'frontend/dist/notification-admin.css',
    'frontend/dist/login.html',
    'frontend/dist/login.js',
    'frontend/dist/login.css',
    'frontend/dist/auth-guard.js',
}
total = 0
with tarfile.open(archive, 'r:gz') as source:
    for member in source:
        if not member.name.startswith(prefix):
            continue
        relative = pathlib.PurePosixPath(member.name[len(prefix):])
        path = relative.as_posix()
        wanted = path == 'notifications' or path.startswith('notifications/') or path in allowed_files
        if not wanted or member.isdir():
            continue
        if not member.isfile() or relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError(f'Unsafe archive member: {path}')
        total += member.size
        if member.size < 0 or total > 32 * 1024 * 1024:
            raise RuntimeError('Extracted application exceeds 32 MiB')
        destination = stage.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.extractfile(member) as data, destination.open('xb') as output:
            shutil.copyfileobj(data, output)
required = [
    'notifications/package.json', 'notifications/src/server.mjs',
    'notifications/src/run-worker.mjs', 'notifications/src/operations.mjs',
    'notifications/migrations/001_notifications.sql', 'notifications/migrations/002_operations.sql',
    'frontend/dist/index.html', 'frontend/dist/auth-guard.js',
    'frontend/dist/login.html', 'frontend/dist/login.js', 'frontend/dist/login.css',
    'frontend/dist/notifications.html', 'frontend/dist/notifications.js',
    'frontend/dist/notification-admin.css',
]
for name in required:
    if not (stage / name).is_file():
        raise RuntimeError(f'Required release file is missing: {name}')
(stage / 'DEPLOYED_COMMIT').write_text(commit + '\n', encoding='ascii')
PY
    rm -f "$archive"
    chmod -R a=rX,u+w "$stage"
    mkdir "$RELEASE"
    cp -a "$stage/." "$RELEASE/"
    rm -rf "$stage"
fi
release_is_valid || { echo 'Prepared release failed validation.' >&2; find "$RELEASE" -maxdepth 4 -type f -print >&2; exit 1; }
chmod 0755 "$APP_ROOT" "$APP_ROOT/releases" "$RELEASE"
ln -sfn "$RELEASE" "$CURRENT"
[[ "$(readlink -f "$CURRENT")" == "$RELEASE" ]] || { echo 'Current release symlink is incorrect.' >&2; exit 1; }
[[ -f "$CURRENT/notifications/src/migrate.mjs" ]] || {
    echo 'Migration file is not visible through the current release symlink.' >&2
    ls -la "$APP_ROOT" "$RELEASE" "$RELEASE/notifications" "$RELEASE/notifications/src" >&2 || true
    exit 1
}

log 'Create isolated service account and persistent database directory'
if ! id fumai-notify >/dev/null 2>&1; then
    useradd --system --home-dir "$STATE_ROOT" --shell /usr/sbin/nologin fumai-notify
fi
chown fumai-notify:fumai-notify "$STATE_ROOT"
chmod 0750 "$STATE_ROOT"
runuser -u fumai-notify -- test -r "$CURRENT/notifications/src/migrate.mjs" || {
    echo 'The service account cannot read the migration file.' >&2
    namei -l "$CURRENT/notifications/src/migrate.mjs" >&2 || true
    exit 1
}

python3 - "$ENV_FILE" "$DB_PATH" "$PUBLIC_ORIGIN" "$CURRENT/frontend/dist" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
updates = {
    'FUMAI_DB_PATH': sys.argv[2],
    'FUMAI_NOTIFICATION_PORT': '8787',
    'FUMAI_PUBLIC_ORIGIN': sys.argv[3],
    'FUMAI_STATIC_ROOT': sys.argv[4],
    'FUMAI_SESSION_HOURS': '12',
    'FUMAI_SELF_REGISTRATION': 'true',
    'FUMAI_WORKER_ID': 'worker-server-1',
    'FUMAI_WORKER_CONCURRENCY': '4',
    'FUMAI_WORKER_LEASE_SECONDS': '60',
    'FUMAI_WORKER_MAX_ATTEMPTS': '5',
}
defaults = {
    'FUMAI_NOTIFICATION_MODE': 'mock',
    'FUMAI_EMAIL_PROVIDER': 'mock',
    'FUMAI_SMS_PROVIDER': 'mock',
}
existing = []
seen = set()
if path.exists():
    for line in path.read_text(encoding='utf-8').splitlines():
        key = line.split('=', 1)[0] if '=' in line and not line.lstrip().startswith('#') else None
        if key in updates:
            existing.append(f'{key}={updates[key]}')
            seen.add(key)
        else:
            existing.append(line)
        if key:
            seen.add(key)
for key, value in updates.items():
    if key not in seen:
        existing.append(f'{key}={value}')
for key, value in defaults.items():
    if key not in seen:
        existing.append(f'{key}={value}')
path.write_text('\n'.join(existing).strip() + '\n', encoding='utf-8')
PY
chown root:fumai-notify "$ENV_FILE"
chmod 0640 "$ENV_FILE"

cat > "$API_UNIT" <<EOF
[Unit]
Description=Fumai notification API
After=network.target

[Service]
Type=simple
User=fumai-notify
Group=fumai-notify
WorkingDirectory=$CURRENT/notifications
EnvironmentFile=$ENV_FILE
ExecStart=$NODE_LINK/bin/node src/server.mjs
Restart=on-failure
RestartSec=3
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$STATE_ROOT
MemoryMax=256M
TasksMax=64

[Install]
WantedBy=multi-user.target
EOF

cat > "$WORKER_UNIT" <<EOF
[Unit]
Description=Fumai notification delivery worker
After=network.target fumai-notification-api.service

[Service]
Type=simple
User=fumai-notify
Group=fumai-notify
WorkingDirectory=$CURRENT/notifications
EnvironmentFile=$ENV_FILE
ExecStart=$NODE_LINK/bin/node src/run-worker.mjs
Restart=always
RestartSec=3
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$STATE_ROOT
MemoryMax=256M
TasksMax=64

[Install]
WantedBy=multi-user.target
EOF

log 'Run the idempotent database migration and start API/Worker'
runuser -u fumai-notify -- env \
    FUMAI_DB_PATH="$DB_PATH" \
    "$NODE_LINK/bin/node" "$CURRENT/notifications/src/migrate.mjs"

needs_admin=$(cd "$CURRENT/notifications" && runuser -u fumai-notify -- env \
    FUMAI_DB_PATH="$DB_PATH" \
    "$NODE_LINK/bin/node" --input-type=module -e \
    "import { openDatabase } from './src/db.mjs'; const db=openDatabase(); console.log(db.prepare('SELECT count(*) count FROM app_user').get().count===0?'yes':'no'); db.close();")
if [[ "$needs_admin" == 'yes' ]]; then
    if [[ ! -t 0 ]]; then
        echo 'No application administrator exists. Run this deployment in an interactive terminal to create one.' >&2
        exit 1
    fi
    printf 'Initial website administrator username: '
    read -r bootstrap_username
    printf 'Initial website administrator password (10-128 characters): '
    read -rs bootstrap_password
    printf '\n'
    [[ $bootstrap_username =~ ^[A-Za-z0-9._-]{3,40}$ ]] || { echo 'Administrator username format is invalid.' >&2; exit 1; }
    [[ ${#bootstrap_password} -ge 10 && ${#bootstrap_password} -le 128 ]] || { echo 'Administrator password length is invalid.' >&2; exit 1; }
    (cd "$CURRENT/notifications" && runuser -u fumai-notify -- env \
        FUMAI_DB_PATH="$DB_PATH" \
        FUMAI_BOOTSTRAP_ADMIN_USERNAME="$bootstrap_username" \
        FUMAI_BOOTSTRAP_ADMIN_PASSWORD="$bootstrap_password" \
        "$NODE_LINK/bin/node" --input-type=module -e \
        "import { openDatabase } from './src/db.mjs'; import { AuthService } from './src/auth.mjs'; const db=openDatabase(); const user=new AuthService(db).ensureBootstrapFromEnv(); if(!user) throw new Error('Administrator was not created'); console.log('Created website administrator: '+user.username); db.close();")
    unset bootstrap_password
fi
systemctl daemon-reload
systemctl enable fumai-notification-api.service fumai-notification-worker.service
systemctl restart fumai-notification-api.service fumai-notification-worker.service
for attempt in {1..20}; do
    if python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=2) as response:
    body = json.load(response)
assert body['status'] == 'ok'
assert body['authentication_ready'] is True
PY
    then break; fi
    if [[ $attempt -eq 20 ]]; then
        journalctl -u fumai-notification-api.service -n 50 --no-pager >&2 || true
        exit 1
    fi
    sleep 1
done

log 'Connect Nginx to the notification API and management page'
python3 - "$NGINX_CONFIG" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding='utf-8')
begin = '    # BEGIN FUMAI NOTIFICATIONS\n'
end = '    # END FUMAI NOTIFICATIONS\n'
block = begin + '''    location /api/ {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 3s;
        proxy_read_timeout 70s;
        proxy_send_timeout 10s;
        proxy_buffering off;
        proxy_cache off;
        client_max_body_size 128k;
        add_header Cache-Control "no-store" always;
        add_header X-Content-Type-Options nosniff always;
    }

    location /agent-api/ {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 70s;
        proxy_buffering off;
        proxy_cache off;
        client_max_body_size 1m;
        add_header Cache-Control "no-store" always;
    }

    location = /notifications.html {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_buffering off;
    }

    location = /notifications.js {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
    }

    location = /notification-admin.css {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
    }

    location = / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
    }

    location = /index.html {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
    }

    location ~ ^/(login\.html|login\.js|login\.css|auth-guard\.js)$ {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        add_header Cache-Control "no-store" always;
    }
''' + end
if begin in text:
    start = text.index(begin)
    finish = text.index(end, start) + len(end)
    text = text[:start] + block + text[finish:]
else:
    needle = 'location /api/'
    start = text.find(needle)
    if start < 0:
        raise RuntimeError('The expected /api/ location was not found')
    line_start = text.rfind('\n', 0, start) + 1
    brace = text.find('{', start)
    if brace < 0:
        raise RuntimeError('The /api/ location has no opening brace')
    depth = 0
    finish = None
    for index in range(brace, len(text)):
        if text[index] == '{': depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                finish = index + 1
                if finish < len(text) and text[finish] == '\n': finish += 1
                break
    if finish is None:
        raise RuntimeError('The /api/ location has no closing brace')
    text = text[:line_start] + block + text[finish:]
path.write_text(text, encoding='utf-8')
PY
CONFIG_CHANGED=1
nginx -t
systemctl reload nginx

log 'Verify public entry points and service state'
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1/api/health', timeout=5) as response:
    health = json.load(response)
assert health['status'] == 'ok'
assert health['authentication_ready'] is True
with urllib.request.urlopen('http://127.0.0.1/login.html', timeout=5) as response:
    login = response.read().decode('utf-8')
assert response.status == 200 and '登录工作台' in login
with urllib.request.urlopen('http://127.0.0.1/notifications.html', timeout=5) as response:
    page = response.read().decode('utf-8')
assert response.status == 200 and '通知' in page
print(json.dumps({'health': health, 'login_page': 'ok', 'notifications_page': 'ok'}, ensure_ascii=False))
PY
systemctl --no-pager --full status fumai-notification-api.service fumai-notification-worker.service | sed -n '1,24p'

DEPLOY_SUCCEEDED=1
trap - ERR
printf '\nDEPLOYMENT COMPLETE\n'
printf 'Commit: %s\n' "$COMMIT"
printf 'Mode: authenticated website, mock providers until server secrets are configured\n'
printf 'Page: %s/notifications.html\n' "$PUBLIC_ORIGIN"
printf 'Backup: %s\n' "$BACKUP_ROOT"
