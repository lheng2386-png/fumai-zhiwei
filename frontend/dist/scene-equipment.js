import * as T from './vendor/three-r180/three.module.min.js';
import {mergeGeometries} from './vendor/three-r180/BufferGeometryUtils.js';
import {equipmentRecipe} from './scene-equipment-data.js';
import {panelPose,panelMount,mountSpec} from './scene-panel-mount.js';

// All dimensions are illustrative metres, not manufacturer or survey data.
export function createEquipmentKit(){
    const geometries=new Set(),textures=new Set(),materials=new Map(),prototypes=new Map(),devices=[];
    let panelAssets=[],panelVisuals=[],rackVisuals=[],mounts=[],panelProxy=null,closePanel=null,closeId='',visibleIds=new Set(),alertIds=new Set();
    const dummy=new T.Object3D(),matrix=new T.Matrix4(),zero=new T.Matrix4().makeScale(0,0,0);
    const canvas=document.createElement('canvas');canvas.width=384;canvas.height=768;const ctx=canvas.getContext('2d');
    ctx.fillStyle='#102937';ctx.fillRect(0,0,384,768);
    for(let row=0;row<12;row++)for(let col=0;col<6;col++){
        const x=col*64+3,y=row*64+3;ctx.fillStyle=(row+col)%4?'#183d53':'#204a62';ctx.fillRect(x,y,58,58);
        ctx.fillStyle='#6f87923a';for(let bus=0;bus<3;bus++)ctx.fillRect(x+10+bus*18,y,1,58);
        ctx.strokeStyle='#35566b50';for(let line=0;line<6;line++){ctx.beginPath();ctx.moveTo(x,y+5+line*9);ctx.lineTo(x+58,y+5+line*9);ctx.stroke();}
    }
    const cellMap=new T.CanvasTexture(canvas);cellMap.colorSpace=T.SRGBColorSpace;textures.add(cellMap);
    for(const spec of equipmentRecipe.materials){const m=new T.MeshStandardMaterial({color:new T.Color(...spec.color),metalness:spec.metallic,roughness:spec.roughness});if(['glass','cell','cellAlt'].includes(spec.id)){m.metalness=.02;m.roughness=.84;m.envMapIntensity=.12;}if(spec.id==='glass'){m.map=cellMap;m.color.set('#99adba');}if(['cell','cellAlt'].includes(spec.id))m.color.multiplyScalar(.35);if(['aluminium','steel','busbar'].includes(spec.id)){m.metalness=.3;m.roughness=.72;m.envMapIntensity=.25;if(spec.id==='aluminium')m.color.set('#607178');if(spec.id==='busbar')m.color.set('#6d8792');}materials.set(spec.id,m);}
    function geometry(part){
        let g;
        if(part.shape==='box'){
            const [w,h,d]=part.size,r=Math.min(part.bevel||0,Math.min(w,h,d)*.18);
            if(r){const s=new T.Shape();s.moveTo(-w/2+r,-h/2);s.lineTo(w/2-r,-h/2);s.quadraticCurveTo(w/2,-h/2,w/2,-h/2+r);s.lineTo(w/2,h/2-r);s.quadraticCurveTo(w/2,h/2,w/2-r,h/2);s.lineTo(-w/2+r,h/2);s.quadraticCurveTo(-w/2,h/2,-w/2,h/2-r);s.lineTo(-w/2,-h/2+r);s.quadraticCurveTo(-w/2,-h/2,-w/2+r,-h/2);g=new T.ExtrudeGeometry(s,{depth:d-2*r,steps:1,bevelEnabled:true,bevelThickness:r,bevelSize:r*.5,bevelSegments:2,curveSegments:3});g.translate(0,0,-d/2+r);}else g=new T.BoxGeometry(w,h,d);
            g.translate(...part.center);
        }else if(part.shape==='cylinder'){
            const a=new T.Vector3(...part.start),b=new T.Vector3(...part.end),direction=b.clone().sub(a);g=new T.CylinderGeometry(part.radius,part.radius,direction.length(),12);g.applyQuaternion(new T.Quaternion().setFromUnitVectors(new T.Vector3(0,1,0),direction.normalize()));g.translate(...a.add(b).multiplyScalar(.5).toArray());
        }else if(part.shape==='torus'){g=new T.TorusGeometry(part.radius,part.tube,6,20);if(part.axis==='x')g.rotateY(Math.PI/2);g.translate(...part.center);}else throw Error('Unsupported equipment geometry');
        if(g.index){const expanded=g.toNonIndexed();g.dispose();return expanded;}return g;
    }
    function prototype(type,level='base'){
        const key=type+':'+level;if(prototypes.has(key))return prototypes.get(key);
        const model=equipmentRecipe.models.find(m=>m.id===type),buckets=new Map();
        for(const part of model.parts){if(level==='base'&&part.detail||level==='detail'&&!part.detail)continue;if(!buckets.has(part.material))buckets.set(part.material,[]);buckets.get(part.material).push(geometry(part));}
        const list=[];for(const [id,parts]of buckets){const merged=mergeGeometries(parts,false);parts.forEach(g=>g.dispose());if(!merged)throw Error('Equipment merge failed');geometries.add(merged);list.push({geometry:merged,material:materials.get(id)});}prototypes.set(key,list);return list;
    }
    function meshes(type,level){const root=new T.Group();for(const part of prototype(type,level)){const mesh=new T.Mesh(part.geometry,part.material);mesh.castShadow=true;mesh.receiveShadow=true;root.add(mesh);}return root;}
    function createDevice(type){const root=new T.Group();root.add(meshes(type,'base'));const detail=meshes(type,'detail');detail.visible=false;root.add(detail);devices.push({root,detail,type});return root;}
    function pose(asset){return panelPose(asset,dummy.matrix);}
    function syncRacks(){
        for(const {mesh,kind,count}of rackVisuals){
            for(let i=0;i<panelAssets.length;i++)for(let part=0;part<count;part++){
                const mount=mounts[i],show=visibleIds.has(panelAssets[i].id)&&mount.supported;
                const transform=show?(kind==='upper'?mount.upper:mount[kind][part]):zero;
                mesh.setMatrixAt(i*count+part,transform);
            }
            mesh.instanceMatrix.needsUpdate=true;mesh.computeBoundingSphere();
        }
    }
    function addRackBatch(parent,geometry,material,kind,count){
        const mesh=new T.InstancedMesh(geometry,material,panelAssets.length*count);mesh.castShadow=true;mesh.receiveShadow=true;mesh.userData.mountKind=kind;parent.add(mesh);rackVisuals.push({mesh,kind,count});
    }

    function addPanels(parent,assets){
        panelAssets=assets;visibleIds=new Set(assets.map(a=>a.id));
        const proxyGeometry=new T.BoxGeometry(1.5,.09,2.2),proxyMaterial=new T.MeshBasicMaterial({visible:false});geometries.add(proxyGeometry);materials.set('pick-proxy',proxyMaterial);
        panelProxy=new T.InstancedMesh(proxyGeometry,proxyMaterial,assets.length);panelProxy.userData.panelInstances=true;parent.add(panelProxy);
        for(const part of prototype('panel','base')){const mesh=new T.InstancedMesh(part.geometry,part.material,assets.length);mesh.castShadow=true;mesh.receiveShadow=true;parent.add(mesh);panelVisuals.push(mesh);}
        mounts=assets.map(panelMount);
        for(const part of prototype('rack','base'))addRackBatch(parent,part.geometry,part.material,'upper',1);
        const postGeometry=new T.BoxGeometry(...mountSpec.post_size),padGeometry=new T.BoxGeometry(...mountSpec.pad_size),braceGeometry=new T.CylinderGeometry(mountSpec.brace_radius,mountSpec.brace_radius,1,8);
        for(const geometry of[postGeometry,padGeometry,braceGeometry])geometries.add(geometry);
        addRackBatch(parent,postGeometry,materials.get('steel'),'posts',4);addRackBatch(parent,padGeometry,materials.get('concrete'),'pads',4);addRackBatch(parent,braceGeometry,materials.get('steel'),'braces',2);syncRacks();
        closePanel=meshes('panel','all');closePanel.visible=false;parent.add(closePanel);syncPanels();return panelProxy;
    }
    function syncPanels(){
        if(!panelProxy)return;
        for(let i=0;i<panelAssets.length;i++){
            const asset=panelAssets[i],show=visibleIds.has(asset.id);matrix.copy(show?pose(asset):zero);panelProxy.setMatrixAt(i,matrix);

            for(const mesh of panelVisuals){mesh.setMatrixAt(i,show&&asset.id!==closeId?matrix:zero);mesh.setColorAt(i,new T.Color(alertIds.has(asset.id)?'#e9c1b7':'#ffffff'));}
        }
        panelProxy.instanceMatrix.needsUpdate=true;panelProxy.computeBoundingSphere();
        for(const mesh of panelVisuals){mesh.instanceMatrix.needsUpdate=true;mesh.instanceColor.needsUpdate=true;mesh.computeBoundingSphere();}
    }
    function filterPanels(allowed,alert){visibleIds=new Set(panelAssets.filter(allowed).map(a=>a.id));alertIds=new Set(panelAssets.filter(alert).map(a=>a.id));syncRacks();syncPanels();}
    function update(camera,selected,quality){
        for(const entry of devices){const position=entry.root.getWorldPosition(new T.Vector3());entry.detail.visible=quality==='standard'&&camera.position.distanceTo(position)<(entry.type==='transformer'?30:20);}
        const asset=panelAssets.find(a=>a.id===selected),next=quality==='standard'&&asset&&visibleIds.has(asset.id)&&camera.position.distanceTo(new T.Vector3(...asset.position))<12?asset.id:'';
        if(next!==closeId){closeId=next;if(closePanel){closePanel.visible=!!next;if(next){closePanel.position.fromArray(asset.position);closePanel.rotation.set(...asset.rotation.map(n=>n*Math.PI/180));}}syncPanels();}
    }
    return {createDevice,addPanels,filterPanels,update,stats:()=>({profile:'browser-detail-v1',mounts:{supported:mounts.filter(m=>m.supported).length,unsupported:mounts.filter(m=>!m.supported).length},surface:{style:'matte-pv-v1',glassRoughness:materials.get('glass').roughness,glassMetalness:materials.get('glass').metalness,glassEnvironment:materials.get('glass').envMapIntensity,frameRoughness:materials.get('aluminium').roughness},closePanel:closeId,nearDevices:devices.filter(d=>d.detail.visible&&d.root.visible).length}),dispose(){for(const g of geometries)g.dispose();for(const t of textures)t.dispose();for(const m of materials.values())m.dispose();}};
}
