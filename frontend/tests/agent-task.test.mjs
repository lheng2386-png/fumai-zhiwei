import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { webcrypto } from 'node:crypto';
import { test } from 'node:test';
import vm from 'node:vm';

const source=await readFile(new URL('../dist/agent.js',import.meta.url),'utf8');
const storageKey='fumai-agent-active-task';
const requestId='6c88bb5e-72fb-4d49-83dd-7d4ef6179280';
const result={run_id:'run-1',created_at:'2026-10-19T01:00:00Z',summary:'证据核查完成。',boundary:'需要人工复核',hypotheses:[],checks:['核对现场记录'],unknowns:[],events:[],trace:[],metadata:{source:'constructed_demo'},model_status:'connected'};
const snapshot=(status='running',extra={})=>({task_id:'task-1',status,stage:status==='running'?'model_analysis':status,created_at:'2026-10-19T01:00:00Z',elapsed_seconds:5,question:'分析设备',...extra});
const json=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
const frame=(type,data)=>`event: ${type}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
const flush=async()=>{for(let i=0;i<30;i++) await Promise.resolve();};

async function harness(handler,{saved,token}={}) {
    let now=Date.parse('2026-10-19T01:00:00Z'),nextTimer=0,paints=0;
    const storage=new Map(saved?[[storageKey,JSON.stringify(saved)]]:[]),timers=new Map(),intervals=new Map(),listeners={},calls=[];
    const nodes=Object.fromEntries(['elapsed','stage','transport'].map(key=>[key,{textContent:''}]));
    const element=()=>({open:false,_html:'',set innerHTML(value){this._html=value;},get innerHTML(){return this._html;},setAttribute(){},addEventListener(){},querySelector(){return null;},showModal(){this.open=true;},close(){this.open=false;},focus(){}});
    const workbench=element();
    Object.defineProperty(workbench,'innerHTML',{set(value){this._html=value;paints++;},get(){return this._html;}});
    const document={body:{append(){}},createElement:element,querySelector:selector=>selector==='#agent-workbench'?workbench:null,querySelectorAll:selector=>{
        const match=selector.match(/^\[data-agent-(elapsed|stage|transport)\]$/);return match?[nodes[match[1]]]:[];
    },addEventListener:(name,fn)=>{listeners[name]=fn;}};
    const context={document,window:{FumaiDemo:{events:[]}},sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},crypto:webcrypto,Uint8Array,AbortController,TextDecoder,location:{protocol:'https:',hostname:'example.test'},Date:class extends Date{static now(){return now;}},setTimeout:(fn,ms)=>{timers.set(++nextTimer,{fn,ms});return nextTimer;},clearTimeout:id=>timers.delete(id),setInterval:(fn,ms)=>{intervals.set(++nextTimer,{fn,ms});return nextTimer;},clearInterval:id=>intervals.delete(id),fetch:async(url,options)=>{
        calls.push({url,options});
        if(url.endsWith('/bootstrap')) return json({mode:'demo',history:[],events:[],model_configured:true});
        return handler(url,options,{storage,calls});
    }};
    // Auth is entered through the actual form submit path; this exercises header propagation.
    context.FormData=class {get(name){return name==='token'?token:null;}};
    vm.runInNewContext(source,context,{filename:'agent.js'});await flush();
    return {storage,calls,nodes,workbench,get paints(){return paints;},get clockCount(){return intervals.size;},async advance(ms){now+=ms;for(const {fn} of intervals.values())fn();await flush();},async timer(ms){const entry=[...timers].find(([,t])=>t.ms===ms);assert.ok(entry,`Expected ${ms} ms timer`);timers.delete(entry[0]);entry[1].fn();await flush();},async prompt(text='分析设备'){await listeners.click({target:{closest:selector=>selector==='[data-agent-prompt]'?{dataset:{agentPrompt:text}}:null}});await flush();},async login(){await listeners.submit({preventDefault(){},target:{id:'agent-login',matches:()=>false,reset(){},querySelector:()=>({})}});await flush();},async scope(value){await listeners.change({target:{id:'agent-analysis-scope',value,matches:()=>false}});await flush();},async action(action){await listeners.click({target:{closest:selector=>selector==='[data-agent-action]'?{dataset:{agentAction:action}}:null}});await flush();}};
}
function streamResponse(signal) {
    let controller;
    const stream=new ReadableStream({start(c){controller=c;signal.addEventListener('abort',()=>{try{c.error(new DOMException('Aborted','AbortError'));}catch{}});}});
    return {response:new Response(stream,{headers:{'Content-Type':'text/event-stream'}}),push:text=>controller.enqueue(new TextEncoder().encode(text)),bytes:bytes=>controller.enqueue(bytes),close:()=>controller.close()};
}

test('SSE preserves chunked UTF-8, displays only validated final result, and clock only touches time nodes',async()=>{
    let stream;
    const h=await harness(async(url,options,{storage})=>{
        if(url.endsWith('/analysis-tasks')) {assert.ok(JSON.parse(storage.get(storageKey)).request_id,'request ID persisted before POST');return json(snapshot('queued'));}
        if(url.endsWith('/events')) {stream=streamResponse(options.signal);return stream.response;}
        assert.fail(`Unexpected request ${url}`);
    },{token:'a'.repeat(40)});
    await h.login();await h.prompt();
    const paints=h.paints;
    await h.advance(3000);
    assert.equal(h.paints,paints,'clock must not replace input or transcript');
    assert.match(h.nodes.elapsed.textContent,/8 秒/);
    assert.ok(h.calls.filter(c=>c.url.includes('analysis-tasks')).every(c=>c.options.headers.Authorization==='Bearer '+'a'.repeat(40)));
    assert.ok(h.calls.every(c=>c.options.headers['X-Fumai-Session']));
    const progress=frame('progress',snapshot('running',{elapsed_seconds:9,result:{summary:'不可显示的草稿'}}));
    stream.push(progress.slice(0,10));stream.push(progress.slice(10));await flush();
    assert.doesNotMatch(h.workbench.innerHTML,/不可显示的草稿/);
    assert.match(h.nodes.stage.textContent,/模型正在/);
    const bytes=new TextEncoder().encode(frame('done',snapshot('completed',{result,elapsed_seconds:12})));
    // Split inside a Chinese UTF-8 character as a real network read can do.
    const split=bytes.findIndex(byte=>byte>=0xe0)+1;
    stream.bytes(bytes.slice(0,split));stream.bytes(bytes.slice(split));await flush();
    assert.match(h.workbench.innerHTML,/证据核查完成/);
    assert.match(h.workbench.innerHTML,/用时 12 秒/);
    assert.equal(h.storage.has(storageKey),false);
    assert.equal(h.clockCount,0);
});

test('lost POST response retries same request ID; SSE disconnect falls back to GET without duplicate analysis',async()=>{
    let posts=0,gets=0;
    const ids=[];
    const h=await harness(async(url,options)=>{
        if(url.endsWith('/analysis-tasks')) {posts++;ids.push(JSON.parse(options.body).request_id);if(posts===1)throw new TypeError('network');return json(snapshot());}
        if(url.endsWith('/events')) return new Response('',{headers:{'Content-Type':'text/event-stream'}});
        if(url.endsWith('/task-1')) {gets++;if(gets===1)throw new TypeError('offline');return json(snapshot('completed',{result}));}
        assert.fail(`Unexpected request ${url}`);
    });
    await h.prompt();assert.match(h.nodes.transport.textContent,/同一任务标识/);
    await h.timer(2000);await h.timer(2000);
    assert.match(h.nodes.transport.textContent,/自动重连|查询任务状态/);
    await h.timer(2000);
    assert.equal(posts,2);assert.equal(new Set(ids).size,1);assert.equal(gets,2);
    assert.match(h.workbench.innerHTML,/证据核查完成/);
});

test('refresh resumes saved task with zero POST; 60-second stream closes and queries persisted result',async()=>{
    let stream;
    const h=await harness(async(url,options)=>{
        if(url.endsWith('/events')) {stream=streamResponse(options.signal);return stream.response;}
        if(url.endsWith('/task-1')) return json(snapshot('completed',{result,elapsed_seconds:70}));
        assert.fail(`No new analysis expected ${url}`);
    },{saved:{request_id:requestId,task_id:'task-1',question:'刷新前的问题',elapsed_seconds:8,saved_at:Date.parse('2026-10-19T00:59:58Z')}});
    assert.ok(stream);
    await h.advance(1000);assert.match(h.nodes.elapsed.textContent,/11 秒/);
    await h.timer(60000);await h.timer(2000);
    assert.match(h.workbench.innerHTML,/刷新前的问题/);
    assert.match(h.workbench.innerHTML,/证据核查完成/);
    assert.equal(h.calls.some(c=>c.options.method==='POST'),false);
});

test('refresh before create response resubmits original request id and selection',async()=>{
    const h=await harness(async(url,options)=>{
        assert.equal(url,'/agent-api/analysis-tasks');
        assert.deepEqual(JSON.parse(options.body),{message:'已提交的问题',event_id:'event-2',request_id:requestId});
        return json(snapshot('completed',{result}));
    },{saved:{request_id:requestId,question:'已提交的问题',event_id:'event-2',elapsed_seconds:2,saved_at:Date.now()}});
    assert.match(h.workbench.innerHTML,/证据核查完成/);
    assert.equal(h.storage.has(storageKey),false);
});

test('invalid final result is not rendered; retained task can recover, while explicit task failure permits resubmission',async()=>{
    let streams=0;
    const h=await harness(async(url)=>{
        if(url.endsWith('/analysis-tasks')) return json(snapshot());
        if(url.endsWith('/events')) {streams++;return new Response(frame('done',snapshot('completed',{result:streams===1?{summary:'不完整回复'}:result})),{headers:{'Content-Type':'text/event-stream'}});}
        assert.fail(`Unexpected request ${url}`);
    });
    await h.prompt();
    assert.doesNotMatch(h.workbench.innerHTML,/不完整回复/);
    assert.match(h.workbench.innerHTML,/格式不完整/);assert.match(h.workbench.innerHTML,/恢复任务/);
    assert.equal(h.storage.has(storageKey),true);
    await h.action('resume-task');assert.match(h.workbench.innerHTML,/证据核查完成/);
    const failed=await harness(async()=>json(snapshot('failed',{error:'本次任务已停止，请重试。'})));
    await failed.prompt();assert.match(failed.workbench.innerHTML,/本次任务已停止/);
    assert.equal(failed.storage.has(storageKey),false);assert.equal(failed.clockCount,0);
});

test('auth expiry pauses without retry storm; definitive missing task releases the form',async()=>{
    const saved={request_id:requestId,task_id:'task-1',question:'需要恢复',elapsed_seconds:3,saved_at:Date.now()};
    const h=await harness(async()=>json({error:'登录已过期'},401),{saved});
    assert.match(h.workbench.innerHTML,/访问令牌/);assert.match(h.workbench.innerHTML,/恢复任务/);
    assert.equal(h.storage.has(storageKey),true);assert.equal(h.calls.length,2);
    const missing=await harness(async()=>json({error:'任务已过期，请重新分析'},404),{saved});
    assert.match(missing.workbench.innerHTML,/任务已过期/);assert.equal(missing.storage.has(storageKey),false);assert.equal(missing.clockCount,0);
});


test('knowledge scope reaches the backend and document citations render safely with MCP trace',async()=>{
    const rag={...result,evidence_ids:[],response_type:'answer',intent:'knowledge',presentation:'question_answer',mcp_status:'connected',
        answers:[{question:'查手册',answer:'按原文核对',status:'answered'}],
        knowledge_sources:[{citation_id:'KB:real',chunk_id:'real',title:'<script>bad()</script>',filename:'manual.txt',location:'第 2 页',ordinal:3,text:'原文 <img src=x onerror=bad()>',digest:'abcdef123456'}],
        model_analysis:{summary:'按原文核对',hypotheses:[],checks:[],unknowns:[],evidence_ids:['KB:real']},
        trace:[{tool:'search_knowledge',transport:'mcp',status:'ok',result_count:1}]};
    const h=await harness(async(url,options)=>{
        if(url.endsWith('/analysis-tasks')){const body=JSON.parse(options.body);assert.equal(body.scope,'knowledge');assert.equal(body.event_id,null);return json(snapshot('completed',{result:rag}));}
        assert.fail(`Unexpected request ${url}`);
    });
    await h.scope('knowledge');await h.prompt('查手册');
    assert.match(h.workbench.innerHTML,/知识库原文出处/);assert.match(h.workbench.innerHTML,/模型已引用/);
    assert.match(h.workbench.innerHTML,/第 2 页/);assert.match(h.workbench.innerHTML,/查看 MCP 工具调用记录/);
    assert.match(h.workbench.innerHTML,/&lt;script&gt;/);assert.doesNotMatch(h.workbench.innerHTML,/<script>bad|<img src=x/);
});
