const {chromium}=require(process.env.FUMAI_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.FUMAI_CHECK_URL||'http://127.0.0.1:4196';
const output=process.env.FUMAI_TEST_OUTPUT||path.resolve('outputs');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.FUMAI_BROWSER_EXECUTABLE?{executablePath:process.env.FUMAI_BROWSER_EXECUTABLE}:{})});
 const page=await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});page.setDefaultTimeout(120000);
 const errors=[],checks=[],responses=[];page.on('pageerror',e=>errors.push(e.message));
 const ask=async question=>{
  const count=await page.locator('#agent-workbench .agent-message.assistant').count();
  const pending=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/agent-api/analysis-tasks'));
  await page.locator('#workbench-question').fill(question);await page.locator('.agent-send').click();
  let task=await(await pending).json();
  await page.waitForFunction(n=>document.querySelectorAll('#agent-workbench .agent-message.assistant').length>n,count);
  await page.waitForFunction(()=>!document.querySelector('#workbench-question').disabled);
  if(!task.result){const headers=await page.evaluate(()=>({'X-Fumai-Client':localStorage.getItem('fumai-agent-client'),'X-Fumai-Session':sessionStorage.getItem('fumai-agent-session')}));task=await(await page.request.get(base+'/agent-api/analysis-tasks/'+task.task_id,{headers})).json();}
  assert.equal(task.status,'completed');responses.push(task.result);return task.result;
 };
 try{
  await page.goto(base+'/#agent',{waitUntil:'networkidle'});
  const first=await ask('光伏逆变器出现欠压，一般是什么原因？');
  assert.equal(first.intent,'knowledge');assert.equal(first.events.length,0);assert.match(first.summary,/交流/);assert.match(first.summary,/直流/);
  const reply=page.locator('.agent-message.assistant').last();
  assert.equal(await reply.locator('.agent-evidence-card').count(),0);assert.equal(await reply.locator('.agent-knowledge-sources a').count(),2);
  checks.push('general undervoltage knowledge answers directly with manufacturer references');
  const morning=await ask('那如果只在清晨出现呢？');assert.equal(morning.intent,'followup');assert.equal(morning.knowledge_topic,'undervoltage');assert.equal(morning.events.length,0);
  const basis=await ask('你这样解释的依据是什么？');assert.equal(basis.intent,'followup');assert.equal(basis.knowledge_topic,'undervoltage');assert.equal(basis.events.length,0);
  checks.push('knowledge conversation retains topic through time-condition and basis followups');
  await page.screenshot({path:path.join(output,'fumai-knowledge-conversation.png'),animations:'disabled'});
  const old=await page.evaluate(()=>sessionStorage.getItem('fumai-agent-session'));
  await page.locator('[data-agent-action=clear]').click();await page.waitForFunction(id=>sessionStorage.getItem('fumai-agent-session')!==id,old);
  await page.waitForFunction(()=>document.querySelector('.agent-chat-top>span')?.textContent==='服务在线');
  const analyzed=await ask('分析 inv_07 的异常证据');assert.equal(analyzed.response_type,'analysis');
  for(const question of ['你做出这个判断的依据是什么？','为什么这么判断？']){
   const answer=await ask(question);assert.equal(answer.intent,'followup');assert.deepEqual(answer.source_run_ids,[analyzed.run_id]);assert.ok(answer.events.length);assert.ok(!answer.summary.includes('不会触发设备证据分析'));
  }
  checks.push('two evidence followups reuse the original analysis rather than scope refusal or new diagnosis');
  await page.setViewportSize({width:390,height:844});const box=await page.locator('.agent-input-area').boundingBox();assert.ok(box&&box.y+box.height<=844);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(output,'fumai-knowledge-mobile.png'),animations:'disabled'});
  checks.push('mobile composer stays visible after knowledge and evidence conversations');
  assert.deepEqual(errors,[]);
  const result={base,checks,pageErrors:errors,responses:responses.map(r=>({question:r.question,intent:r.intent,model_status:r.model_status,topic:r.knowledge_topic,run_id:r.run_id,source_run_ids:r.source_run_ids,summary:r.summary}))};
  fs.writeFileSync(path.join(output,'fumai-knowledge-browser-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
