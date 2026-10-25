const {chromium}=require(process.env.FUMAI_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const base=process.env.FUMAI_CHECK_URL||'http://127.0.0.1:4208',out=process.env.FUMAI_TEST_OUTPUT||path.resolve('outputs');
fs.mkdirSync(out,{recursive:true});
(async()=>{
    const browser=await chromium.launch({headless:true,...(process.env.FUMAI_BROWSER_EXECUTABLE?{executablePath:process.env.FUMAI_BROWSER_EXECUTABLE}:{}),args:['--enable-unsafe-swiftshader']});
    const context=await browser.newContext({viewport:{width:1600,height:1000},reducedMotion:'reduce',hasTouch:true}),page=await context.newPage(),checks=[],errors=[];
    page.on('pageerror',e=>errors.push(e.message));let registered=false;
    if(new URL(base).hostname==='127.0.0.1')await page.route('**/api/auth/me',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({user:{username:'navigation_local',display_name:'本地测试',role:'viewer'}})}));
    else if(process.env.FUMAI_SCENE_REGISTER_VIEWER==='1'){
        const r=await page.request.post(base+'/api/auth/register',{headers:{Origin:base},data:{username:'navqa_'+crypto.randomBytes(5).toString('hex'),password:crypto.randomBytes(24).toString('base64url'),email:'navigation-qa@example.invalid',display_name:'漫游验收测试',role:'viewer'}});assert.equal(r.status(),201);registered=true;
    }
    const stats=()=>page.evaluate(()=>window.FumaiScene.diagnostics());
    const position=async()=>(await stats()).navigation.position;
    const distance=(a,b)=>Math.hypot(...a.map((v,i)=>v-b[i]));
    const full=async()=>page.waitForFunction(()=>window.FumaiScene.diagnostics().expanded);
    const move=async(code,ms=350)=>{await page.keyboard.down(code);await page.waitForTimeout(ms);await page.keyboard.up(code);await page.waitForTimeout(50);};
    try{
        await page.goto(base+'/#scene');await page.waitForFunction(()=>document.querySelector('#scene-workbench')?.dataset.sceneReady==='webgl');
        await page.locator('[data-scene-fullscreen]').click();await full();
        const bounds=await page.locator('#scene-workbench').boundingBox();assert.ok(bounds.x===0&&bounds.y===0&&Math.abs(bounds.width-1600)<2&&Math.abs(bounds.height-1000)<2);
        assert.ok(await page.locator('[data-camera=free]').isVisible());assert.ok(await page.locator('.scene-inspector').isVisible());assert.equal(await page.locator('.scene-case').isVisible(),false);checks.push('fullscreen includes toolbar and inspector and fills viewport');
        const beforeWidth=(await page.locator('.scene-viewport').boundingBox()).width;await page.locator('.scene-bar [data-scene-panel]').click();assert.ok((await page.locator('.scene-viewport').boundingBox()).width>beforeWidth);checks.push('device panel can collapse without leaving fullscreen');
        await page.locator('[data-camera=free]').click();assert.equal((await stats()).navigation.mode,'free');
        let p=await position();await move('KeyW');assert.ok(distance(p,await position())>.3);checks.push('keyboard changes station position');
        p=await position();await move('KeyD');assert.ok(distance(p,await position())>.3);
        const slow=await position();await move('KeyW');const normalDistance=distance(slow,await position());p=await position();await page.keyboard.down('Shift');await move('KeyW');await page.keyboard.up('Shift');assert.ok(distance(p,await position())>normalDistance*1.7);checks.push('strafe and accelerated movement work');
        const canvas=await page.locator('.scene-viewport canvas').boundingBox(),center={x:canvas.x+canvas.width*.55,y:canvas.y+canvas.height*.45};const yaw=(await stats()).navigation.yaw,compass=await page.locator('.scene-compass i').getAttribute('style');
        await page.mouse.move(center.x,center.y);await page.mouse.down();await page.mouse.move(center.x+100,center.y-30,{steps:5});await page.mouse.up();assert.ok(Math.abs((await stats()).navigation.yaw-yaw)>.2);assert.notEqual(await page.locator('.scene-compass i').getAttribute('style'),compass);checks.push('drag rotates heading, pitch and compass');p=await position();await page.keyboard.down('Control');await move('ArrowUp');await page.keyboard.up('Control');assert.ok(distance(p,await position())<.001);checks.push('browser modifier shortcuts do not move camera');
        await page.locator('.scene-bar [data-scene-panel]').click();await page.locator('[data-scene-search]').fill('inv_07');p=await position();await move('KeyW');assert.ok(distance(p,await position())<.001);await page.locator('[data-scene-search]').fill('');checks.push('typing does not move camera');
        p=await position();await page.locator('.scene-assets [data-scene-select=inv_07]').click();assert.ok(distance(p,await position())<.001);assert.match(await page.locator('.scene-selection').innerText(),/inv_07/);checks.push('selecting equipment in free mode preserves station position');
        await page.locator('.scene-viewport canvas').focus();await page.keyboard.down('KeyW');await page.waitForTimeout(150);await page.evaluate(()=>window.dispatchEvent(new Event('blur')));p=await position();await page.waitForTimeout(250);await page.keyboard.up('KeyW');assert.ok(distance(p,await position())<.001);checks.push('blur clears held movement');
        const button=await page.locator('[data-roam-move=forward]').boundingBox();p=await position();await page.mouse.move(button.x+button.width/2,button.y+button.height/2);await page.mouse.down();await page.waitForTimeout(300);await page.mouse.up();assert.ok(distance(p,await position())>.2);checks.push('screen direction controls move camera');
        await page.locator('.scene-viewport canvas').focus();await move('KeyQ',800);assert.ok((await position())[1]>=.4);await page.keyboard.press('KeyR');assert.equal((await position())[1],1.65);checks.push('height floor and standing reset work');
        await page.screenshot({path:path.join(out,'scene-fullscreen-free-desktop.png')});
        await page.locator('[data-scene-fullscreen]').click();await page.waitForFunction(()=>!window.FumaiScene.diagnostics().expanded);assert.equal(await page.evaluate(()=>document.body.style.overflow),'hidden');checks.push('native fullscreen exit keeps the tab-sized workspace');
        await page.evaluate(()=>document.querySelector('#scene-workbench').requestFullscreen=()=>Promise.reject(new Error('test unsupported fullscreen')));await page.locator('[data-scene-fullscreen]').click();await full();await page.keyboard.press('Escape');await page.waitForFunction(()=>!window.FumaiScene.diagnostics().expanded);checks.push('page expansion fallback and Escape work');
        await page.setViewportSize({width:390,height:844});if((await stats()).panels.list)await page.locator('.scene-edge-left').click();if((await stats()).panels.detail)await page.locator('.scene-edge-right').click();await page.locator('[data-scene-fullscreen]').click();await full();assert.equal(await page.locator('.scene-bar [data-scene-panel]').getAttribute('aria-expanded'),'false');await page.locator('[data-camera=free]').click();
        const touch=await context.newCDPSession(page),pad=await page.locator('[data-roam-move=forward]').boundingBox();p=await position();await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:pad.x+pad.width/2,y:pad.y+pad.height/2}]});await page.waitForTimeout(350);await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});assert.ok(distance(p,await position())>.2);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'scene-fullscreen-free-mobile.png')});checks.push('mobile fullscreen and native touch movement work');
        await page.evaluate(()=>location.hash='overview');await page.waitForSelector('#main[data-view=overview]');assert.equal((await stats()).active,false);assert.notEqual(await page.evaluate(()=>document.body.style.overflow),'hidden');assert.deepEqual(errors,[]);checks.push('route exit disposes navigation and fullscreen state');
        const result={base,checks,pageErrors:errors};fs.writeFileSync(path.join(out,'scene-navigation-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
    }finally{if(registered)await page.request.post(base+'/api/auth/logout',{headers:{Origin:base}}).catch(()=>{});await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
