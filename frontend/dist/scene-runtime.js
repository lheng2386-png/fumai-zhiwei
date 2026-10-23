import * as T from './vendor/three-r180/three.module.min.js';
import {OrbitControls} from './vendor/three-r180/OrbitControls.js';
import {demoManifest,validateManifest,TYPES,assetEvents,pendingEvents} from './scene-data.js';

const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const rad=v=>v*Math.PI/180;
const material=(color,extra={})=>new T.MeshStandardMaterial({color,roughness:.75,...extra});
function disposeTree(root){const geometries=new Set(),materials=new Set(),textures=new Set();root.traverse(o=>{if(o.geometry)geometries.add(o.geometry);for(const m of (Array.isArray(o.material)?o.material:[o.material]).filter(Boolean)){materials.add(m);for(const value of Object.values(m))if(value?.isTexture)textures.add(value);}});for(const t of textures)t.dispose();for(const m of materials)m.dispose();for(const g of geometries)g.dispose();}

export function createSceneView(host,{devices,getEvents,openAgent}){
 let manifest=validateManifest(demoManifest(devices)),events=getEvents(),selected='inv_07',type='all',onlyAlerts=false,query='',labels=true,disposed=false,renderer,controls,scene,camera,world,panelMesh,panelAssets=[],light,shadowGrid,ring,observer,raf=0,transition=null,draws=0,frames=0,quality='standard',lighting='day',renderReady=false,importVersion=0;
 const assetRoots=new Map(),pickables=[],markers=new Map(),modelStates=new Map(),listeners=[];
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 host.innerHTML=`<div class="scene-bar"><button data-camera="overview" class="active">全站视角</button><button data-camera="top">俯视</button><button data-camera="focus">定位设备</button><span class="scene-bar-spacer"></span><select data-scene-type aria-label="设备类型"><option value="all">全部设备</option>${Object.entries(TYPES).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select><button data-scene-alerts aria-pressed="false">仅待核查</button><button data-scene-labels aria-pressed="true">标注开</button><select data-scene-light aria-label="场景光照"><option value="day">日间光照</option><option value="dusk">傍晚光照</option></select><select data-scene-quality aria-label="画质"><option value="standard">标准画质</option><option value="low">低功耗</option></select><button data-scene-fullscreen aria-label="全屏场景">⛶</button></div><div class="scene-message" role="status" hidden></div><div class="scene-layout"><div class="scene-viewport"><div class="scene-overlays"></div><div class="scene-head"><strong data-scene-name></strong><span>设备空间分布 / 点击设备查看线索</span></div><div class="scene-source" data-scene-source></div><div class="scene-compass" aria-label="示意坐标北向"><b>N</b><i>↑</i></div></div><aside class="scene-inspector"><div class="scene-inspector-head"><small>ASSET EXPLORER</small><h2>设备索引</h2><p>在列表或场景中选择设备</p></div><div class="scene-search"><input data-scene-search aria-label="搜索三维设备" placeholder="设备编号 / 分区…"></div><div class="scene-assets" aria-label="设备列表"></div><div class="scene-selection" aria-live="polite"></div></aside></div><div class="scene-stats"><span><b data-count-panel></b>组件</span><span><b data-count-inverter></b>逆变器</span><span><b data-count-transformer></b>箱变</span><span><b data-count-alert></b>待核查设备</span><span class="scene-help">拖拽旋转 · 滚轮缩放 · 右键平移 · 触屏双指缩放</span></div>`;
 const viewport=host.querySelector('.scene-viewport'),overlay=host.querySelector('.scene-overlays'),selection=host.querySelector('.scene-selection'),assetList=host.querySelector('.scene-assets');
 const on=(target,name,fn,options)=>{target.addEventListener(name,fn,options);listeners.push(()=>target.removeEventListener(name,fn,options));};
 const message=text=>{const el=host.querySelector('.scene-message');el.hidden=!text;el.textContent=text;};
 const alertFor=a=>pendingEvents(a,events,manifest.source).length;
 const allowed=a=>(type==='all'||a.type===type)&&(!onlyAlerts||alertFor(a)>0);
 const visibleAssets=()=>manifest.assets.filter(a=>allowed(a)&&(!query||`${a.id} ${a.name} ${a.zone}`.toLowerCase().includes(query))).sort((a,b)=>({inverter:0,transformer:1,panel:2}[a.type]-{inverter:0,transformer:1,panel:2}[b.type]));
 function info(){
  host.querySelector('[data-scene-name]').textContent=manifest.name;
  host.querySelector('[data-scene-source]').textContent=manifest.source==='constructed_demo'?'示意布局 · 样例事件回放 · 非测绘成果':'用户导入布局 · 坐标与模型待工程验收 · 业务状态未接入';
  for(const key of Object.keys(TYPES))host.querySelector(`[data-count-${key}]`).textContent=manifest.assets.filter(a=>a.type===key).length;
  host.querySelector('[data-count-alert]').textContent=manifest.assets.filter(a=>a.type==='inverter'&&alertFor(a)).length;
 }
 function renderAssets(){
  const rows=visibleAssets();
  assetList.innerHTML=rows.slice(0,100).map(a=>`<button data-scene-select="${esc(a.id)}" aria-pressed="${a.id===selected}"><span class="scene-dot ${alertFor(a)?'alert':''}"></span>${esc(a.name)}<small>${esc(a.zone)}</small></button>`).join('')+(rows.length>100?'<p class="scene-empty">显示前 100 项，请搜索设备编号。</p>':'')||'<p class="scene-empty">当前条件没有设备</p>';
 }
 function renderSelection(){
  const a=manifest.assets.find(a=>a.id===selected);
  if(!a){selection.innerHTML='<p class="scene-empty">选择设备，查看位置与关联线索。</p>';return;}
  const linked=assetEvents(a,events,manifest.source),device=devices.find(d=>d.id===a.id),showPower=manifest.source==='constructed_demo'&&device;
  selection.innerHTML=`<h3>${esc(a.name)}</h3><p>${esc(a.id)} · ${esc(TYPES[a.type])}</p><dl class="scene-kv"><div><dt>所在区域</dt><dd>${esc(a.zone)}</dd></div><div><dt>线索状态</dt><dd>${alertFor(a)?'候选事件待核查':manifest.source==='constructed_demo'?'未挂接待核查线索':'业务数据未接入'}</dd></div><div><dt>场景坐标 / m</dt><dd>${a.position.map(n=>n.toFixed(1)).join(' / ')}</dd></div><div><dt>空间来源</dt><dd>${manifest.source==='constructed_demo'?'构造示意':'用户导入 / 待验收'}</dd></div>${showPower?`<div><dt>样例实测功率</dt><dd>${esc(device.actual_kw)} kW</dd></div><div><dt>样例参考功率</dt><dd>${esc(device.expected_kw)} kW</dd></div>`:''}</dl>${a.group&&a.type==='panel'?`<p>示意关联：${esc(a.group)}；非工程接线确认。</p>`:''}<p class="scene-model-state">${esc(modelStates.get(a.id)||(a.model?'模型等待加载':'程序化示意模型'))}</p><div class="scene-event-list">${linked.length?'<strong>关联样例事件</strong>'+linked.map(e=>`<article><strong>${esc(e.event_id)}</strong><p>${esc(e.start_local)}<br>事件观测 ${esc(e.mean_actual_kw)} / 参考 ${esc(e.mean_reference_kw)} kW</p><button data-scene-event="${esc(e.event_id)}">查看证据 ↗</button>　<button data-scene-agent="${esc(e.event_id)}">询问智能体 ↗</button></article>`).join(''):'<p>当前没有挂接业务事件，不能据此判断设备正常。</p>'}</div>`;
 }
 function choose(id,focus=false){if(!manifest.assets.some(a=>a.id===id))return;selected=id;renderAssets();renderSelection();updateRing();if(focus)moveCamera('focus');invalidate();host.dispatchEvent(new CustomEvent('fumai-scene-select',{bubbles:true,detail:{id,source:manifest.source}}));}
 function updateRing(){if(!ring)return;const a=manifest.assets.find(a=>a.id===selected);ring.visible=!!a&&allowed(a);if(a){ring.position.set(a.position[0],.06,a.position[2]);ring.scale.setScalar(a.type==='panel'?1.2:a.type==='transformer'?3:1.5);}}
 function updateFilters(){
  if(world){for(const a of manifest.assets){const root=assetRoots.get(a.id);if(root)root.visible=allowed(a);}
   if(panelMesh){const dummy=new T.Object3D();for(let i=0;i<panelAssets.length;i++){const a=panelAssets[i];dummy.position.fromArray(a.position);dummy.rotation.set(...a.rotation.map(rad));dummy.scale.setScalar(allowed(a)?1:0);dummy.updateMatrix();panelMesh.setMatrixAt(i,dummy.matrix);panelMesh.setColorAt(i,new T.Color(onlyAlerts&&alertFor(a)?'#e2b0a1':'#ffffff'));}panelMesh.instanceMatrix.needsUpdate=true;panelMesh.instanceColor.needsUpdate=true;}
  }
  if(!manifest.assets.some(a=>a.id===selected&&allowed(a)))selected=visibleAssets()[0]?.id||'';
  renderAssets();renderSelection();updateRing();invalidate();
 }
 function box(parent,size,pos,mat){const mesh=new T.Mesh(new T.BoxGeometry(...size),mat);mesh.position.set(...pos);mesh.castShadow=true;mesh.receiveShadow=true;parent.add(mesh);return mesh;}
 function addPrimitive(a){
  const group=new T.Group();group.position.fromArray(a.position);group.rotation.set(...a.rotation.map(rad));group.userData.assetId=a.id;world.add(group);assetRoots.set(a.id,group);
  if(a.type==='inverter'){
   box(group,[1.2,.15,.9],[0,.08,0],material('#a8b1a3'));
   box(group,[.82,1.62,.42],[0,.95,0],material('#d8e0d5',{metalness:.15,roughness:.5}));
   box(group,[.86,.12,.46],[0,1.8,0],material('#3f6558'));
   box(group,[.27,.16,.025],[0,1.22,.225],material('#173f38'));
   for(let j=0;j<4;j++)box(group,[.5,.025,.025],[0,.55+j*.1,.225],material('#899d8e'));
  }else if(a.type==='transformer'){
   box(group,[5,.3,3.5],[0,.15,0],material('#acb3a5'));
   box(group,[4.1,2.5,2.65],[0,1.5,0],material('#7d9383',{metalness:.2}));
   box(group,[4.45,.23,2.95],[0,2.88,0],material('#3f5d50'));
   for(let j=0;j<3;j++){box(group,[1.08,2.1,.045],[-1.3+j*1.3,1.45,1.35],material('#a0b19f'));box(group,[.04,.35,.07],[-1+j*1.3,1.4,1.4],material('#324b3c'));}
   box(group,[.4,.4,.02],[0,2.1,1.4],material('#d7b46a'));
  }else{box(group,[1.5,.08,2.2],[0,0,0],material('#294b60'));}
  group.traverse(o=>{if(o.isMesh){o.userData.assetId=a.id;pickables.push(o);}});
 }
 function panelTexture(){
  const canvas=document.createElement('canvas');canvas.width=128;canvas.height=192;const ctx=canvas.getContext('2d');ctx.fillStyle='#18394e';ctx.fillRect(0,0,128,192);
  for(let r=0;r<10;r++)for(let c=0;c<6;c++){ctx.fillStyle=(r+c)%3?'#294f65':'#31586b';ctx.fillRect(3+c*20.5,3+r*18.5,19,17);}
  ctx.strokeStyle='#c1cbd0';ctx.lineWidth=3;ctx.strokeRect(1.5,1.5,125,189);const texture=new T.CanvasTexture(canvas);texture.colorSpace=T.SRGBColorSpace;return texture;
 }
 function build(){
  importVersion++;const version=importVersion;
  if(world){disposeTree(world);scene.remove(world);}assetRoots.clear();pickables.length=0;markers.clear();overlay.replaceChildren();modelStates.clear();panelMesh=null;
  world=new T.Group();scene.add(world);
  box(world,[114,.3,96],[0,-.25,0],material('#bec7ad'));
  box(world,[98,.03,3],[0,-.075,0],material('#dce0cc'));
  box(world,[3,.04,83],[39,-.07,0],material('#dce0cc'));
  const grid=new T.GridHelper(110,22,'#879e83','#b0bea1');grid.position.y=-.065;grid.material.opacity=.2;grid.material.transparent=true;world.add(grid);shadowGrid=grid;
  const fence=material('#718775');for(const z of [-41,41])box(world,[101,.6,.09],[0,.4,z],fence);for(const x of [-50.5,50.5])box(world,[.09,.6,82],[x,.4,0],fence);
  box(world,[6,2.8,4],[-45,1.4,32],material('#c1c8b6'));box(world,[6.5,.2,4.5],[-45,2.9,32],material('#527360'));
  panelAssets=manifest.assets.filter(a=>a.type==='panel'&&!a.model);
  if(panelAssets.length){panelMesh=new T.InstancedMesh(new T.BoxGeometry(1.5,.09,2.2),material('#ffffff',{map:panelTexture(),metalness:.18,roughness:.52}),panelAssets.length);panelMesh.castShadow=true;panelMesh.receiveShadow=true;panelMesh.userData.panelInstances=true;world.add(panelMesh);pickables.push(panelMesh);
   const legs=new T.InstancedMesh(new T.BoxGeometry(.05,1,.05),material('#77897c'),panelAssets.length*2),dummy=new T.Object3D();
   for(let i=0;i<panelAssets.length;i++)for(let j=0;j<2;j++){dummy.position.set(panelAssets[i].position[0],.48,panelAssets[i].position[2]+(j?-.75:.75));dummy.scale.set(1,j?.85:1.45,1);dummy.updateMatrix();legs.setMatrixAt(i*2+j,dummy.matrix);}legs.castShadow=true;world.add(legs);
   // Supports remain spatial context when individual modules are filtered.
  }
  for(const a of manifest.assets.filter(a=>a.type!=='panel'||a.model)){addPrimitive(a);if(a.model)void loadModel(a,version);}
  for(const a of manifest.assets.filter(a=>a.type==='inverter'&&alertFor(a))){const marker=document.createElement('button');marker.className='scene-marker';marker.dataset.sceneSelect=a.id;marker.setAttribute('aria-label',`${a.name}，${alertFor(a)} 条候选事件待核查`);marker.innerHTML=`${esc(a.id)} <b>${alertFor(a)}</b>`;overlay.append(marker);markers.set(a.id,marker);}
  ring=new T.Mesh(new T.RingGeometry(.8,1,48),new T.MeshBasicMaterial({color:'#d4a74d',side:T.DoubleSide,transparent:true,opacity:.9}));ring.rotation.x=-Math.PI/2;world.add(ring);
  updateFilters();info();renderSelection();moveCamera('overview',true);
 }
 async function loadModel(a,version){
  try{
   modelStates.set(a.id,'正在加载工程模型…');if(selected===a.id)renderSelection();
   const {loadEngineeringModel}=await import('./scene-models.js');const object=await loadEngineeringModel(a.model);
   if(disposed||version!==importVersion){disposeTree(object);return;}
   const root=assetRoots.get(a.id);for(const child of [...root.children]){disposeTree(child);root.remove(child);}for(let i=pickables.length-1;i>=0;i--)if(pickables[i].userData.assetId===a.id)pickables.splice(i,1);
   object.scale.fromArray(a.scale);root.add(object);object.traverse(o=>{if(o.isMesh){o.userData.assetId=a.id;pickables.push(o);o.castShadow=true;o.receiveShadow=true;}});modelStates.set(a.id,'已加载用户模型 · 尺度与精度待验收');if(selected===a.id)renderSelection();invalidate();
  }catch(error){if(disposed||version!==importVersion)return;modelStates.set(a.id,'模型未能加载，当前显示示意占位体');if(selected===a.id)renderSelection();message('部分模型未加载；保留可选择的示意占位体，请检查文件与接入格式。');}
 }
 function moveCamera(mode,instant=false){
  if(!camera||!controls)return;
  const bounds=new T.Box3();for(const a of manifest.assets)bounds.expandByPoint(new T.Vector3(...a.position));const center=bounds.getCenter(new T.Vector3()),extent=Math.max(30,bounds.getSize(new T.Vector3()).length());
  let target=center.clone().setY(0),position;
  if(mode==='focus'){
   const a=manifest.assets.find(a=>a.id===selected);if(!a)return;target=new T.Vector3(...a.position);target.y=Math.max(1,target.y);position=target.clone().add(new T.Vector3(9,8,10).multiplyScalar(a.type==='transformer'?1.6:1));
  }else {const fit=Math.max(1,1.35/camera.aspect);position=target.clone().add((mode==='top'?new T.Vector3(0,extent*1.05,.1):new T.Vector3(extent*.73,extent*.74,extent*.88)).multiplyScalar(fit));}
  controls.maxDistance=Math.max(200,extent*4);camera.far=Math.max(1200,extent*10);camera.updateProjectionMatrix();
  if(instant||reduced){camera.position.copy(position);controls.target.copy(target);transition=null;controls.update();}else transition={from:camera.position.clone(),to:position,start:controls.target.clone(),target,time:performance.now()};
  host.querySelectorAll('[data-camera]').forEach(b=>b.classList.toggle('active',b.dataset.camera===mode));invalidate();
 }
 function drawMarkers(){
  const occupied=[];
  for(const [id,marker]of [...markers].sort(([a],[b])=>Number(b===selected)-Number(a===selected))){const a=manifest.assets.find(a=>a.id===id),v=new T.Vector3(...a.position);v.y+=3;v.project(camera);const x=(v.x*.5+.5)*viewport.clientWidth,y=(-v.y*.5+.5)*viewport.clientHeight;let visible=labels&&allowed(a)&&v.z>-1&&v.z<1&&Math.abs(v.x)<1.05&&Math.abs(v.y)<1.05&&y>75;if(visible&&occupied.some(p=>Math.abs(p[0]-x)<78&&Math.abs(p[1]-y)<31))visible=false;marker.hidden=!visible;if(visible){marker.style.left=`${x}px`;marker.style.top=`${y}px`;occupied.push([x,y]);}marker.classList.toggle('selected',id===selected);}
  host.querySelector('.scene-compass i').style.transform=`rotate(${Math.atan2(camera.position.x-controls.target.x,camera.position.z-controls.target.z)}rad)`;
 }
 function tick(now){
  raf=0;if(disposed||!renderer||!renderReady||document.hidden)return;
  if(transition){const t=Math.min(1,(now-transition.time)/650),ease=1-(1-t)**3;camera.position.lerpVectors(transition.from,transition.to,ease);controls.target.lerpVectors(transition.start,transition.target,ease);if(t===1)transition=null;}
  const moving=controls.update();renderer.render(scene,camera);frames++;draws=renderer.info.render.calls;drawMarkers();
  if(transition||moving)invalidate();
 }
 function invalidate(){if(!disposed&&renderer&&renderReady&&!raf)raf=requestAnimationFrame(tick);}
 function resize(){if(!renderer)return;const w=viewport.clientWidth,h=viewport.clientHeight;if(!w||!h)return;camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setPixelRatio(quality==='low'?1:Math.min(devicePixelRatio||1,innerWidth<650?1.25:1.75));renderer.setSize(w,h,false);const active=host.querySelector('[data-camera].active')?.dataset.camera;if(active==='overview'||active==='top')moveCamera(active,true);invalidate();}
 function fallback(){viewport.insertAdjacentHTML('beforeend','<div class="scene-fallback"><h3>当前浏览器未能启用三维显示</h3><p>可继续使用右侧设备索引查看详情与事件。<br>启用浏览器图形加速后刷新可重试。</p><a class="secondary-button" href="#devices">查看设备列表 ↗</a></div>');host.dataset.sceneReady='fallback';}
 try{
  renderer=new T.WebGLRenderer({antialias:true,powerPreference:'high-performance',alpha:false});renderer.setClearColor('#e7eee7');renderer.shadowMap.enabled=true;renderer.shadowMap.type=T.PCFSoftShadowMap;renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.25;renderer.domElement.tabIndex=0;renderer.domElement.setAttribute('aria-label','三维电站场景；也可使用设备索引选择');viewport.prepend(renderer.domElement);
  scene=new T.Scene();scene.fog=new T.Fog('#e7eee7',170,350);camera=new T.PerspectiveCamera(38,1,.2,1200);
  scene.add(new T.HemisphereLight('#eff6ee','#7b8d70',2.7));light=new T.DirectionalLight('#fff6dc',3.2);light.position.set(-25,60,-25);light.castShadow=true;light.shadow.mapSize.set(1024,1024);Object.assign(light.shadow.camera,{left:-75,right:75,top:75,bottom:-75,near:1,far:150});light.shadow.bias=-.001;light.shadow.normalBias=.08;scene.add(light);
  controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.12;controls.maxPolarAngle=Math.PI*.48;controls.minDistance=4;on(controls,'change',invalidate);on(controls,'start',()=>{transition=null;});
  on(renderer.domElement,'webglcontextlost',e=>{e.preventDefault();renderReady=false;if(raf)cancelAnimationFrame(raf);raf=0;fallback();});
  const raycaster=new T.Raycaster();let down=null;
  on(renderer.domElement,'pointerdown',e=>{down=[e.clientX,e.clientY];});
  on(renderer.domElement,'pointerup',e=>{if(!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>5)return;const rect=renderer.domElement.getBoundingClientRect();raycaster.setFromCamera(new T.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1),camera);const hit=raycaster.intersectObjects(pickables,false).find(h=>{const id=h.object===panelMesh?panelAssets[h.instanceId]?.id:h.object.userData.assetId;const asset=manifest.assets.find(a=>a.id===id);return h.object.visible&&asset&&allowed(asset);});if(hit){const id=hit.object===panelMesh?panelAssets[hit.instanceId]?.id:hit.object.userData.assetId;if(id)choose(id);}});
  renderReady=true;observer=new ResizeObserver(resize);observer.observe(viewport);build();resize();host.dataset.sceneReady='webgl';
 }catch(error){renderer?.dispose();renderer=null;fallback();}
 info();renderAssets();renderSelection();
 on(host,'click',e=>{
  const select=e.target.closest('[data-scene-select]');if(select){choose(select.dataset.sceneSelect,true);return;}
  const cameraButton=e.target.closest('[data-camera]');if(cameraButton){moveCamera(cameraButton.dataset.camera);return;}
  const event=e.target.closest('[data-scene-event]');if(event){location.hash='events';setTimeout(()=>document.querySelector(`[data-event="${CSS.escape(event.dataset.sceneEvent)}"]`)?.click(),400);return;}
  const agent=e.target.closest('[data-scene-agent]');if(agent){openAgent(agent.dataset.sceneAgent);return;}
  if(e.target.closest('[data-scene-alerts]')){onlyAlerts=!onlyAlerts;e.target.closest('button').classList.toggle('active',onlyAlerts);e.target.closest('button').setAttribute('aria-pressed',String(onlyAlerts));updateFilters();}
  if(e.target.closest('[data-scene-labels]')){labels=!labels;e.target.closest('button').textContent=labels?'标注开':'标注关';e.target.closest('button').setAttribute('aria-pressed',String(labels));invalidate();}
  if(e.target.closest('[data-scene-fullscreen]')){if(document.fullscreenElement)void document.exitFullscreen();else viewport.requestFullscreen?.().catch(()=>message('浏览器暂不支持全屏，可使用全站视角。'));}
 });
 on(host,'input',e=>{if(e.target.matches('[data-scene-search]')){query=e.target.value.trim().toLowerCase();renderAssets();}});
 on(host,'change',e=>{
  if(e.target.matches('[data-scene-type]')){type=e.target.value;updateFilters();}
  if(e.target.matches('[data-scene-quality]')){quality=e.target.value;if(renderer){renderer.shadowMap.enabled=quality==='standard';resize();}}
  if(e.target.matches('[data-scene-light]')){lighting=e.target.value;if(light){light.color.set(lighting==='dusk'?'#ffb26f':'#fff6dc');light.intensity=lighting==='dusk'?2:3.2;light.position.set(lighting==='dusk'?-65:-25,lighting==='dusk'?18:60,-25);renderer.toneMappingExposure=lighting==='dusk'?.85:1.25;invalidate();}}
 });
 on(document,'visibilitychange',invalidate);
 const exportButton=document.querySelector('[data-scene-export]'),importInput=document.querySelector('[data-scene-import]');
 on(exportButton,'click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(manifest,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='fumai-scene-template.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
 on(importInput,'change',async()=>{const file=importInput.files?.[0];if(!file)return;try{if(file.size>2*1024*1024)throw Error('场景 JSON 不得超过 2 MB。');const parsed=validateManifest(JSON.parse(await file.text()));if(disposed)return;manifest=parsed;events=getEvents();selected=manifest.assets[0].id;type='all';onlyAlerts=false;query='';host.querySelector('[data-scene-type]').value='all';host.querySelector('[data-scene-alerts]').classList.remove('active');host.querySelector('[data-scene-alerts]').setAttribute('aria-pressed','false');host.querySelector('[data-scene-search]').value='';message('已在当前浏览器载入场景，坐标和模型精度待工程验收；未写入服务器。');if(renderer)build();info();renderAssets();renderSelection();}catch(error){message(error.message||'场景格式无效，保留原场景。');}finally{importInput.value='';}});
 return {dispose(){disposed=true;importVersion++;if(raf)cancelAnimationFrame(raf);observer?.disconnect();for(const off of listeners)off();controls?.dispose();if(scene)disposeTree(scene);renderer?.dispose();renderer?.forceContextLoss();host.dataset.sceneDisposed='true';},stats(){return {frames,drawCalls:draws,assets:manifest.assets.length,backend:'webgl',active:!disposed};}};
}
