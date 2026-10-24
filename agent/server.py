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
from .knowledge import guard_knowledge, Knowledge
from .mcp import MCPServer, MCPClient, VERSION


class HTTPError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Application:
    def __init__(self, snapshot, store, origin, users=None, model=None, release="development", static_root=None, mcp_endpoint=None):
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
        self.knowledge=Knowledge(store)
        self.mcp=MCPServer(snapshot,self.knowledge)
        self.mcp_endpoint=mcp_endpoint
        self.tasks = AnalysisTasks(store, self.slots, self.analyze)
        self.conversation = Conversation(snapshot,store)

    def knowledge_owner(self,owner):
        return 'site:'+self.snapshot.meta['site_id'] if self.snapshot.mode=='live' else owner

    def tool_client(self,owner):
        headers={'Origin':self.origin,'X-Forwarded-Proto':'https'}
        if self.snapshot.mode=='demo' and owner.startswith(('demo:','browser:')):
            headers['X-Fumai-Session']=str(uuid.uuid4()) if owner.startswith('browser:') else owner.removeprefix('demo:')
            if owner.startswith('browser:'):headers['X-Fumai-Client']=owner.removeprefix('browser:')
        else:
            for token,user in self.users.items():
                if user['id']==owner:headers['Authorization']='Bearer '+token;break
            else:raise InputError('当前身份无法连接 MCP。')
        return MCPClient(self.mcp_endpoint,headers,transport=None if self.mcp_endpoint else lambda body:self.mcp.handle(self.knowledge_owner(owner),body))

    def rag_query(self,owner,body):
        message=body['message']
        conversation_id=body.get('_conversation_id') or self.store.default_conversation(owner)
        turns=self.store.turns(owner,conversation_id,limit=2)
        if turns and turns[-1].get('reply_mode')=='rag' and re.match(r'^(那|这|它|如果|怎么|为什么|还有|继续|依据)',message):
            return (message+' '+turns[-1].get('knowledge_question',turns[-1]['question']))[:2000]
        return message

    def uses_rag(self,owner,body):
        scope=body.get('scope','auto')
        if scope not in {'auto','knowledge','events'}:raise InputError('请选择综合分析、知识库或事件分析。')
        if scope=='knowledge':return True
        if scope=='events':return False
        if body.get('event_id') or body.get('device_id') or self.conversation.needs_model(owner,body) and any(kind=='maintenance' for _,kind in self.conversation.routes(owner,body)):
            return False
        return bool(self.knowledge.search(self.knowledge_owner(owner),self.rag_query(owner,body),1)['chunks'])

    def analyze(self,owner,body,progress=None):
        if self.uses_rag(owner,body):return self.rag_analyze(owner,body,progress)
        return self.legacy_analyze(owner,body,progress)

    def model_enrich(self,owner,message,result,progress=None):
        tools=self.tool_client(owner)
        if result.get('allow_tools',True):
            evidence=tools.tool('search_knowledge',{'query':message,'limit':5})
            result['knowledge_sources']=evidence['chunks']
            result['knowledge_notice']=evidence['notice']
            result.setdefault('trace',[]).append({'tool':'search_knowledge','transport':'mcp','status':'ok','result_count':len(evidence['chunks'])})
        result=self.model.enrich(tools,message,result,**({'progress':progress} if progress else {}))
        result['mcp_status']='connected'
        result.setdefault('trace',[]).append({'tool':'mcp_initialize','status':'ok','result_count':len(tools.tools)})
        for t in result['trace']:
            if t['tool'] in {t['name'] for t in tools.tools}:t['transport']='mcp'
        return result

    def rag_analyze(self, owner, body, progress=None):
        if progress:progress('evidence')
        scope=body.get('scope','auto')
        conversation_id=body.get('_conversation_id') or self.store.default_conversation(owner)
        if scope not in {'auto','knowledge','events'}:raise InputError('请选择综合分析、知识库或事件分析。')
        result = self.snapshot.analyze(body.get('message'), body.get('event_id'), body.get('device_id'))
        result.update(knowledge_sources=[],retrieval='bm25',mcp_status='connecting')
        if scope=='knowledge' or not body.get('event_id') and not body.get('device_id'):
            result.update(events=[],evidence_ids=[],hypotheses=[],checks=[],trace=[])
        result.update(response_type='answer',intent='knowledge',presentation='question_answer',reply_mode='rag',allow_tools=False,questions=[{'question_id':'q1','question':body['message']}],knowledge_question=body['message'])
        result['conversation_context']=[context_excerpt(r) for r in self.store.turns(owner,conversation_id,limit=6)]
        tools=None
        try:
            if progress:progress('mcp_connect')
            tools=self.tool_client(owner)
            result['mcp_status']='connected'
            result['trace'].append({'tool':'mcp_initialize','status':'ok','result_count':len(tools.tools)})
            if scope!='events':
                if progress:progress('retrieving_knowledge')
                evidence=tools.tool('search_knowledge',{'query':self.rag_query(owner,body),'limit':5})
                result['knowledge_sources']=evidence['chunks']
                result['trace'].append({'tool':'search_knowledge','transport':'mcp','status':'ok','result_count':len(evidence['chunks'])})
                result['knowledge_notice']=evidence['notice']
                if not result['events']:result['summary']='已检索到 '+str(len(evidence['chunks']))+' 段相关资料，请对照原文出处核查。' if evidence['chunks'] else evidence['notice']
        except Exception:
            result['mcp_status']='failed'
            result['knowledge_notice']='知识工具连接失败，本次未完成知识库检索，请稍后重试。'
            result['unknowns'].insert(0,result['knowledge_notice'])
        if self.model and tools and result['knowledge_sources']:
            try:
                if progress:progress('model')
                result = self.model.enrich(tools, body['message'], copy.deepcopy(result), **({'progress':progress} if progress else {}))
                for trace in result['trace']:
                    if trace.get('tool') in {t['name'] for t in tools.tools}:trace['transport']='mcp'
            except Exception as error:
                result['model_status'] = 'failed_fallback'
                result['model_error_code']=type(error).__name__
                result['trace'].append({'tool':'qwen_generate','status':'failed','result_count':0,'error_code':type(error).__name__})
                result['unknowns'].insert(0, '模型请求失败或输出未通过校验，已保留检索原文及规则分析；可稍后重试。')
        elif self.model and not tools:
            result['model_status']='failed_fallback'
        if progress:progress('saving')
        answer=result.get('model_analysis',{}).get('summary') or result['summary']
        result['summary']=answer
        result['answers']=[{'question_id':'q1','question':body['message'],'answer':answer,'status':'answered' if result['knowledge_sources'] else 'unknown','intent':'knowledge','evidence_ids':result.get('model_analysis',{}).get('evidence_ids',[])}]
        return self.store.save_run(owner, body['message'], result,conversation_id)

    def legacy_analyze(self, owner, body, progress=None):
        conversation_id=body.get('_conversation_id') or self.store.default_conversation(owner)
        fresh_analysis=any(kind=='maintenance' for _,kind in self.conversation.routes(owner,body))
        if not fresh_analysis:
            if progress:
                progress("answering")
            result=self.conversation.direct(owner,body,self.model)
            if self.model and self.conversation.can_enrich_reply(owner,body) and (result.get('events') or result.get('knowledge_question_ids')):
                try:
                    bundle={**copy.deepcopy(result),'allow_tools':False,'hypotheses':[],'checks':[],'unknowns':[],
                            'questions':[{'question_id':a['question_id'],'question':a['question']} for a in result['answers']],
                            'conversation_context':[context_excerpt(r) for r in self.store.turns(owner,conversation_id,limit=8)]}
                    # Do not anchor the model on the deterministic fallback answer.
                    bundle.pop('answers',None)
                    bundle['summary']='请直接回答当前追问的具体问题；已有观测是引用材料，不是要重复输出的报告。'
                    bundle['reply_requirements']={'mode':'followup','no_new_diagnosis':True,'do_not_repeat_previous_reply':True}
                    knowledge_ids=result.get('knowledge_question_ids',[])
                    if knowledge_ids:
                        bundle={'events':[],'evidence_ids':[],'trace':[],'allow_tools':False,'reply_mode':'knowledge',
                                'questions':[{'question_id':a['question_id'],'question':a['question']} for a in result['answers'] if a['question_id'] in knowledge_ids],
                                'knowledge_notes':result['knowledge_notes'],
                                'conversation_context':[context_excerpt(r) for r in self.store.turns(owner,conversation_id,limit=8) if r.get('knowledge_topic') or r.get('user_feedback')]}
                    enriched=self.model_enrich(owner,body['message'],bundle,progress=progress) if progress else self.model_enrich(owner,body['message'],bundle)
                    if enriched.get('model_analysis'):
                        enriched['model_analysis']['hypotheses']=[]  # No model-generated cause list is published.
                    if knowledge_ids:guard_knowledge(enriched.get('model_analysis',{}),result['knowledge_topic'])
                    else:guard_model(enriched.get('model_analysis'),result['events'])
                    analysis=enriched['model_analysis'];answers={a['question_id']:a for a in analysis.get('answers',[])}
                    for entry in result['answers']:
                        if entry['question_id'] in knowledge_ids or not knowledge_ids and entry['intent']=='followup' and not re.search(r'通信|缺失|排序|首位|第一|原因',entry['question']):
                            answer=answers.get(entry['question_id'])
                            if answer:
                                entry['answer']=answer['answer']
                                if entry['question_id'] in knowledge_ids:entry['status']='answered'
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
                    result = self.model_enrich(owner, body["message"], copy.deepcopy(result), progress=progress)
                else:
                    result = self.model_enrich(owner, body["message"], copy.deepcopy(result))
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
                    "device_control": False,"knowledge_retrieval":"bm25","mcp_protocol":VERSION,"mcp_tools":5}
        write = method == "POST"
        kb_write=path.startswith('/agent-api/knowledge/') and path!='/agent-api/knowledge/search'
        owner = self.identity(headers, write and (path in {'/agent-api/drafts','/agent-api/confirm'} or kb_write))
        if write and self.snapshot.mode == "demo" and headers.get("Origin") != self.origin and path!="/agent-api/mcp":
            raise HTTPError(403, "提交需要同源页面")
        if path=="/agent-api/mcp":
            self.limit(client+':mcp',180)
            if method!='POST':raise HTTPError(405,'此 MCP 服务使用 POST JSON 响应，不提供独立 SSE 通道。')
            if headers.get('MCP-Protocol-Version') not in {None,VERSION}:raise HTTPError(400,'不支持的 MCP 协议版本。')
            accept=headers.get('Accept','')
            if 'application/json' not in accept or 'text/event-stream' not in accept:raise HTTPError(406,'MCP 客户端需接受 application/json 和 text/event-stream。')
            return self.mcp.handle(self.knowledge_owner(owner),body)
        if method == "GET" and (path.startswith("/agent-api/analysis-tasks/") or path.startswith("/agent-api/knowledge/documents/")):
            self.limit(client + ":task-poll", 120)
        elif path in {'/agent-api/bootstrap','/agent-api/history','/agent-api/conversations','/agent-api/run'}:
            self.limit(client+':history',120)
        else:
            self.limit(client,30)
        snapshot = self.snapshot
        kb_owner=self.knowledge_owner(owner)
        if path=='/agent-api/tools' and method=='GET':
            client_tools=self.tool_client(owner)
            return {'status':'connected','protocol':VERSION,'endpoint':'/agent-api/mcp','tools':client_tools.tools}
        if path=='/agent-api/knowledge/documents':
            if method=='GET':return {'documents':self.knowledge.list(kb_owner),'retrieval':'bm25','formats':['pdf','docx','txt','md'],'max_file_mb':6}
            if method=='POST':return self.knowledge.upload(kb_owner,body)
        if path=='/agent-api/knowledge/search' and method=='POST':
            return self.knowledge.search(kb_owner,body.get('query'),body.get('limit',5))
        if path.startswith('/agent-api/knowledge/documents/'):
            parts=path.removeprefix('/agent-api/knowledge/documents/').split('/')
            doc_id=parts[0]
            if len(parts)==1 and method=='GET':return self.knowledge.get(kb_owner,doc_id)
            if len(parts)==2:
                action=parts[1]
                if method=='GET' and action=='preview':return self.knowledge.preview(kb_owner,doc_id)
                if method=='POST' and action=='retry':return self.knowledge.retry(kb_owner,doc_id)
                if method=='POST' and action=='delete':return self.knowledge.delete(kb_owner,doc_id)
        conversation_id=headers.get('X-Fumai-Session') or self.store.default_conversation(owner)
        conversation_id=self.store.ensure_conversation(owner,conversation_id)
        if method == "GET" and path == "/agent-api/bootstrap":
            return {"metadata": snapshot.meta, "mode": snapshot.mode, "model_configured": self.model is not None,
                    "stale": snapshot.stale, "devices": list(snapshot.devices.values()), "events": snapshot.list_events(limit=12)["events"],
                    "history": self.store.history(owner,analysis_only=True), "device_control": False, "release": self.release,"knowledge":self.knowledge.list(kb_owner),"mcp_tools":5,
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
            if set(body) - {"message", "event_id", "device_id",'response_mode','scope'}:
                raise InputError("未知分析参数")
            if body.get('response_mode','auto') not in {'auto','report','chat'}:raise InputError('未知回答方式')
            body={**body,'_conversation_id':conversation_id}
            if not self.uses_rag(owner,body) and not self.conversation.needs_model(owner,body):
                return self.analyze(owner, body)
            if not self.slots.acquire(blocking=False):
                raise HTTPError(503, "模型正在处理其他任务，请稍后重试")
            try:
                return self.analyze(owner, body)
            finally:
                self.slots.release()
        if method == "POST" and path == "/agent-api/analysis-tasks":
            if set(body) - {"message", "event_id", "device_id", "request_id",'response_mode','scope'}:
                raise InputError("未知分析参数")
            if body.get('response_mode','auto') not in {'auto','report','chat'}:raise InputError('未知回答方式')
            body={**body,'_conversation_id':conversation_id}
            maintenance = self.uses_rag(owner,body) or self.conversation.needs_model(owner,body)
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

    def stream_task(self, app, task_id, knowledge=False):
        # Authenticate and check ownership before committing SSE response headers.
        owner = app.identity(self.headers)
        app.limit(self.headers.get("X-Real-IP") or self.client_address[0])
        get_task=(lambda: app.knowledge.get(app.knowledge_owner(owner),task_id)) if knowledge else (lambda: app.tasks.get(owner,task_id))
        task = get_task()
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
                event = "done" if task["status"] in (TERMINAL | {"ready"}) else "progress"
                data = json.dumps(task, ensure_ascii=False, allow_nan=False)
                self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
                self.wfile.flush()
                if event == "done" or time.monotonic() >= deadline:
                    return
                time.sleep(1)
                task = get_task()
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
            if self.command=='GET' and path.startswith('/agent-api/knowledge/documents/') and path.endswith('/events'):
                self.stream_task(app,path[len('/agent-api/knowledge/documents/'):-len('/events')],knowledge=True)
                return
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
                if not 1 <= length <= (9*1024*1024 if path=="/agent-api/knowledge/documents" else 16384):
                    raise HTTPError(413, "资料上传上限为 6 MB，普通请求上限为 16 KiB。")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise InputError("JSON 必须为对象")
            document = app.dispatch(self.command, path, self.headers, body, self.headers.get("X-Real-IP") or self.client_address[0])
            if document is None and path=='/agent-api/mcp':
                self.send_response(202);self.send_header('Content-Length','0');self.end_headers()
            else:self.reply(200, document)
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
                                 release, args.static_root,mcp_endpoint=f"http://127.0.0.1:{args.port}/agent-api/mcp")
        print(f"Fumai agent listening on loopback:{args.port}, mode={snapshot.mode}", flush=True)
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
