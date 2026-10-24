'use strict';
(()=>{
 let generation=0,view=null;
 const render=()=>`<div class="page-intro"><div><span class="eyebrow">SPATIAL OPERATIONS / 空间运维</span><h1>三维电站<span class="scene-beta">工程案例</span></h1><p>定位设备、核对证据、记录核查决定，体验完整的工程分析过程。</p></div><div class="intro-controls"><button class="secondary-button" data-scene-export>下载接入模板 ↗</button><label class="secondary-button scene-import">导入场景<input type="file" accept=".json,application/json" data-scene-import></label></div></div><section id="scene-workbench" class="scene-workbench" aria-label="三维电站工作台"><div class="scene-loading" role="status">正在构建三维场景…</div></section>`;
 function dispose(){generation++;view?.dispose();view=null;}
 async function mount(getEvents){
  const token=++generation,host=document.querySelector('#scene-workbench');
  try{const {createSceneView}=await import('./scene-runtime.js');if(token!==generation||!host.isConnected)return;view=createSceneView(host,{devices:window.FumaiDemo.devices,getEvents,openAgent:id=>window.FumaiAgent.open(id)});}
  catch(error){if(token!==generation||!host.isConnected)return;host.innerHTML='<div class="scene-unavailable"><h2>三维资源暂时不可用</h2><p>请刷新重试；设备和事件仍可在常规页面查看。</p><a class="secondary-button" href="#devices">设备监测 ↗</a></div>';}
 }
 window.FumaiScene={render,mount,dispose,diagnostics:()=>view?.stats()||{active:false}};
})();
