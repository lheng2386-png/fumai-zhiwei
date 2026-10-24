// Adapter for a real, locally built Unity WebGL distribution; never fabricates Wasm output.
export function mountUnityScene(host,{getEvents,devices,openEvidence,openAgent}){
    const frame=document.createElement('iframe');frame.src='./unity-scene/index.html';frame.title='Unity 原生三维电站';frame.allow='fullscreen';frame.style.cssText='width:100%;height:76vh;min-height:560px;border:0;display:block';
    let active=true,ready=false;
    const post=data=>{if(active&&ready)frame.contentWindow?.postMessage({schema:'fumai-unity-v1',...data},location.origin);};
    const firstEvent=id=>getEvents().find(e=>e.inverter===id)?.event_id;
    const receive=event=>{
        if(!active||event.origin!==location.origin||event.source!==frame.contentWindow||event.data?.schema!=='fumai-unity-v1')return;
        const data=event.data;
        if(data.type==='ready'){
            ready=true;host.dataset.sceneReady='unity';
            const events=getEvents();post({type:'monitoring',payload:JSON.stringify({source:'constructed_demo',observed_at:'historical_demo_snapshot',values:devices.map(d=>({id:d.id,state:events.find(e=>e.inverter===d.id&&['unreviewed','needs_investigation'].includes(e.review_status))?.review_status||'no_pending_candidate',has_actual_kw:Number.isFinite(d.actual_kw),actual_kw:d.actual_kw,has_reference_kw:Number.isFinite(d.expected_kw),reference_kw:d.expected_kw}))})});
        }
        if(!/^[A-Za-z0-9_-]{1,64}$/.test(data.id||''))return;
        if(data.type==='selected')host.dispatchEvent(new CustomEvent('fumai-scene-select',{bubbles:true,detail:{id:data.id,source:data.source,engine:'unity'}}));
        if(data.type==='agent'){const id=firstEvent(data.id);if(id)openAgent(id);}
        if(data.type==='evidence'){const id=firstEvent(data.id);if(id)openEvidence(id);}
    };
    window.addEventListener('message',receive);host.replaceChildren(frame);
    return {select(id){if(/^[A-Za-z0-9_-]{1,64}$/.test(id))post({type:'select',id});},dispose(){post({type:'dispose'});active=false;window.removeEventListener('message',receive);frame.remove();},stats(){return {active,ready,engine:'Unity WebGL / Wasm'};}};
}
