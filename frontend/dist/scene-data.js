export const TYPES={panel:'光伏组件',inverter:'逆变器',transformer:'箱式变压器'};
export function demoManifest(devices){
 const assets=[];
 for(let i=0;i<48;i++){
  const x=(i%8-3.5)*25,z=(Math.floor(i/8)-2.5)*29;
  const inverter=devices[i]?.id||`inv_${String(i+1).padStart(2,'0')}`;
  const zone=`${String.fromCharCode(65+Math.floor(i/8))} 区`;
  assets.push({id:inverter,type:'inverter',name:devices[i]?.name||`逆变器 ${String(i+1).padStart(2,'0')}`,position:[x+11,0,z+11.6],zone,group:`trafo_${Math.floor(i/12)+1}`});
  for(let row=0;row<8;row++)for(let col=0;col<12;col++)assets.push({id:`pv_${String(i*96+row*12+col+1).padStart(4,'0')}`,type:'panel',name:`组件 ${i*96+row*12+col+1}`,position:[x+(col-5.5)*1.65,1.25,z+(row-3.5)*2.7],zone,group:inverter,rotation:[-24,0,0]});
 }
 for(let i=0;i<4;i++)assets.push({id:`trafo_${i+1}`,type:'transformer',name:`箱变 ${String(i+1).padStart(2,'0')}`,position:[118,0,(i-1.5)*48],zone:'配电区域'});
 return {version:1,source:'constructed_demo',name:'伏脉 · 光伏电站示意场景',units:'m',up:'Y',coordinateSystem:'local_demo',origin:[0,0,0],assets};
}
export function validateManifest(input){
 if(!input||input.version!==1||input.units!=='m'||input.up!=='Y'||!['constructed_demo','engineering_design','field_survey'].includes(input.source))throw Error('场景须为 version=1、米制、Y 向上，并注明数据来源。');
 if(!Array.isArray(input.assets)||!input.assets.length||input.assets.length>5000)throw Error('场景设备数量须在 1–5000 之间。');
 const ids=new Set();
 const triple=(v,min,max)=>Array.isArray(v)&&v.length===3&&v.every(n=>Number.isFinite(n)&&n>=min&&n<=max);
 const assets=input.assets.map(a=>{
  if(!a||typeof a.id!=='string'||!/^[a-zA-Z0-9_-]{1,64}$/.test(a.id)||ids.has(a.id)||!Object.hasOwn(TYPES,a.type)||!triple(a.position,-10000,10000))throw Error('设备编号、类型或坐标无效，或存在重复编号。');
  ids.add(a.id);
  if(a.rotation&&!triple(a.rotation,-360,360)||a.scale&&!triple(a.scale,0.001,100))throw Error('旋转角度或模型缩放超出范围。');
  if(a.model&&!/^\.\/models\/[a-zA-Z0-9_/-]+\.glb$/.test(a.model))throw Error('模型只允许引用本站 ./models/ 下的 .glb 文件。');
  return {id:a.id,type:a.type,name:String(a.name||a.id).slice(0,80),zone:String(a.zone||'未分区').slice(0,40),position:[...a.position],rotation:a.rotation||[0,0,0],scale:a.scale||[1,1,1],group:typeof a.group==='string'?a.group:undefined,model:a.model||undefined};
 });
 for(const a of assets)if(a.group&&!ids.has(a.group))throw Error('关联设备编号不在场景中。');
 return {version:1,source:input.source,name:String(input.name||'导入场景').slice(0,80),units:'m',up:'Y',coordinateSystem:String(input.coordinateSystem||'local').slice(0,80),origin:triple(input.origin,-1e9,1e9)?input.origin:[0,0,0],assets};
}
export function assetEvents(asset,events,source){return source==='constructed_demo'?events.filter(e=>e.inverter===(asset.type==='panel'?asset.group:asset.id)):[];}
export function pendingEvents(asset,events,source){return assetEvents(asset,events,source).filter(e=>e.review_status==='unreviewed'||e.review_status==='needs_investigation');}
