const {chromium}=require(process.env.FUMAI_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.FUMAI_CHECK_URL||'http://127.0.0.1:4212',out=process.env.FUMAI_TEST_OUTPUT||path.resolve('outputs');
(async()=>{
    const browser=await chromium.launch({headless:true,...(process.env.FUMAI_BROWSER_EXECUTABLE?{executablePath:process.env.FUMAI_BROWSER_EXECUTABLE}:{}),args:['--enable-unsafe-swiftshader']});
    const page=await browser.newPage({viewport:{width:1100,height:800}}),errors=[],checks=[];
    try{
        assert.equal(new URL(base).hostname,'127.0.0.1');fs.mkdirSync(out,{recursive:true});
        page.on('pageerror',e=>errors.push(e.message));page.on('console',e=>{if(e.type()==='error'&&/Shader|WebGLProgram|GL_INVALID/.test(e.text()))errors.push(e.text());});
        await page.goto(base+'/models/meshopt-sample.json');
        const geometry=await page.evaluate(async base=>{
            const T=await import(base+'/vendor/three-r180/three.module.min.js'),{createEquipmentKit}=await import(base+'/scene-equipment.js'),{panelPose}=await import(base+'/scene-panel-mount.js'),{equipmentRecipe}=await import(base+'/scene-equipment-data.js');
            document.body.innerHTML='';document.body.style.margin='0';
            const renderer=new T.WebGLRenderer({antialias:true,logarithmicDepthBuffer:true});renderer.setSize(1100,800);renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.05;renderer.shadowMap.enabled=true;renderer.setClearColor('#dfe7df');document.body.append(renderer.domElement);
            const scene=new T.Scene(),root=new T.Group();scene.add(root);scene.add(new T.HemisphereLight('#e5f1fa','#82916f',1.15));const sun=new T.DirectionalLight('#fff4dd',3);sun.position.set(-3,8,-4);sun.castShadow=true;Object.assign(sun.shadow.camera,{left:-7,right:7,top:7,bottom:-7});scene.add(sun);
            const floor=new T.Mesh(new T.PlaneGeometry(30,30),new T.MeshStandardMaterial({color:'#99a18a',roughness:1}));floor.rotation.x=-Math.PI/2;floor.position.y=-.02;floor.receiveShadow=true;scene.add(floor);
            const camera=new T.PerspectiveCamera(40,1100/800,.05,100),kit=createEquipmentKit();
            const assets=[{id:'left',position:[-2.5,1.25,0],rotation:[-24,0,0]},{id:'middle',position:[0,1.25,0],rotation:[-24,0,0]},{id:'angled',position:[2.5,1.7,0],rotation:[-35,30,6]}],proxy=kit.addPanels(root,assets);
            const module=equipmentRecipe.models.find(m=>m.id==='panel'),back=module.parts.find(p=>p.name==='backsheet'),underside=back.center[1]-back.size[1]/2;let max=-Infinity;
            root.traverse(mesh=>{const kind=mesh.userData.mountKind;if(!kind)return;const count=kind==='upper'?1:kind==='braces'?2:4;for(let a=0;a<assets.length;a++)for(let part=0;part<count;part++){const matrix=new T.Matrix4();mesh.getMatrixAt(a*count+part,matrix);matrix.premultiply(panelPose(assets[a]).invert());const p=mesh.geometry.attributes.position,v=new T.Vector3();for(let i=0;i<p.count;i++)max=Math.max(max,v.fromBufferAttribute(p,i).applyMatrix4(matrix).y);}});
            window.mountHarness={T,renderer,scene,root,camera,kit,proxy,assets,view(position,target=[0,1.1,0],quality='standard'){camera.position.fromArray(position);camera.lookAt(...target);camera.updateMatrixWorld();kit.update(camera,'middle',quality);renderer.render(scene,camera);}};
            window.mountHarness.view([3,2.4,-4]);return {maximumRackLocalY:max,underside,supported:kit.stats().mounts.supported};
        },base);
        assert.equal(geometry.supported,3);assert.ok(geometry.maximumRackLocalY<=geometry.underside+1e-5);checks.push('actual instanced rack vertices remain below all three panel backsheets');
        for(const [name,position]of[['front',[3,2.4,-4]],['low',[3,1.1,-4]],['underside',[2,.35,3]],['overview',[7,5,-9]]]){await page.evaluate(position=>window.mountHarness.view(position),position);await page.screenshot({path:path.join(out,'mount-'+name+'.png')});}checks.push('front, low, underside and overview renders preserve connected supports');
        const picking=await page.evaluate(()=>{const h=window.mountHarness;h.view([0,2,-3],[0,1.25,0]);const ray=new h.T.Raycaster();ray.setFromCamera(new h.T.Vector2(0,0),h.camera);return ray.intersectObject(h.proxy).map(hit=>hit.instanceId);});assert.ok(picking.includes(1));checks.push('panel picking remains aligned to the visible module');
        const lod=await page.evaluate(()=>{const h=window.mountHarness;h.view([3,2.4,-4]);const near=h.kit.stats().closePanel;h.view([10,10,-22]);const far=h.kit.stats().closePanel;h.view([3,2.4,-4],[0,1.1,0],'low');const low=h.kit.stats().closePanel;return {near,far,low};});assert.equal(lod.near,'middle');assert.equal(lod.far,'');assert.equal(lod.low,'');checks.push('near detail, distant instances and low power share one support installation');
        const filtering=await page.evaluate(()=>{const h=window.mountHarness;h.kit.filterPanels(a=>a.id==='middle',()=>false);const values=[];h.root.traverse(mesh=>{if(!mesh.userData.mountKind)return;const count=mesh.userData.mountKind==='upper'?1:mesh.userData.mountKind==='braces'?2:4;const hidden=new h.T.Matrix4(),shown=new h.T.Matrix4();mesh.getMatrixAt(0,hidden);mesh.getMatrixAt(count,shown);values.push({hidden:hidden.determinant(),shown:shown.determinant()});});return values;});assert.ok(filtering.every(v=>v.hidden===0&&v.shown>0));checks.push('device filtering hides every corresponding support component');
        await page.evaluate(()=>{const h=window.mountHarness;h.kit.dispose();h.scene.traverse(o=>{o.geometry?.dispose();for(const m of(Array.isArray(o.material)?o.material:[o.material]).filter(Boolean))m.dispose();});h.renderer.dispose();h.renderer.forceContextLoss();});
        assert.deepEqual(errors,[]);const result={base,checks,pageErrors:errors,geometry};fs.writeFileSync(path.join(out,'scene-panel-mount-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
    }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
