import * as T from './vendor/three-r180/three.module.min.js';

function seeded(seed){return()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;};}
export function createAtmosphere(scene,renderer){
    const generator=new T.PMREMGenerator(renderer),canvas=document.createElement('canvas');
    canvas.width=512;canvas.height=256;
    const ctx=canvas.getContext('2d'),gradient=ctx.createLinearGradient(0,0,0,256);
    for(const [stop,color] of [[0,'#5b9fc9'],[.5,'#dce3d6'],[1,'#879477']])gradient.addColorStop(stop,color);
    ctx.fillStyle=gradient;ctx.fillRect(0,0,512,256);
    const map=new T.CanvasTexture(canvas);map.colorSpace=T.SRGBColorSpace;map.mapping=T.EquirectangularReflectionMapping;
    const environment=generator.fromEquirectangular(map);scene.environment=environment.texture;
    // One cached environment; sky colours and sun direction use uniforms during playback.
    const material=new T.ShaderMaterial({
        side:T.BackSide,depthWrite:false,fog:false,
        uniforms:{zenith:{value:new T.Color()},horizon:{value:new T.Color()},ground:{value:new T.Color('#879477')},sunDirection:{value:new T.Vector3()},sunColor:{value:new T.Color()}},
        vertexShader:`varying vec3 skyDirection;void main(){skyDirection=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`,
        fragmentShader:`uniform vec3 zenith,horizon,ground,sunDirection,sunColor;varying vec3 skyDirection;
            void main(){vec3 direction=normalize(skyDirection);vec3 sky=mix(horizon,zenith,smoothstep(0.0,.85,max(direction.y,0.0)));
            sky=mix(sky,ground,smoothstep(0.0,.6,max(-direction.y,0.0)));
            float alignment=max(dot(direction,sunDirection),0.0);sky+=sunColor*(pow(alignment,500.0)*1.6+pow(alignment,24.0)*.18);
            gl_FragColor=vec4(sky,1.0);
            #include <tonemapping_fragment>
            #include <colorspace_fragment>
            }`
    });
    const geometry=new T.SphereGeometry(900,32,16),sky=new T.Mesh(geometry,material);sky.frustumCulled=false;sky.renderOrder=-1;scene.add(sky);
    return {
        center(position){sky.position.copy(position);},
        sunlight(sample){
            material.uniforms.zenith.value.set('#3b84b6').lerp(new T.Color('#42546c'),sample.warmth);
            material.uniforms.horizon.value.set('#e4e7dd').lerp(new T.Color('#e8b18b'),sample.warmth);
            material.uniforms.sunColor.value.set('#fff6dc').lerp(new T.Color('#ffb26f'),sample.warmth);
            material.uniforms.sunDirection.value.fromArray(sample.sunPosition).normalize();
            scene.environmentIntensity=.5+(1-sample.warmth)*.15;
            scene.fog.color.set('#cedad8').lerp(new T.Color('#baaca3'),sample.warmth);
        },
        dispose(){scene.remove(sky);geometry.dispose();material.dispose();map.dispose();environment.dispose();generator.dispose();}
    };
}
export function addLandscape(parent,renderer,{demonstration=true,bounds}={}){
    const random=seeded(3287),groundCanvas=document.createElement('canvas');groundCanvas.width=groundCanvas.height=256;const ctx=groundCanvas.getContext('2d'),pixels=ctx.createImageData(256,256);
    for(let y=0;y<256;y++)for(let x=0;x<256;x++){const n=random(),i=(y*256+x)*4;pixels.data[i]=163+n*40;pixels.data[i+1]=154+n*36;pixels.data[i+2]=127+n*30;pixels.data[i+3]=255;}ctx.putImageData(pixels,0,0);
    const map=new T.CanvasTexture(groundCanvas);map.colorSpace=T.SRGBColorSpace;map.wrapS=map.wrapT=T.RepeatWrapping;map.repeat.set(100,100);map.anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());
    const ground=new T.Mesh(new T.PlaneGeometry(Math.max(1200,bounds?bounds.getSize(new T.Vector3()).length()+200:1200),Math.max(1200,bounds?bounds.getSize(new T.Vector3()).length()+200:1200)),new T.MeshStandardMaterial({map,roughness:.98}));ground.rotation.x=-Math.PI/2;if(bounds)ground.position.copy(bounds.getCenter(new T.Vector3()));ground.position.y=-.04;ground.receiveShadow=true;parent.add(ground);
    const natural=new T.Group();parent.add(natural);if(!demonstration)return {quality(){}};
    const concrete=new T.MeshStandardMaterial({color:'#b9bdb2',roughness:.93}),metal=new T.MeshStandardMaterial({color:'#71877d',metalness:.7,roughness:.43}),roof=new T.MeshStandardMaterial({color:'#526f65',metalness:.35,roughness:.45});
    function box(size,position,material){const mesh=new T.Mesh(new T.BoxGeometry(...size),material);mesh.position.fromArray(position);mesh.castShadow=true;mesh.receiveShadow=true;parent.add(mesh);return mesh;}
    const minX=bounds.min.x-14,maxX=bounds.max.x+14,minZ=bounds.min.z-14,maxZ=bounds.max.z+14,cx=(minX+maxX)/2,cz=(minZ+maxZ)/2,width=maxX-minX,depth=maxZ-minZ,roadX=maxX-20;
    box([width,.05,4],[cx,-.012,0],concrete);box([4,.055,depth],[roadX,-.008,cz],concrete);
    for(const z of[-58,-29,29,58])box([width-28,.045,2.3],[cx-10,-.015,z],concrete);
    const posts=[];for(const z of[minZ,maxZ])for(let x=minX;x<=maxX;x+=3.5)posts.push([x,1,z]);for(const x of[minX,maxX])for(let z=minZ+3.5;z<maxZ;z+=3.5)if(!(x===maxX&&Math.abs(z)<3))posts.push([x,1,z]);
    const fence=new T.InstancedMesh(new T.BoxGeometry(.055,2,.055),metal,posts.length),dummy=new T.Object3D();posts.forEach((p,i)=>{dummy.position.fromArray(p);dummy.updateMatrix();fence.setMatrixAt(i,dummy.matrix);});fence.castShadow=true;parent.add(fence);
    for(const y of[.32,1.05,1.8]){for(const z of[minZ,maxZ])box([width,.025,.025],[cx,y,z],metal);box([.025,.025,depth],[minX,y,cz],metal);for(const [a,b]of[[minZ,-3],[3,maxZ]])box([.025,.025,b-a],[maxX,y,(a+b)/2],metal);}
    // Context building and circulation routes are illustrative, not surveyed.
    const bx=minX+7,bz=maxZ-9;
    box([7,.25,5],[bx,.08,bz],concrete);box([6,3.1,4],[bx,1.75,bz],new T.MeshStandardMaterial({color:'#d4d7ca',roughness:.83}));box([6.5,.18,4.5],[bx,3.38,bz],roof);
    const windowMaterial=new T.MeshStandardMaterial({color:'#345c66',metalness:.5,roughness:.25});for(const x of[bx-1.7,bx+1.7])box([1.1,.85,.04],[x,2.1,bz+2.02],windowMaterial);box([.9,2.1,.06],[bx,1.28,bz+2.04],roof);box([.52,.65,.42],[bx-3.05,1.4,bz-.3],metal);
    const grassCanvas=document.createElement('canvas');grassCanvas.width=64;grassCanvas.height=128;const gc=grassCanvas.getContext('2d');
    for(let i=0;i<20;i++){const x=random()*64;gc.fillStyle=['#718444','#8a9957','#aaa477'][i%3];gc.beginPath();gc.moveTo(x,128);gc.quadraticCurveTo(x+(random()-.5)*35,55,x+(random()-.5)*30,12+random()*60);gc.lineTo(x+3,128);gc.fill();}
    const grassMap=new T.CanvasTexture(grassCanvas);grassMap.colorSpace=T.SRGBColorSpace;const grass=new T.InstancedMesh(new T.PlaneGeometry(.8,.55),new T.MeshStandardMaterial({map:grassMap,alphaTest:.4,side:T.DoubleSide,roughness:1}),2300);
    for(let i=0;i<2300;i++){let x=minX-8+random()*(width+16),z=minZ-8+random()*(depth+16);if([0,-58,-29,29,58].some(road=>Math.abs(z-road)<2.5))z+=3.5;if(Math.abs(x-roadX)<2.8)x+=4;dummy.position.set(x,.24,z);dummy.rotation.set(0,random()*Math.PI,0);dummy.scale.setScalar(.45+random()*.75);dummy.updateMatrix();grass.setMatrixAt(i,dummy.matrix);}natural.add(grass);
    return {quality(mode){natural.visible=mode!=='low';}};
}
