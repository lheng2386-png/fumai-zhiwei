import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {createServer} from 'node:net';
import {mkdtempSync,rmSync,writeFileSync,readFileSync,statSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {once} from 'node:events';

test('真实 HTTP：身份权限、模型专用凭证、接收回执、日志查询与模型回传工具',async()=>{
  const socket=createServer();socket.listen(0,'127.0.0.1');await once(socket,'listening');const port=socket.address().port;await new Promise(r=>socket.close(r));
  const directory=mkdtempSync(join(tmpdir(),'fumai-ingestion-'));
  const origin=`http://127.0.0.1:${port}`;const token='isolated-test-ingestion-token-0000000000';
  const child=spawn(process.execPath,['src/server.mjs'],{cwd:process.cwd(),env:{...process.env,FUMAI_DB_PATH:join(directory,'db.sqlite'),FUMAI_NOTIFICATION_PORT:String(port),FUMAI_PUBLIC_ORIGIN:origin,FUMAI_BOOTSTRAP_ADMIN_USERNAME:'testadmin',FUMAI_BOOTSTRAP_ADMIN_PASSWORD:'TestingOnlyPassword_2026!',FUMAI_EMAIL_PROVIDER:'mock',FUMAI_SMS_PROVIDER:'mock',FUMAI_MODEL_INGEST_TOKEN:token},stdio:['ignore','ignore','pipe']});
  let errors='';child.stderr.on('data',chunk=>errors+=chunk);
  try {
    let ready=false;
    for(let i=0;i<80;i++){try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,50));}
    assert.equal(ready,true,errors);
    const request=(path,value,headers={})=>fetch(origin+path,{method:value?'POST':'GET',headers:{...(value?{'Content-Type':'application/json'}:{}),...headers},...(value?{body:JSON.stringify(value)}:{})});
    assert.equal((await request('/api/monitoring/logs')).status,401);
    let response=await request('/api/auth/login',{username:'testadmin',password:'TestingOnlyPassword_2026!'});assert.equal(response.status,200);
    const cookie=response.headers.get('set-cookie').split(';')[0];const admin={Cookie:cookie};
    response=await request('/api/equipment',{id:'TEST-A',plant_id:'plant',name:'联调设备'},admin);assert.equal(response.status,200);
    const payload={request_id:'http-r1',schema_version:'competition-inference-v1',evidence:[{evidence_id:'http-e1',plant_id:'plant',asset_id:'TEST-A',observation_id:'obs',observed_at:'2026-10-20T00:00:00Z',task:'timeseries',model_version:'model-v1',threshold_version:'t1',decision:'anomaly',quality:'ready',data_kind:'synthetic_demo',score:.9}]};
    assert.equal((await request('/api/model-ingestions',payload)).status,401);
    assert.equal((await request('/api/model-ingestions',payload,{Authorization:'Bearer wrong'})).status,401);
    response=await request('/api/model-ingestions',payload,{Authorization:'Bearer '+token});assert.equal(response.status,201);assert.equal((await response.json()).accepted,1);
    response=await request('/api/model-ingestions',payload,{Authorization:'Bearer '+token});assert.equal(response.status,200);assert.equal((await response.json()).replayed,true);
    assert.equal((await request('/api/monitoring/logs',null,{Authorization:'Bearer '+token})).status,401,'接入凭证不能读取其他业务接口');
    response=await request('/api/monitoring/logs?equipment_id=TEST-A',null,admin);const logs=await response.json();assert.equal(logs.total,1);
    response=await request('/api/monitoring/logs/'+logs.logs[0].id,null,admin);assert.equal((await response.json()).raw_result.task,'timeseries');
    response=await request('/api/model-ingestions/http-r1',null,{Authorization:'Bearer '+token});assert.equal((await response.json()).accepted,1);
    response=await request('/api/auth/register',{username:'viewerqa',display_name:'只读测试',password:'TestingOnlyPassword_2026!',role:'viewer'});assert.equal(response.status,201);const viewer={Cookie:response.headers.get('set-cookie').split(';')[0]};
    assert.equal((await request('/api/monitoring/logs',null,viewer)).status,200);
    assert.equal((await request('/api/model-ingestions',{...payload,request_id:'blocked-viewer'},viewer)).status,403);
    assert.equal((await request('/api/model-asset-mappings',{plant_id:'plant',asset_id:'alias',equipment_id:'TEST-A'},viewer)).status,403);
    assert.equal((await request('/api/model-asset-mappings',{plant_id:'plant',asset_id:'alias',equipment_id:'TEST-A'},admin)).status,200);
    const report=join(directory,'report.json'),receipt=join(directory,'receipt.json');
    const cliPayload={...payload,evidence:[{...payload.evidence[0],evidence_id:'cli-evidence'}]};delete cliPayload.request_id;
    writeFileSync(report,JSON.stringify(cliPayload));
    const cliArgs=['integrations/deliver_model_result.py','--input',report,'--receipt',receipt,'--endpoint',origin+'/api/model-ingestions'];
    let cli=spawnSync('python3',cliArgs,{env:{...process.env,FUMAI_MODEL_INGEST_TOKEN:token},encoding:'utf8'});
    assert.equal(cli.status,0,cli.stdout+cli.stderr);assert.equal(JSON.parse(readFileSync(receipt)).accepted,1);
    assert.equal(statSync(receipt).mode&0o777,0o600);
    const repeatedReceipt=join(directory,'receipt-repeat.json');cliArgs[4]=repeatedReceipt;
    cli=spawnSync('python3',cliArgs,{env:{...process.env,FUMAI_MODEL_INGEST_TOKEN:token},encoding:'utf8'});
    assert.equal(cli.status,0,cli.stdout+cli.stderr);assert.equal(JSON.parse(readFileSync(repeatedReceipt)).replayed,true);
    assert.equal((await (await request('/api/monitoring/logs',null,admin)).json()).total,2);
  } finally {child.kill('SIGTERM');if(child.exitCode===null)await once(child,'exit');rmSync(directory,{recursive:true,force:true});}
});
