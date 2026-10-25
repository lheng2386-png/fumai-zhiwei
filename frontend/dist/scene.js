'use strict';
(()=>{
 let generation=0,view=null;
 const render=()=>`<section id="scene-workbench" class="scene-workbench scene-tab" aria-label="三维电站工作台"><div class="scene-loading" role="status">正在构建三维场景…</div></section>`;
 function dispose(){generation++;view?.dispose();view=null;}
 async function mount(getEvents){
  const token=++generation,host=document.querySelector('#scene-workbench');
  try{const {createSceneView}=await import('./scene-runtime.js');if(token!==generation||!host.isConnected)return;view=createSceneView(host,{devices:window.FumaiDemo.devices,getEvents,openAgent:id=>window.FumaiAgent.open(id)});}
  catch(error){if(token!==generation||!host.isConnected)return;host.innerHTML='<div class="scene-unavailable"><h2>三维资源暂时不可用</h2><p>请刷新重试；设备和事件仍可在常规页面查看。</p><a class="secondary-button" href="#devices">设备监测 ↗</a></div>';}
 }
 window.FumaiScene={render,mount,dispose,diagnostics:()=>view?.stats()||{active:false}};
})();
