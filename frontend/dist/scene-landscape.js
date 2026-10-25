import * as T from './vendor/three-r180/three.module.min.js';

function seeded(seed){return()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;};}
export function createAtmosphere(scene,renderer){
    const maps=new Map(),generator=new T.PMREMGenerator(renderer);let current='';
    function lighting(mode){
        if(mode===current)return;current=mode;
        if(!maps.has(mode)){
            const dusk=mode==='dusk',canvas=document.createElement('canvas');canvas.width=1024;canvas.height=512;const ctx=canvas.getContext('2d'),gradient=ctx.createLinearGradient(0,0,0,512),random=seeded(7407);
            for(const [stop,color]of(dusk?[[0,'#42546c'],[.4,'#b4a9aa'],[.53,'#e8c5a0'],[1,'#64745b']]:[[0,'#3b84b6'],[.4,'#a3c8da'],[.53,'#e4e7dd'],[1,'#879477']]))gradient.addColorStop(stop,color);
            ctx.fillStyle=gradient;ctx.fillRect(0,0,1024,512);
            for(let i=0;i<55;i++){const x=random()*1024,y=120+random()*80,r=12+random()*45,g=ctx.createRadialGradient(x,y,0,x,y,r);g.addColorStop(0,'#ffffff20');g.addColorStop(1,'#ffffff00');ctx.fillStyle=g;ctx.fillRect(x-r,y-r,r*2,r*2);}
            const sky=new T.CanvasTexture(canvas);sky.colorSpace=T.SRGBColorSpace;sky.mapping=T.EquirectangularReflectionMapping;const environment=generator.fromEquirectangular(sky);maps.set(mode,{sky,environment});
        }
        const entry=maps.get(mode);scene.background=entry.sky;scene.environment=entry.environment.texture;scene.environmentIntensity=.65;scene.fog.color.set(mode==='dusk'?'#baaca3':'#cedad8');
    }
    lighting('day');return {lighting,dispose(){for(const entry of maps.values()){entry.sky.dispose();entry.environment.dispose();}generator.dispose();}};
}
export function addLandscape(parent,renderer,{demonstration=true}={}){
    const random=seeded(3287),groundCanvas=document.createElement('canvas');groundCanvas.width=groundCanvas.height=256;const ctx=groundCanvas.getContext('2d'),pixels=ctx.createImageData(256,256);
    for(let y=0;y<256;y++)for(let x=0;x<256;x++){const n=random(),i=(y*256+x)*4;pixels.data[i]=163+n*40;pixels.data[i+1]=154+n*36;pixels.data[i+2]=127+n*30;pixels.data[i+3]=255;}ctx.putImageData(pixels,0,0);
    const map=new T.CanvasTexture(groundCanvas);map.colorSpace=T.SRGBColorSpace;map.wrapS=map.wrapT=T.RepeatWrapping;map.repeat.set(100,100);map.anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());
    const ground=new T.Mesh(new T.PlaneGeometry(1200,1200),new T.MeshStandardMaterial({map,roughness:.98}));ground.rotation.x=-Math.PI/2;ground.position.y=-.04;ground.receiveShadow=true;parent.add(ground);
    const natural=new T.Group();parent.add(natural);if(!demonstration)return {quality(){}};
    const concrete=new T.MeshStandardMaterial({color:'#b9bdb2',roughness:.93}),metal=new T.MeshStandardMaterial({color:'#71877d',metalness:.7,roughness:.43}),roof=new T.MeshStandardMaterial({color:'#526f65',metalness:.35,roughness:.45});
    function box(size,position,material){const mesh=new T.Mesh(new T.BoxGeometry(...size),material);mesh.position.fromArray(position);mesh.castShadow=true;mesh.receiveShadow=true;parent.add(mesh);return mesh;}
    box([111,.05,3.4],[0,-.012,0],concrete);box([3.7,.055,85],[39,-.008,0],concrete);
    const posts=[];for(const z of[-42,42])for(let x=-52;x<=52;x+=2.6)posts.push([x,1,z]);for(const x of[-52,52])for(let z=-39.4;z<42;z+=2.6)if(!(x===52&&Math.abs(z)<2.5))posts.push([x,1,z]);
    const fence=new T.InstancedMesh(new T.BoxGeometry(.055,2,.055),metal,posts.length),dummy=new T.Object3D();posts.forEach((p,i)=>{dummy.position.fromArray(p);dummy.updateMatrix();fence.setMatrixAt(i,dummy.matrix);});fence.castShadow=true;parent.add(fence);
    for(const y of[.32,1.05,1.8])for(const sign of[-1,1]){box([104,.025,.025],[0,y,sign*42],metal);if(sign<0)box([.025,.025,84],[-52,y,0],metal);else for(const z of[-22.3,22.3])box([.025,.025,39.4],[52,y,z],metal);}
    // The station building is context only, without invented production telemetry.
    box([7,.25,5],[-45,.08,31],concrete);box([6,3.1,4],[-45,1.75,31],new T.MeshStandardMaterial({color:'#d4d7ca',roughness:.83}));box([6.5,.18,4.5],[-45,3.38,31],roof);
    const windowMaterial=new T.MeshStandardMaterial({color:'#345c66',metalness:.5,roughness:.25});for(const x of[-46.7,-43.3])box([1.1,.85,.04],[x,2.1,33.02],windowMaterial);box([.9,2.1,.06],[-45,1.28,33.04],roof);box([.52,.65,.42],[-48.05,1.4,30.7],metal);
    const grassCanvas=document.createElement('canvas');grassCanvas.width=64;grassCanvas.height=128;const gc=grassCanvas.getContext('2d');
    for(let i=0;i<20;i++){const x=random()*64;gc.fillStyle=['#718444','#8a9957','#aaa477'][i%3];gc.beginPath();gc.moveTo(x,128);gc.quadraticCurveTo(x+(random()-.5)*35,55,x+(random()-.5)*30,12+random()*60);gc.lineTo(x+3,128);gc.fill();}
    const grassMap=new T.CanvasTexture(grassCanvas);grassMap.colorSpace=T.SRGBColorSpace;const grass=new T.InstancedMesh(new T.PlaneGeometry(.8,.55),new T.MeshStandardMaterial({map:grassMap,alphaTest:.4,side:T.DoubleSide,roughness:1}),1300);
    for(let i=0;i<1300;i++){let x=(random()-.5)*130,z=(random()-.5)*108;if(Math.abs(z)<2.2)z+=z<0?-4:4;if(Math.abs(x-39)<2.3)x+=x<39?-4:4;dummy.position.set(x,.24,z);dummy.rotation.set(0,random()*Math.PI,0);dummy.scale.setScalar(.45+random()*.75);dummy.updateMatrix();grass.setMatrixAt(i,dummy.matrix);}natural.add(grass);
    return {quality(mode){natural.visible=mode!=='low';}};
}
