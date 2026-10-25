import * as T from './vendor/three-r180/three.module.min.js';
import {OrbitControls} from './vendor/three-r180/OrbitControls.js';
import {demoManifest,validateManifest,TYPES,assetEvents,pendingEvents} from './scene-data.js';
import {createCase,advanceCase,caseReport,renderCase} from './scene-case.js';
import {createEquipmentKit} from './scene-equipment.js';
import {addLandscape,createAtmosphere} from './scene-landscape.js';
import {createNavigation} from './scene-navigation.js';
import {daylightSample,advanceDaylight,DAYLIGHT_DEFAULT,DAYLIGHT_START,DAYLIGHT_END} from './scene-daylight.js';

const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const rad=v=>v*Math.PI/180;
function disposeTree(root){const geometries=new Set(),materials=new Set(),textures=new Set();root.traverse(o=>{if(o.geometry)geometries.add(o.geometry);for(const m of (Array.isArray(o.material)?o.material:[o.material]).filter(Boolean)){materials.add(m);for(const value of Object.values(m))if(value?.isTexture)textures.add(value);}});for(const t of textures)t.dispose();for(const m of materials)m.dispose();for(const g of geometries)g.dispose();}

export function createSceneView(host,{devices,getEvents,openAgent}){
 let manifest=validateManifest(demoManifest(devices)),events=getEvents(),selected='inv_07',type='all',onlyAlerts=false,query='',labels=true,disposed=false,renderer,controls,scene,camera,world,panelMesh,panelAssets=[],light,ambientLight,ring,observer,raf=0,transition=null,draws=0,frames=0,quality='standard',renderReady=false,importVersion=0;
 const assetRoots=new Map(),pickables=[],markers=new Map(),modelStates=new Map(),listeners=[];
 let caseState=null,walkthroughAvailable=true,equipment,landscape,atmosphere,navigation,expanded=false,nativeFullscreen=false,panelHidden=false,savedPanelHidden=false,oldBodyOverflow='';
 let sunlight=daylightSample(DAYLIGHT_DEFAULT),sunPlaying=false,sunTick=0,sunObserver;
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 host.innerHTML=`<div class="scene-bar"><button data-camera="overview" class="active">全站视角</button><button data-camera="top">俯视</button><button data-camera="focus">设备近览</button><button data-camera="walk">巡检视角</button><button data-camera="free" aria-pressed="false">自由漫游</button><span class="scene-bar-spacer"></span><select data-scene-type aria-label="设备类型"><option value="all">全部设备</option>${Object.entries(TYPES).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select><button data-scene-alerts aria-pressed="false">仅待核查</button><button data-scene-labels aria-pressed="true">标注开</button><select data-scene-light aria-label="场景光照"><option value="day">日间光照</option><option value="dusk">傍晚光照</option><option value="timeline">时间轴光照</option></select><select data-scene-quality aria-label="画质"><option value="standard">标准画质</option><option value="low">低功耗</option></select><button data-scene-panel aria-expanded="true">设备面板</button><button data-scene-fullscreen aria-label="全屏场景" aria-pressed="false">⛶ 全屏</button></div><div class="scene-message" role="status" hidden></div><div class="scene-layout"><div class="scene-viewport"><div class="scene-overlays"></div><div class="scene-head"><strong data-scene-name></strong><span>设备空间分布 / 点击设备查看线索</span></div><div class="scene-source" data-scene-source></div><div class="scene-compass" aria-label="示意坐标北向"><b>N</b><i>↑</i></div></div><aside class="scene-inspector"><div class="scene-inspector-head"><small>ASSET EXPLORER</small><h2>设备索引</h2><p>在列表或场景中选择设备</p></div><div class="scene-search"><input data-scene-search aria-label="搜索三维设备" placeholder="设备编号 / 分区…"></div><div class="scene-assets" aria-label="设备列表"></div><div class="scene-selection" aria-live="polite"></div></aside></div><section class="scene-daylight" aria-label="日照示意时间轴"><div class="scene-daylight-heading"><strong>日照演示</strong><output data-sun-time aria-label="示意时间">12:00</output><span data-sun-altitude>太阳高度角示意 60°</span></div><div class="scene-daylight-track"><label><span class="scene-sr-only">日照示意时间</span><input type="range" data-sun-range min="360" max="1080" step="5" value="720" aria-valuetext="12:00，日照示意"></label><div class="scene-daylight-ticks"><span>06:00 · 东</span><span>12:00</span><span>18:00 · 西</span></div></div><div class="scene-daylight-actions"><button data-sun-play aria-pressed="false">播放日照</button><button data-sun-reset>回到正午</button></div><small>示意太阳轨迹，非现场气象；不改变设备数据</small></section><div class="scene-stats"><span><b data-count-panel></b>组件</span><span><b data-count-inverter></b>逆变器</span><span><b data-count-transformer></b>箱变</span><span><b data-count-alert></b>待核查设备</span><span class="scene-help">拖拽旋转 · 滚轮缩放 · 右键平移 · 触屏双指缩放</span></div>`;
 const viewport=host.querySelector('.scene-viewport'),overlay=host.querySelector('.scene-overlays'),selection=host.querySelector('.scene-selection'),assetList=host.querySelector('.scene-assets');
 const casePanel=document.createElement('section');casePanel.className='scene-case';casePanel.setAttribute('aria-label','工程案例演练');host.prepend(casePanel);
 function renderWalkthrough(focus=false){casePanel.innerHTML=renderCase(caseState,walkthroughAvailable);host.classList.toggle('scene-case-active',!!caseState);if(focus){const heading=casePanel.querySelector('h3');if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}}}
 const download=(value,name)=>{const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const anchor=document.createElement('a');anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 renderWalkthrough();
 const on=(target,name,fn,options)=>{target.addEventListener(name,fn,options);listeners.push(()=>target.removeEventListener(name,fn,options));};
 const message=text=>{const el=host.querySelector('.scene-message');el.hidden=!text;el.textContent=text;};
 const alertFor=a=>pendingEvents(a,events,manifest.source).length;
 const allowed=a=>(type==='all'||a.type===type)&&(!onlyAlerts||alertFor(a)>0);
 const visibleAssets=()=>manifest.assets.filter(a=>allowed(a)&&(!query||`${a.id} ${a.name} ${a.zone}`.toLowerCase().includes(query))).sort((a,b)=>({inverter:0,transformer:1,panel:2}[a.type]-{inverter:0,transformer:1,panel:2}[b.type]));
 function info(){
  host.querySelector('[data-scene-name]').textContent=manifest.name;
  host.querySelector('[data-scene-source]').textContent=manifest.source==='constructed_demo'?'精细化示意 · 样例事件回放 · 非测绘成果':'用户导入布局 · 坐标与模型待工程验收 · 业务状态未接入';
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
  selection.innerHTML=`<h3>${esc(a.name)}</h3><p>${esc(a.id)} · ${esc(TYPES[a.type])}</p><dl class="scene-kv"><div><dt>所在区域</dt><dd>${esc(a.zone)}</dd></div><div><dt>线索状态</dt><dd>${alertFor(a)?'候选事件待核查':manifest.source==='constructed_demo'?'未挂接待核查线索':'业务数据未接入'}</dd></div><div><dt>场景坐标 / m</dt><dd>${a.position.map(n=>n.toFixed(1)).join(' / ')}</dd></div><div><dt>空间来源</dt><dd>${manifest.source==='constructed_demo'?'构造示意':'用户导入 / 待验收'}</dd></div>${showPower?`<div><dt>样例实测功率</dt><dd>${esc(device.actual_kw)} kW</dd></div><div><dt>样例参考功率</dt><dd>${esc(device.expected_kw)} kW</dd></div>`:''}</dl>${a.group&&a.type==='panel'?`<p>示意关联：${esc(a.group)}；非工程接线确认。</p>`:''}<p class="scene-model-state">${esc(modelStates.get(a.id)||(a.model?'模型等待加载':'精细化示意模型 · 尺寸待核对'))}</p><div class="scene-event-list">${linked.length?'<strong>关联样例事件</strong>'+linked.map(e=>`<article><strong>${esc(e.event_id)}</strong><p>${esc(e.start_local)}<br>事件观测 ${esc(e.mean_actual_kw)} / 参考 ${esc(e.mean_reference_kw)} kW</p><button data-scene-event="${esc(e.event_id)}">查看证据 ↗</button>　<button data-scene-agent="${esc(e.event_id)}">询问智能体 ↗</button></article>`).join(''):'<p>当前没有挂接业务事件，不能据此判断设备正常。</p>'}</div>`;
 }
 function choose(id,focus=false){if(!manifest.assets.some(a=>a.id===id))return;selected=id;if(caseState?.stage===0){caseState.picked=id;caseState.feedback='';renderWalkthrough();}renderAssets();renderSelection();updateRing();if(focus&&!navigation?.enabled)moveCamera('focus');invalidate();host.dispatchEvent(new CustomEvent('fumai-scene-select',{bubbles:true,detail:{id,source:manifest.source}}));}
 function updateRing(){if(!ring)return;const a=manifest.assets.find(a=>a.id===selected);ring.visible=!!a&&allowed(a);if(a){ring.position.set(a.position[0],.06,a.position[2]);ring.scale.setScalar(a.type==='panel'?1.2:a.type==='transformer'?3:1.5);}}
 function updateFilters(){
  if(world){for(const a of manifest.assets){const root=assetRoots.get(a.id);if(root)root.visible=allowed(a);}
   equipment?.filterPanels(allowed,a=>onlyAlerts&&alertFor(a)>0);
  }
  if(!manifest.assets.some(a=>a.id===selected&&allowed(a)))selected=visibleAssets()[0]?.id||'';
  renderAssets();renderSelection();updateRing();invalidate();
 }
 function addPrimitive(a){
  const group=equipment.createDevice(a.type);group.position.fromArray(a.position);group.rotation.set(...a.rotation.map(rad));group.userData.assetId=a.id;world.add(group);assetRoots.set(a.id,group);
  group.traverse(o=>{if(o.isMesh){o.userData.assetId=a.id;pickables.push(o);}});
 }
 function build(){
  importVersion++;const version=importVersion;
  if(world){disposeTree(world);scene.remove(world);}equipment?.dispose();equipment=createEquipmentKit();assetRoots.clear();pickables.length=0;markers.clear();overlay.replaceChildren();modelStates.clear();panelMesh=null;
  world=new T.Group();scene.add(world);
  landscape=addLandscape(world,renderer,{demonstration:manifest.source==='constructed_demo'});landscape.quality(quality);
  panelAssets=manifest.assets.filter(a=>a.type==='panel'&&!a.model);
  if(panelAssets.length){panelMesh=equipment.addPanels(world,panelAssets);pickables.push(panelMesh);}
  for(const a of manifest.assets.filter(a=>a.type!=='panel'||a.model)){addPrimitive(a);if(a.model)void loadModel(a,version);}
  for(const a of manifest.assets.filter(a=>a.type==='inverter'&&alertFor(a))){const marker=document.createElement('button');marker.className='scene-marker';marker.dataset.sceneSelect=a.id;marker.setAttribute('aria-label',`${a.name}，${alertFor(a)} 条候选事件待核查`);marker.innerHTML=`${esc(a.id)} <b>${alertFor(a)}</b>`;overlay.append(marker);markers.set(a.id,marker);}
  ring=new T.Mesh(new T.RingGeometry(.8,1,48),new T.MeshBasicMaterial({color:'#d4a74d',side:T.DoubleSide,transparent:true,opacity:.9}));ring.rotation.x=-Math.PI/2;world.add(ring);
  const navBounds=new T.Box3();for(const a of manifest.assets)navBounds.expandByPoint(new T.Vector3(...a.position));navigation?.setBounds(navBounds);
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
  if(mode==='free'){
   if(navigation.enabled){navigation.setEnabled(false);return;}
   if(camera.position.y>12)moveCamera('walk',true);
   transition=null;navigation.setEnabled(true);host.querySelectorAll('[data-camera]').forEach(b=>b.classList.toggle('active',b.dataset.camera==='free'));return;
  }
  navigation?.setEnabled(false);
  const bounds=new T.Box3();for(const a of manifest.assets)bounds.expandByPoint(new T.Vector3(...a.position));const center=bounds.getCenter(new T.Vector3()),extent=Math.max(30,bounds.getSize(new T.Vector3()).length());
  let target=center.clone().setY(0),position;
  if(mode==='focus'||mode==='walk'){
   const a=manifest.assets.find(a=>a.id===selected);if(!a)return;target=new T.Vector3(...a.position);target.y+=a.type==='transformer'?1.6:a.type==='inverter'?1:0;
   const offset=mode==='walk'?new T.Vector3(4,.55,6):a.type==='panel'?new T.Vector3(2.7,2.2,-3.4):a.type==='transformer'?new T.Vector3(8,4,9):new T.Vector3(2.4,1.1,3.1);
   if(mode==='walk')target.y=1.1;position=target.clone().add(offset);
  }else {const fit=Math.max(1,1.35/camera.aspect);position=target.clone().add((mode==='top'?new T.Vector3(0,extent*.95,.1):new T.Vector3(extent*.6,extent*.52,-extent*.72)).multiplyScalar(fit));}
  controls.maxDistance=Math.max(200,extent*4);camera.far=Math.max(1200,extent*10);camera.updateProjectionMatrix();
  if(instant||reduced){camera.position.copy(position);controls.target.copy(target);transition=null;controls.update();}else transition={from:camera.position.clone(),to:position,start:controls.target.clone(),target,time:performance.now()};
  host.querySelectorAll('[data-camera]').forEach(b=>b.classList.toggle('active',b.dataset.camera===mode));invalidate();
 }
 function drawMarkers(){
  const occupied=[];
  for(const [id,marker]of [...markers].sort(([a],[b])=>Number(b===selected)-Number(a===selected))){const a=manifest.assets.find(a=>a.id===id),v=new T.Vector3(...a.position);v.y+=3;v.project(camera);const x=(v.x*.5+.5)*viewport.clientWidth,y=(-v.y*.5+.5)*viewport.clientHeight;let visible=labels&&allowed(a)&&v.z>-1&&v.z<1&&Math.abs(v.x)<1.05&&Math.abs(v.y)<1.05&&y>75;if(visible&&occupied.some(p=>Math.abs(p[0]-x)<78&&Math.abs(p[1]-y)<31))visible=false;marker.hidden=!visible;if(visible){marker.style.left=`${x}px`;marker.style.top=`${y}px`;occupied.push([x,y]);}marker.classList.toggle('selected',id===selected);}
  const heading=camera.getWorldDirection(new T.Vector3());host.querySelector('.scene-compass i').style.transform=`rotate(${Math.atan2(-heading.x,-heading.z)}rad)`;
 }
 function syncSun(){
  host.querySelector('[data-sun-range]').value=String(Math.round(sunlight.minute));
  host.querySelector('[data-sun-range]').setAttribute('aria-valuetext',`${sunlight.time}，日照示意`);
  host.querySelector('[data-sun-time]').textContent=sunlight.time;
  host.querySelector('[data-sun-altitude]').textContent=`太阳高度角示意 ${Math.round(sunlight.altitude)}°`;
  const button=host.querySelector('[data-sun-play]');button.textContent=sunPlaying?'暂停日照':'播放日照';button.setAttribute('aria-pressed',String(sunPlaying));
  if(light&&renderer){light.position.fromArray(sunlight.sunPosition);light.color.copy(new T.Color('#fff6dc').lerp(new T.Color('#ffac70'),sunlight.warmth));light.intensity=sunlight.intensity;ambientLight.intensity=sunlight.ambient;renderer.toneMappingExposure=sunlight.exposure;atmosphere.sunlight(sunlight);}
 }
 function pauseSun(){sunPlaying=false;sunTick=0;syncSun();}
 function setSun(minute){pauseSun();sunlight=daylightSample(minute);host.querySelector('[data-scene-light]').value='timeline';syncSun();invalidate();}
 function tick(now){
  raf=0;if(disposed||!renderer||!renderReady||document.hidden)return;
  if(sunPlaying){const elapsed=sunTick?(now-sunTick)/1000:0;sunTick=now;sunlight=advanceDaylight(sunlight.minute,elapsed);if(sunlight.minute>=DAYLIGHT_END){sunPlaying=false;sunTick=0;}syncSun();}
  if(transition){const t=Math.min(1,(now-transition.time)/650),ease=1-(1-t)**3;camera.position.lerpVectors(transition.from,transition.to,ease);controls.target.lerpVectors(transition.start,transition.target,ease);if(t===1)transition=null;}
  const walking=navigation?.update(now);const moving=navigation?.enabled?false:controls.update();equipment?.update(camera,selected,quality);atmosphere.center(camera.position);renderer.render(scene,camera);frames++;draws=renderer.info.render.calls;drawMarkers();
  if(sunPlaying||transition||moving||walking||navigation?.moving())invalidate();
 }
 function invalidate(){if(!disposed&&renderer&&renderReady&&!raf)raf=requestAnimationFrame(tick);}
 function resize(){if(!renderer)return;const w=viewport.clientWidth,h=viewport.clientHeight;if(!w||!h)return;camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setPixelRatio(quality==='low'?1:Math.min(devicePixelRatio||1,innerWidth<650?1.25:1.75));renderer.setSize(w,h,false);const active=host.querySelector('[data-camera].active')?.dataset.camera;if(active==='overview'||active==='top')moveCamera(active,true);invalidate();}
 function fallback(){pauseSun();for(const el of host.querySelectorAll('[data-camera=free],[data-sun-play],[data-sun-reset],[data-sun-range],[data-scene-light]'))el.disabled=true;viewport.insertAdjacentHTML('beforeend','<div class="scene-fallback"><h3>当前浏览器未能启用三维显示</h3><p>可继续使用右侧设备索引查看详情与事件。<br>启用浏览器图形加速后刷新可重试。</p><a class="secondary-button" href="#devices">查看设备列表 ↗</a></div>');host.dataset.sceneReady='fallback';}
 try{
  renderer=new T.WebGLRenderer({antialias:true,powerPreference:'high-performance',alpha:false});renderer.setClearColor('#e7eee7');renderer.shadowMap.enabled=true;renderer.shadowMap.type=T.PCFSoftShadowMap;renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.08;renderer.domElement.tabIndex=0;renderer.domElement.setAttribute('aria-label','三维电站场景；也可使用设备索引选择');viewport.prepend(renderer.domElement);
  scene=new T.Scene();scene.fog=new T.Fog('#cedad8',130,500);camera=new T.PerspectiveCamera(43,1,.1,1200);atmosphere=createAtmosphere(scene,renderer);
  ambientLight=new T.HemisphereLight('#e5f1fa','#78845c',1.15);scene.add(ambientLight);light=new T.DirectionalLight('#fff6dc',3.2);light.position.set(-25,60,-25);light.castShadow=true;light.shadow.mapSize.set(2048,2048);Object.assign(light.shadow.camera,{left:-75,right:75,top:75,bottom:-75,near:1,far:200});light.shadow.bias=-.0001;light.shadow.normalBias=.035;scene.add(light);
  syncSun();
  controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.12;controls.maxPolarAngle=Math.PI*.48;controls.minDistance=1.3;on(controls,'change',invalidate);on(controls,'start',()=>{transition=null;});
  navigation=createNavigation({camera,controls,canvas:renderer.domElement,host,invalidate,onChange:active=>{host.querySelector('[data-camera=free]').classList.toggle('active',active);host.querySelector('[data-camera=free]').setAttribute('aria-pressed',String(active));host.querySelector('.scene-help').textContent=active?'WASD / 方向键移动 · Q/E 升降 · Shift 加速 · 拖动转头 · R 站高复位 · Esc 退出漫游':'拖拽旋转 · 滚轮缩放 · 右键平移 · 触屏双指缩放';}});
  on(renderer.domElement,'webglcontextlost',e=>{e.preventDefault();navigation?.setEnabled(false);renderReady=false;if(raf)cancelAnimationFrame(raf);raf=0;fallback();});
  const raycaster=new T.Raycaster();let down=null;
  on(renderer.domElement,'pointerdown',e=>{down=[e.clientX,e.clientY];});
  on(renderer.domElement,'pointerup',e=>{if(!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>5)return;const rect=renderer.domElement.getBoundingClientRect();raycaster.setFromCamera(new T.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1),camera);const hit=raycaster.intersectObjects(pickables,false).find(h=>{const id=h.object===panelMesh?panelAssets[h.instanceId]?.id:h.object.userData.assetId;const asset=manifest.assets.find(a=>a.id===id);return h.object.visible&&asset&&allowed(asset);});if(hit){const id=hit.object===panelMesh?panelAssets[hit.instanceId]?.id:hit.object.userData.assetId;if(id)choose(id);}});
  sunObserver=new IntersectionObserver(entries=>{if(!entries[0].isIntersecting)pauseSun();});sunObserver.observe(viewport);
  renderReady=true;observer=new ResizeObserver(resize);observer.observe(viewport);build();resize();host.dataset.sceneReady='webgl';
 }catch(error){renderer?.dispose();renderer=null;fallback();}
 info();renderAssets();renderSelection();
 on(host,'click',e=>{
  if(e.target.closest('[data-sun-play]')){if(!renderReady)return;if(sunPlaying)pauseSun();else{if(sunlight.minute>=DAYLIGHT_END)sunlight=daylightSample(DAYLIGHT_START);sunPlaying=true;sunTick=0;host.querySelector('[data-scene-light]').value='timeline';syncSun();invalidate();}return;}
  if(e.target.closest('[data-sun-reset]')){setSun(DAYLIGHT_DEFAULT);host.querySelector('[data-scene-light]').value='day';return;}

  const start=e.target.closest('[data-case-start]');if(start){if(!walkthroughAvailable)return;try{caseState=createCase(start.dataset.caseStart,window.FumaiDemo.events);type='all';onlyAlerts=false;query='';host.querySelector('[data-scene-type]').value='all';host.querySelector('[data-scene-alerts]').classList.remove('active');host.querySelector('[data-scene-alerts]').setAttribute('aria-pressed','false');host.querySelector('[data-scene-search]').value='';updateFilters();renderWalkthrough(true);moveCamera('overview');}catch(error){message(error.message);}return;}
  if(e.target.closest('[data-case-exit]')){caseState=null;renderWalkthrough();return;}
  if(e.target.closest('[data-case-show-evidence]')&&caseState?.stage===1){caseState.evidenceSeen=true;caseState.feedback='';renderWalkthrough();return;}
  if(e.target.closest('[data-case-agent]')&&caseState){openAgent(caseState.event.event_id);return;}
  if(e.target.closest('[data-case-export]')&&caseState?.stage===3){download(caseReport(caseState),'fumai-rehearsal-'+caseState.definition.id+'.json');return;}
  const action=e.target.closest('[data-case-action]');if(action&&caseState){advanceCase(caseState,action.dataset.caseAction,action.dataset.caseValue);renderWalkthrough(true);return;}
  const select=e.target.closest('[data-scene-select]');if(select){choose(select.dataset.sceneSelect,true);return;}
  const cameraButton=e.target.closest('[data-camera]');if(cameraButton){moveCamera(cameraButton.dataset.camera);return;}
  const event=e.target.closest('[data-scene-event]');if(event){location.hash='events';setTimeout(()=>document.querySelector(`[data-event="${CSS.escape(event.dataset.sceneEvent)}"]`)?.click(),400);return;}
  const agent=e.target.closest('[data-scene-agent]');if(agent){openAgent(agent.dataset.sceneAgent);return;}
  if(e.target.closest('[data-scene-alerts]')){onlyAlerts=!onlyAlerts;e.target.closest('button').classList.toggle('active',onlyAlerts);e.target.closest('button').setAttribute('aria-pressed',String(onlyAlerts));updateFilters();}
  if(e.target.closest('[data-scene-labels]')){labels=!labels;e.target.closest('button').textContent=labels?'标注开':'标注关';e.target.closest('button').setAttribute('aria-pressed',String(labels));if(camera&&controls)drawMarkers();invalidate();}
  if(e.target.closest('[data-scene-panel]')){panelHidden=!panelHidden;syncPanel();}
  if(e.target.closest('[data-scene-fullscreen]'))void toggleFullscreen();
 });
 on(host,'input',e=>{if(e.target.matches('[data-sun-range]'))setSun(Number(e.target.value));});
 on(host,'input',e=>{if(e.target.matches('[data-scene-search]')){query=e.target.value.trim().toLowerCase();renderAssets();}});
 on(host,'input',e=>{if(e.target.id==='scene-case-note'&&caseState)caseState.note=e.target.value;});
 on(host,'change',e=>{
  if(e.target.matches('[data-scene-type]')){type=e.target.value;updateFilters();}
  if(e.target.matches('[data-scene-quality]')){quality=e.target.value;if(renderer){renderer.shadowMap.enabled=quality==='standard';landscape?.quality(quality);resize();}}
  if(e.target.matches('[data-scene-light]')){const preset=e.target.value;if(preset==='timeline')return;setSun(preset==='dusk'?1020:DAYLIGHT_DEFAULT);e.target.value=preset;}
 });
 function syncPanel(){host.classList.toggle('scene-panel-hidden',panelHidden);host.querySelector('[data-scene-panel]').setAttribute('aria-expanded',String(!panelHidden));resize();}
 function setExpanded(value){
  if(expanded===value)return;expanded=value;if(value){savedPanelHidden=panelHidden;oldBodyOverflow=document.body.style.overflow;document.body.style.overflow='hidden';if(innerWidth<650){panelHidden=true;syncPanel();}}else{document.body.style.overflow=oldBodyOverflow;panelHidden=savedPanelHidden;syncPanel();navigation?.clear();}
  host.classList.toggle('scene-expanded',value);host.dataset.sceneExpanded=String(value);const button=host.querySelector('[data-scene-fullscreen]');button.textContent=value?'退出全屏':'⛶ 全屏';button.setAttribute('aria-label',value?'退出全屏':'全屏场景');button.setAttribute('aria-pressed',String(value));resize();
 }
 async function toggleFullscreen(){
  if(expanded){if(document.fullscreenElement===host)await document.exitFullscreen().catch(()=>{});setExpanded(false);nativeFullscreen=false;return;}
  setExpanded(true);
  try{if(host.requestFullscreen){await host.requestFullscreen();nativeFullscreen=document.fullscreenElement===host;}else nativeFullscreen=false;}catch(error){nativeFullscreen=false;}
 }
 on(document,'fullscreenchange',()=>{if(document.fullscreenElement===host){nativeFullscreen=true;setExpanded(true);}else if(nativeFullscreen){nativeFullscreen=false;navigation?.setEnabled(false);setExpanded(false);}});
 on(window,'keydown',event=>{if(event.code==='Escape'&&expanded&&!document.fullscreenElement){setExpanded(false);navigation?.setEnabled(false);}});
 on(window,'blur',pauseSun);
 on(document,'visibilitychange',()=>{if(document.hidden)pauseSun();else invalidate();});
 const exportButton=document.querySelector('[data-scene-export]'),importInput=document.querySelector('[data-scene-import]');
 on(exportButton,'click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(manifest,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='fumai-scene-template.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
 on(importInput,'change',async()=>{const file=importInput.files?.[0];if(!file)return;try{if(file.size>2*1024*1024)throw Error('场景 JSON 不得超过 2 MB。');const parsed=validateManifest(JSON.parse(await file.text()));if(disposed)return;manifest=parsed;caseState=null;walkthroughAvailable=false;renderWalkthrough();events=getEvents();selected=manifest.assets[0].id;type='all';onlyAlerts=false;query='';host.querySelector('[data-scene-type]').value='all';host.querySelector('[data-scene-alerts]').classList.remove('active');host.querySelector('[data-scene-alerts]').setAttribute('aria-pressed','false');host.querySelector('[data-scene-search]').value='';message('已在当前浏览器载入场景，坐标和模型精度待工程验收；未写入服务器。');if(renderer)build();info();renderAssets();renderSelection();}catch(error){message(error.message||'场景格式无效，保留原场景。');}finally{importInput.value='';}});
 return {dispose(){disposed=true;pauseSun();sunObserver?.disconnect();navigation?.dispose();if(document.fullscreenElement===host)void document.exitFullscreen().catch(()=>{});setExpanded(false);importVersion++;if(raf)cancelAnimationFrame(raf);observer?.disconnect();for(const off of listeners)off();controls?.dispose();if(scene)disposeTree(scene);equipment?.dispose();atmosphere?.dispose();renderer?.dispose();renderer?.forceContextLoss();host.dataset.sceneDisposed='true';},stats(){return {frames,drawCalls:draws,assets:manifest.assets.length,backend:'webgl',quality,expanded,daylight:{...sunlight,playing:sunPlaying,lightPosition:light?.position.toArray()},navigation:navigation?.stats(),...equipment?.stats(),active:!disposed};}};
}
