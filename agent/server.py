"""Loopback-only HTTP service, intended behind nginx. No external dependencies."""
from __future__ import annotations

import argparse
import copy
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .core import InputError, Snapshot
from .config import configured_model
from .intent import classify_question, direct_answer, informational_intent, normalized
from .store import Store
from .tasks import AnalysisTasks, TaskBusy, TERMINAL
from .conversation import Conversation, guard_model, context_excerpt


class HTTPError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Application:
    def __init__(self, snapshot, store, origin, users=None, model=None, release="development", static_root=None):
        self.snapshot, self.store, self.origin = snapshot, store, origin.rstrip("/")
        self.users, self.model, self.release = users or {}, model, release
        self.static_root = Path(static_root).resolve() if static_root else None
        self.slots = threading.BoundedSemaphore(3)
        self.stream_slots = threading.BoundedSemaphore(8)
        self.rate_lock = threading.Lock()
        self.rates = defaultdict(deque)
        if urlsplit(self.origin).scheme not in {"http", "https"} or not urlsplit(self.origin).netloc:
            raise ValueError("AGENT_ORIGIN must be an exact origin")
        if snapshot.mode == "live" and (not self.origin.startswith("https://") or not self.users):
            raise ValueError("Live mode requires HTTPS origin and server-configured users")
        for token, user in self.users.items():
            if len(token) < 32 or not isinstance(user, dict) or user.get("role") not in {"viewer", "operator"} or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user.get("id", "")):
                raise ValueError("Invalid configured user token, id or role")
        self.tasks = AnalysisTasks(store, self.slots, self.analyze)
        self.conversation = Conversation(snapshot,store)

    def analyze(self, owner, body, progress=None):
        conversation_id=body.get('_conversation_id') or self.store.default_conversation(owner)
        fresh_analysis=any(kind=='maintenance' for _,kind in self.conversation.routes(owner,body))
        if not fresh_analysis:
            if progress:
                progress("answering")
            result=self.conversation.direct(owner,body,self.model)
            if self.model and self.conversation.can_enrich_reply(owner,body) and result.get('events'):
                try:
                    bundle={**copy.deepcopy(result),'allow_tools':False,'hypotheses':[],'checks':[],'unknowns':[],
                            'questions':[{'question_id':a['question_id'],'question':a['question']} for a in result['answers']],
                            'conversation_context':[context_excerpt(r) for r in self.store.turns(owner,conversation_id,limit=8)]}
                    # Do not anchor the model on the deterministic fallback answer.
                    bundle.pop('answers',None)
                    bundle['summary']='请直接回答当前追问的具体问题；已有观测是引用材料，不是要重复输出的报告。'
                    bundle['reply_requirements']={'mode':'followup','no_new_diagnosis':True,'do_not_repeat_previous_reply':True}
                    enriched=self.model.enrich(self.snapshot,body['message'],bundle,progress=progress) if progress else self.model.enrich(self.snapshot,body['message'],bundle)
                    if enriched.get('model_analysis'):
                        enriched['model_analysis']['hypotheses']=[]  # No model-generated cause list is published.
                    guard_model(enriched.get('model_analysis'),result['events'])
                    analysis=enriched['model_analysis'];answers={a['question_id']:a for a in analysis.get('answers',[])}
                    for entry in result['answers']:
                        if entry['intent']=='followup' and not re.search(r'通信|缺失|排序|首位|第一|原因',entry['question']):
                            answer=answers.get(entry['question_id'])
                            if answer:entry['answer']=answer['answer']
                            elif len(result['answers'])==1:entry['answer']=analysis['summary']
                    result.update(engine=enriched['engine'],model_status='connected',model_name=enriched['model_name'])
                    if len(result['answers'])==1:result['summary']=result['answers'][0]['answer']
                except Exception:
                    result['model_status']='failed_fallback'
            return self.store.save_run(owner,body['message'],result,conversation_id)
        if progress:
            progress("evidence")
        result = self.conversation.prepare_analysis(owner,body)
        result.update(response_type="analysis", intent="maintenance")
        introduction = informational_intent(normalized(body["message"]))
        if introduction:
            result["introduction"] = direct_answer(introduction, self.model)["summary"]
        if self.model:
            try:
                if progress:
                    progress("model")
                    result = self.model.enrich(self.snapshot, body["message"], copy.deepcopy(result), progress=progress)
                else:
                    result = self.model.enrich(self.snapshot, body["message"], copy.deepcopy(result))
                if result.get('model_analysis'):
                    result['model_analysis']['hypotheses']=[]  # The report uses the deterministic unranked possibilities.
                guard_model(result.get('model_analysis'),result['events'])
            except Exception:
                result.pop('model_analysis',None)
                result.update(engine='rules',model_status='failed_fallback')
                result["unknowns"].insert(0, "模型请求失败、回答格式无效或包含无依据归因，以下保留可核对的观测与规则建议。")
        if progress:
            progress("saving")
        result=self.conversation.finish_analysis(owner,body,result,self.model)
        return self.store.save_run(owner,body['message'],result,conversation_id)

    @staticmethod
    def request_intent(body):
        return classify_question(body.get("message"), bool(body.get("event_id") or body.get("device_id")))

    def identity(self, headers, write=False):
        origin = headers.get("Origin")
        if origin and origin != self.origin:
            raise HTTPError(403, "来源不匹配")
        bearer=headers.get('Authorization','')
        if self.snapshot.mode == "live" or bearer:
            if headers.get("X-Forwarded-Proto") != "https":
                raise HTTPError(403, "实测模式必须通过 HTTPS 访问")
            token = bearer[7:] if bearer.startswith("Bearer ") else ""
            for known, user in self.users.items():
                if hmac.compare_digest(token.encode(), known.encode()):
                    if write and user["role"] != "operator":
                        raise HTTPError(403, "当前账号没有草稿确认权限")
                    return user["id"]
            raise HTTPError(401, "请使用管理员分配的访问令牌连接")
        if write and origin != self.origin:
            raise HTTPError(403, "提交需要同源页面")
        session = headers.get("X-Fumai-Session", "")
        try:
            uuid.UUID(session)
        except (ValueError, AttributeError):
            raise HTTPError(400, "需要有效的演示会话") from None
        client=headers.get('X-Fumai-Client')
        if client:
            try:client=str(uuid.UUID(client))
            except (ValueError,TypeError,AttributeError):raise HTTPError(400,'需要有效的浏览器访客标识') from None
            owner='browser:'+client
            self.store.claim_legacy(owner,session)
            return owner
        return "demo:" + session

    def limit(self, key, maximum=20):
        now = time.monotonic()
        with self.rate_lock:
            # Bounded memory; loopback proxy passes a sanitized client address.
            for old in list(self.rates):
                if not self.rates[old] or now-self.rates[old][-1] > 60:
                    del self.rates[old]
            if key not in self.rates and len(self.rates) >= 2000:
                raise HTTPError(429, "服务繁忙，请稍后再试")
            queue = self.rates[key]
            while queue and now-queue[0] > 60:
                queue.popleft()
            if len(queue) >= maximum:
                raise HTTPError(429, "请求过于频繁，请一分钟后再试")
            queue.append(now)

    def dispatch(self, method, path, headers, body, client):
        if path == "/agent-api/health" and method == "GET":
            return {"status": "ok", "release": self.release, "data_mode": self.snapshot.mode,
                    "model_configured": self.model is not None, "model_verified": False,
                    "device_control": False}
        write = method == "POST"
        owner = self.identity(headers, write and path in {"/agent-api/drafts", "/agent-api/confirm"})
        if write and self.snapshot.mode == "demo" and headers.get("Origin") != self.origin:
            raise HTTPError(403, "提交需要同源页面")
        if method == "GET" and path.startswith("/agent-api/analysis-tasks/"):
            self.limit(client + ":task-poll", 120)
        elif path in {'/agent-api/bootstrap','/agent-api/history','/agent-api/conversations','/agent-api/run'}:
            self.limit(client+':history',120)
        else:
            self.limit(client,30)
        snapshot = self.snapshot
        conversation_id=headers.get('X-Fumai-Session') or self.store.default_conversation(owner)
        conversation_id=self.store.ensure_conversation(owner,conversation_id)
        if method == "GET" and path == "/agent-api/bootstrap":
            return {"metadata": snapshot.meta, "mode": snapshot.mode, "model_configured": self.model is not None,
                    "stale": snapshot.stale, "devices": list(snapshot.devices.values()), "events": snapshot.list_events(limit=12)["events"],
                    "history": self.store.history(owner,analysis_only=True), "device_control": False, "release": self.release,
                    'conversation_id':conversation_id,'conversations':self.store.conversations(owner),
                    'turns':self.store.turns(owner,conversation_id),
                    'identity':{'kind':'account' if owner in [u['id'] for u in self.users.values()] else 'browser_guest',
                                'history_scope':'account' if owner in [u['id'] for u in self.users.values()] else 'current_browser'},
                    'capabilities':{'history':True,'context':True,'shared_business_data':False,'generation_forecast':False,'notifications':False}}
        if method=='POST' and path=='/agent-api/history':
            if set(body)-{'device_id','conversation_only','limit','offset'}:raise InputError('未知历史查询参数')
            return {'records':self.store.history(owner,conversation_id=conversation_id if body.get('conversation_only') is True else None,
                       device_id=body.get('device_id'),analysis_only=True,limit=body.get('limit',20),offset=body.get('offset',0))}
        if method=='GET' and path=='/agent-api/conversations':
            return {'conversations':self.store.conversations(owner)}
        if method=='POST' and path=='/agent-api/conversations':
            if set(body)!={'conversation_id'}:raise InputError('需要会话编号')
            target=self.store.ensure_conversation(owner,body['conversation_id'])
            return {'conversation_id':target,'turns':self.store.turns(owner,target),'conversations':self.store.conversations(owner)}
        if method == "POST" and path == "/agent-api/analyze":
            if set(body) - {"message", "event_id", "device_id",'response_mode'}:
                raise InputError("未知分析参数")
            if body.get('response_mode','auto') not in {'auto','report','chat'}:raise InputError('未知回答方式')
            body={**body,'_conversation_id':conversation_id}
            if not self.conversation.needs_model(owner,body):
                return self.analyze(owner, body)
            if not self.slots.acquire(blocking=False):
                raise HTTPError(503, "模型正在处理其他任务，请稍后重试")
            try:
                return self.analyze(owner, body)
            finally:
                self.slots.release()
        if method == "POST" and path == "/agent-api/analysis-tasks":
            if set(body) - {"message", "event_id", "device_id", "request_id",'response_mode'}:
                raise InputError("未知分析参数")
            if body.get('response_mode','auto') not in {'auto','report','chat'}:raise InputError('未知回答方式')
            body={**body,'_conversation_id':conversation_id}
            maintenance = self.conversation.needs_model(owner,body)
            if any(kind=='maintenance' for _,kind in self.conversation.routes(owner,body)):
                self.conversation.prepare_analysis(owner,body)
            try:
                return self.tasks.submit(owner, body, reserve_model_slot=maintenance)
            except TaskBusy as exc:
                raise HTTPError(503, str(exc)) from None
        if method == "GET" and path.startswith("/agent-api/analysis-tasks/"):
            return self.tasks.get(owner, path.removeprefix("/agent-api/analysis-tasks/"))
        if method == "POST" and path == "/agent-api/run":
            if set(body) != {"run_id"}:
                raise InputError("需要 run_id")
            return self.store.get_run(owner, body["run_id"])
        if method == "POST" and path == "/agent-api/drafts":
            if set(body) != {"run_id", "event_id"}:
                raise InputError("需要 run_id 与 event_id")
            if snapshot.mode == "live" and snapshot.stale:
                raise HTTPError(409, "当前快照已过期，请更新数据后重新分析")
            run = self.store.get_run(owner, body["run_id"])
            if run.get("response_type") != "analysis":
                raise InputError("普通问答没有设备事件证据，不能生成核查草稿")
            if run["metadata"]["fingerprint"] != snapshot.meta["fingerprint"]:
                raise HTTPError(409, "数据版本已变化，请重新分析")
            return self.store.draft(owner, body["run_id"], body["event_id"])
        if method == "POST" and path == "/agent-api/confirm":
            if set(body) != {"draft_id", "note", "confirmed"} or body["confirmed"] is not True:
                raise InputError("请显式确认并填写说明")
            if snapshot.mode == "live" and snapshot.stale:
                raise HTTPError(409, "当前快照已过期，禁止确认；请更新后重新分析")
            return self.store.confirm(owner, body["draft_id"], body["note"], snapshot.meta["fingerprint"])
        raise HTTPError(404, "接口不存在")


class Handler(BaseHTTPRequestHandler):
    server_version = "FumaiAgent"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        pass  # Audit is structured SQLite; no request bodies, auth or URLs in stdout.

    def reply(self, status, document):
        raw = json.dumps(document, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def stream_task(self, app, task_id):
        # Authenticate and check ownership before committing SSE response headers.
        owner = app.identity(self.headers)
        app.limit(self.headers.get("X-Real-IP") or self.client_address[0])
        task = app.tasks.get(owner, task_id)
        if not app.stream_slots.acquire(blocking=False):
            raise HTTPError(503, "进度连接繁忙，请使用任务查询接口")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.close_connection = True
            deadline = time.monotonic() + 60
            while True:
                event = "done" if task["status"] in TERMINAL else "progress"
                data = json.dumps(task, ensure_ascii=False, allow_nan=False)
                self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
                self.wfile.flush()
                if event == "done" or time.monotonic() >= deadline:
                    return
                time.sleep(1)
                task = app.tasks.get(owner, task_id)
        except Exception:
            # Headers are already sent: never append a second HTTP response.
            # The client falls back to polling, and the worker keeps running.
            return
        finally:
            app.stream_slots.release()

    def handle_request(self):
        request_id = str(uuid.uuid4())
        try:
            app = self.server.app
            path = urlsplit(self.path).path
            if self.command == "GET" and path.startswith("/agent-api/analysis-tasks/") and path.endswith("/events"):
                self.stream_task(app, path[len("/agent-api/analysis-tasks/"):-len("/events")])
                return
            if self.command == "GET" and not path.startswith("/agent-api/") and app.static_root:
                # Development only. nginx serves static assets in deployment.
                allowed = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/intent.js": "intent.js", "/agent.js": "agent.js", "/agent.css": "agent.css", "/data.js": "data.js", "/styles.css": "styles.css", "/favicon.svg": "favicon.svg"}
                if path not in allowed:
                    raise HTTPError(404, "页面不存在")
                file = app.static_root / allowed[path]
                content = file.read_bytes()
                self.send_response(200)
                ext = file.suffix
                self.send_header("Content-Type", {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}[ext]+"; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
                return
            body = {}
            if self.command == "POST":
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise HTTPError(415, "只接受 JSON")
                if self.headers.get("Transfer-Encoding"):
                    raise HTTPError(400, "不支持流式请求体")
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 16384:
                    raise HTTPError(413, "请求体必须在 16 KiB 以内")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise InputError("JSON 必须为对象")
            document = app.dispatch(self.command, path, self.headers, body, self.headers.get("X-Real-IP") or self.client_address[0])
            self.reply(200, document)
        except HTTPError as exc:
            self.reply(exc.status, {"error": exc.message, "request_id": request_id})
        except (InputError, ValueError, TypeError, KeyError) as exc:
            message = str(exc) if isinstance(exc, InputError) else "请求参数无效"
            self.reply(400, {"error": message, "request_id": request_id})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (sqlite3.Error, OSError):
            self.reply(503, {"error": "审计存储暂不可用，请联系管理员", "request_id": request_id})
        except Exception:
            self.reply(500, {"error": "服务异常，请凭请求编号联系管理员", "request_id": request_id})

    do_GET = handle_request
    do_POST = handle_request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--static-root", help="Only for local development")
    args = parser.parse_args()
    snapshot = Snapshot.load(args.snapshot, os.getenv("AGENT_MODE", "demo"))
    model = configured_model()
    marker = Path(".agent-release")
    release = os.getenv("AGENT_RELEASE") or (marker.read_text().strip() if marker.is_file() else "development")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    # Bind before recovering jobs: an accidental duplicate launch on this port
    # must not mark the active process's jobs interrupted. One service per DB.
    try:
        server.app = Application(snapshot, Store(args.database), os.environ["AGENT_ORIGIN"], json.loads(os.getenv("AGENT_USERS_JSON", "{}")), model,
                                 release, args.static_root)
        print(f"Fumai agent listening on loopback:{args.port}, mode={snapshot.mode}", flush=True)
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
