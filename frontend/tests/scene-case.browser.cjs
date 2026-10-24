const {chromium}=require(process.env.FUMAI_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const base=process.env.FUMAI_CHECK_URL||'http://127.0.0.1:4198',out=process.env.FUMAI_TEST_OUTPUT||path.resolve('outputs');fs.mkdirSync(out,{recursive:true});
(async()=>{
    const browser=await chromium.launch({headless:true,...(process.env.FUMAI_BROWSER_EXECUTABLE?{executablePath:process.env.FUMAI_BROWSER_EXECUTABLE}:{}),args:['--enable-unsafe-swiftshader']});
    const page=await browser.newPage({viewport:{width:1600,height:1050},reducedMotion:'reduce'}),errors=[],writes=[],checks=[];
    const local=new URL(base).hostname==='127.0.0.1';let registered=false;
    const auth=async p=>p.route('**/api/auth/me',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({user:{username:'local_case_test',display_name:'本地测试',role:'viewer'}})}));
    if(local)await auth(page);
    else if(process.env.FUMAI_SCENE_REGISTER_VIEWER==='1'){
        const response=await page.request.post(base+'/api/auth/register',{headers:{Origin:base},data:{username:'caseqa_'+crypto.randomBytes(5).toString('hex'),password:crypto.randomBytes(24).toString('base64url'),email:'case-qa@example.invalid',display_name:'案例验收测试',role:'viewer'}});
        assert.equal(response.status(),201);registered=true;
    }
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(!['GET','HEAD','OPTIONS'].includes(request.method()))writes.push(new URL(request.url()).pathname);});
    const ready=()=>page.waitForFunction(()=>document.querySelector('#scene-workbench')?.dataset.sceneReady==='webgl');
    const step=async number=>assert.match(await page.locator('.scene-case-heading [aria-current=step]').textContent(),new RegExp('^'+number));
    try{
        await page.goto(base+'/#scene');await ready();
        await page.locator('[data-case-start=single]').click();await step(1);
        await page.locator('[data-case-action=locate]').click();assert.match(await page.locator('.scene-case-feedback').textContent(),/请在场景/);
        await page.locator('.scene-assets [data-scene-select=inv_01]').click();await page.locator('[data-case-action=locate]').click();await step(1);
        await page.locator('.scene-assets [data-scene-select=inv_07]').click();await page.locator('[data-case-action=locate]').click();await step(2);
        await page.locator('[data-case-value=single]').click();await step(2);assert.match(await page.locator('.scene-case-feedback').textContent(),/先展开/);
        await page.locator('[data-case-show-evidence]').click();assert.match(await page.locator('.scene-case-facts').textContent(),/9.4 \/ 27.8/);
        await page.locator('[data-case-value=communication]').click();assert.match(await page.locator('.scene-case-feedback').textContent(),/尚无通信丢包/);
        await page.screenshot({path:path.join(out,'fumai-case-evidence-desktop.png')});
        await page.locator('[data-case-value=single]').click();await step(3);
        await page.locator('[data-case-value=needs_investigation]').click();assert.match(await page.locator('.scene-case-feedback').textContent(),/至少 8/);
        const note='核对本条事件的同群观测及现场告警、限发和检修记录。';await page.locator('#scene-case-note').fill(note);
        await page.locator('[data-case-value=confirmed_fault]').click();await step(3);assert.equal(await page.locator('#scene-case-note').inputValue(),note);
        await page.locator('[data-case-value=needs_investigation]').click();await step(4);
        const downloadPromise=page.waitForEvent('download');await page.locator('[data-case-export]').click();const download=await downloadPromise;
        const report=JSON.parse(fs.readFileSync(await download.path(),'utf8'));assert.equal(report.data_kind,'synthetic_demo');assert.equal(report.review.submitted_to_backend,false);assert.equal(report.review.note,note);assert.equal(report.event.event_id,'DEMO-2107-001');assert.equal(report.attempts.filter(a=>!a.correct).length,4);
        fs.writeFileSync(path.join(out,'fumai-case-export-sample.json'),JSON.stringify(report,null,2));checks.push('single case: explicit location, evidence gating, unsupported diagnosis blocked, note preserved, local export');
        await page.locator('[data-case-exit]').click();await page.locator('[data-case-start=quality]').click();await page.locator('[data-scene-search]').fill('inv_22');await page.locator('.scene-assets [data-scene-select=inv_22]').click();await page.locator('[data-case-action=locate]').click();await page.locator('[data-case-show-evidence]').click();assert.match(await page.locator('.scene-case-facts').textContent(),/3 台 \/ 0 点/);
        await page.locator('[data-case-value=single]').click();await step(2);assert.match(await page.locator('.scene-case-feedback').textContent(),/比较依据不足/);
        await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'fumai-case-mobile.png'),fullPage:true});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.setViewportSize({width:320,height:720});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
        await page.locator('[data-case-value=insufficient]').click();await step(3);await page.locator('#scene-case-note').fill('有效同群不足，先补齐可比较观测再核查。');await page.locator('[data-case-value=needs_investigation]').click();await step(4);checks.push('insufficient peers: separate evidence and decision, 390/320px usable');
        await page.locator('[data-case-start=quality]').click();await step(1);assert.match(await page.locator('.scene-case-pick').textContent(),/尚未选择/);
        const fixture=await(await page.request.get(base+'/models/meshopt-sample.json')).text();await page.locator('[data-scene-import]').setInputFiles({name:'sample.json',mimeType:'application/json',buffer:Buffer.from(fixture)});await page.waitForSelector('[data-case-start][disabled]');assert.equal(await page.locator('[data-case-action]').count(),0);checks.push('restart clears decisions; importing layout ends and disables built-in walkthrough');
        await page.evaluate(()=>location.hash='overview');await page.waitForSelector('#main[data-view=overview]');await page.evaluate(()=>location.hash='scene');await ready();assert.equal(await page.locator('[data-case-start]:not([disabled])').count(),2);checks.push('route exit clears rehearsal state and restores demo on return');
        const fallback=await browser.newPage({viewport:{width:1200,height:900},storageState:await page.context().storageState()});if(local)await auth(fallback);
        await fallback.addInitScript(()=>{const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return /webgl/.test(type)?null:get.call(this,type,...args);};});
        await fallback.goto(base+'/#scene');await fallback.waitForSelector('#scene-workbench[data-scene-ready=fallback]');await fallback.locator('[data-case-start=single]').click();await fallback.locator('.scene-assets [data-scene-select=inv_07]').click();await fallback.locator('[data-case-action=locate]').click();assert.match(await fallback.locator('.scene-case-heading [aria-current=step]').textContent(),/^2/);await fallback.close();checks.push('without WebGL, equipment index can complete location step');
        assert.deepEqual(writes,[],'Rehearsal must not issue business writes or model requests');assert.deepEqual(errors,[]);
        const result={base,checks,pageErrors:errors,businessWrites:writes,exportDataKind:report.data_kind};fs.writeFileSync(path.join(out,'fumai-case-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
    }finally{if(registered)await page.request.post(base+'/api/auth/logout',{headers:{Origin:base}}).catch(()=>{});await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
