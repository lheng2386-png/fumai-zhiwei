import * as T from './vendor/three-r180/three.module.min.js';

export function createNavigation({camera,controls,canvas,host,invalidate,onChange}){
    let enabled=false,look=null,lastTime=0,yaw=0,pitch=0,bounds=null;
    const actions=new Map(),listeners=[],direction=new T.Vector3();
    const mapping={KeyW:'forward',ArrowUp:'forward',KeyS:'back',ArrowDown:'back',KeyA:'left',ArrowLeft:'left',KeyD:'right',ArrowRight:'right',KeyQ:'down',KeyE:'up',ShiftLeft:'fast',ShiftRight:'fast'};
    const editable=target=>target?.closest?.('input,textarea,select,[contenteditable="true"],[role="textbox"]');
    const on=(target,name,fn,options)=>{target.addEventListener(name,fn,options);listeners.push(()=>target.removeEventListener(name,fn,options));};
    const pad=document.createElement('div');pad.className='scene-roam-controls';pad.hidden=true;pad.setAttribute('aria-label','自由漫游移动控制');
    pad.innerHTML='<div class="scene-roam-pad"><button data-roam-move="forward" aria-label="向前移动">↑</button><button data-roam-move="left" aria-label="向左移动">←</button><button data-roam-move="back" aria-label="向后移动">↓</button><button data-roam-move="right" aria-label="向右移动">→</button></div><div class="scene-roam-height"><button data-roam-move="up" aria-label="升高站位">升高</button><button data-roam-move="down" aria-label="降低站位">降低</button><button data-roam-reset>站高复位</button></div>';
    canvas.parentElement.append(pad);
    function clear(){actions.clear();look=null;lastTime=0;invalidate();}
    function resetHeight(){camera.position.y=1.65;invalidate();}
    function setEnabled(value){
        if(enabled===value)return;clear();enabled=value;controls.enabled=!value;pad.hidden=!value;host.classList.toggle('scene-roaming',value);
        if(value){const euler=new T.Euler().setFromQuaternion(camera.quaternion,'YXZ');yaw=euler.y;pitch=euler.x;canvas.focus({preventScroll:true});}
        else {camera.getWorldDirection(direction);controls.target.copy(camera.position).addScaledVector(direction,8);controls.target.y=Math.max(.1,controls.target.y);controls.update();}
        onChange(value);invalidate();
    }
    on(window,'keydown',event=>{
        if(!enabled)return;
        if(event.code==='Escape'){setEnabled(false);return;}
        if(event.ctrlKey||event.metaKey||event.altKey||editable(event.target)||!host.contains(document.activeElement))return;
        if(event.code==='KeyR'){event.preventDefault();resetHeight();return;}
        const action=mapping[event.code];if(!action)return;event.preventDefault();actions.set('key:'+event.code,action);invalidate();
    });
    on(window,'keyup',event=>{if(actions.delete('key:'+event.code))invalidate();});
    on(window,'blur',clear);
    on(document,'visibilitychange',()=>{if(document.hidden)clear();});
    on(document,'pointerdown',event=>{if(!host.contains(event.target))clear();});
    on(host,'focusin',event=>{if(editable(event.target))clear();});
    on(canvas,'pointerdown',event=>{
        if(!enabled||event.button!==0&&event.button!==2)return;event.preventDefault();canvas.focus({preventScroll:true});look={id:event.pointerId,x:event.clientX,y:event.clientY};canvas.setPointerCapture?.(event.pointerId);
    });
    on(canvas,'pointermove',event=>{
        if(!enabled||look?.id!==event.pointerId)return;
        yaw-=(event.clientX-look.x)*.004;pitch=T.MathUtils.clamp(pitch-(event.clientY-look.y)*.004,-Math.PI*.47,Math.PI*.47);look.x=event.clientX;look.y=event.clientY;
        camera.quaternion.setFromEuler(new T.Euler(pitch,yaw,0,'YXZ'));invalidate();
    });
    const endLook=event=>{if(look?.id===event.pointerId)look=null;};
    on(canvas,'pointerup',endLook);on(canvas,'pointercancel',endLook);on(canvas,'lostpointercapture',endLook);
    on(pad,'pointerdown',event=>{
        const button=event.target.closest('[data-roam-move]');if(!enabled||!button)return;event.preventDefault();canvas.focus({preventScroll:true});actions.set('touch:'+event.pointerId,button.dataset.roamMove);button.setPointerCapture(event.pointerId);invalidate();
    });
    const endMove=event=>{if(actions.delete('touch:'+event.pointerId))invalidate();};
    on(pad,'pointerup',endMove);on(pad,'pointercancel',endMove);on(pad,'lostpointercapture',endMove);
    on(pad,'click',event=>{if(event.target.closest('[data-roam-reset]'))resetHeight();});
    function moving(){return enabled&&[...actions.values()].some(a=>a!=='fast');}
    function update(now){
        const delta=lastTime?Math.min(.05,Math.max(0,(now-lastTime)/1000)):0;lastTime=now;if(!moving())return false;
        const held=new Set(actions.values()),axis=new T.Vector3(Number(held.has('right'))-Number(held.has('left')),Number(held.has('up'))-Number(held.has('down')),Number(held.has('forward'))-Number(held.has('back')));
        if(axis.lengthSq()){
            axis.normalize().multiplyScalar((held.has('fast')?10:3.2)*delta);
            camera.position.x+=Math.cos(yaw)*axis.x-Math.sin(yaw)*axis.z;
            camera.position.z-=Math.sin(yaw)*axis.x+Math.cos(yaw)*axis.z;
            camera.position.y=T.MathUtils.clamp(camera.position.y+axis.y,.4,200);
            if(bounds){camera.position.x=T.MathUtils.clamp(camera.position.x,bounds.min.x,bounds.max.x);camera.position.z=T.MathUtils.clamp(camera.position.z,bounds.min.z,bounds.max.z);}
        }
        return true;
    }
    return {get enabled(){return enabled;},setEnabled,clear,update,moving,setBounds(box){bounds=box.clone().expandByScalar(60);},stats(){return {mode:enabled?'free':'orbit',position:camera.position.toArray(),yaw,pitch,moving:moving()};},dispose(){setEnabled(false);for(const off of listeners)off();pad.remove();}};
}
