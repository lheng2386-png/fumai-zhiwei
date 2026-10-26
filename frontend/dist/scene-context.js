import * as T from './vendor/three-r180/three.module.min.js';
import {mergeGeometries} from './vendor/three-r180/BufferGeometryUtils.js';
import {contextLayout} from './scene-context-data.js';

/** Peripheral buildings and vegetation; all geometry is illustrative and non-pickable. */
export function addIndustrialContext(parent,bounds) {
    const layout=contextLayout(bounds),root=new T.Group(),fine=new T.Group();
    root.name='Constructed industrial surroundings';parent.add(root);root.add(fine);
    const baseBuckets=new Map(),fineBuckets=new Map(),dummy=new T.Object3D();
    const materials={
        wall:new T.MeshStandardMaterial({color:'#c0c7c3',roughness:.91}),
        trim:new T.MeshStandardMaterial({color:'#738783',roughness:.83,metalness:.12}),
        roof:new T.MeshStandardMaterial({color:'#526b70',roughness:.82,metalness:.16}),
        window:new T.MeshStandardMaterial({color:'#3f6973',roughness:.78,metalness:.04,envMapIntensity:.2}),
        door:new T.MeshStandardMaterial({color:'#788b8d',roughness:.87}),
        concrete:new T.MeshStandardMaterial({color:'#a8afa5',roughness:1}),
        road:new T.MeshStandardMaterial({color:'#777e74',roughness:1}),
        marking:new T.MeshStandardMaterial({color:'#c5c5a3',roughness:1}),
        lawn:new T.MeshStandardMaterial({color:'#7d8c61',roughness:1}),
        bark:new T.MeshStandardMaterial({color:'#6d6450',roughness:1}),
        leaves:new T.MeshStandardMaterial({color:'#64855a',roughness:1,metalness:0})
    };
    function batch(geometry,material,detailed=false) {
        if(geometry.index){const expanded=geometry.toNonIndexed();geometry.dispose();geometry=expanded;}
        const buckets=detailed?fineBuckets:baseBuckets;
        if(!buckets.has(material))buckets.set(material,[]);buckets.get(material).push(geometry);
    }
    function box(size,position,material,detailed=false,rotation=0) {
        const geometry=new T.BoxGeometry(...size);if(rotation)geometry.rotateZ(rotation);geometry.translate(...position);batch(geometry,material,detailed);
    }
    function factory(b) {
        const {x,z,width:w,depth:d,height:h}=b,angle=.13,rise=w/2*Math.tan(angle);
        box([w+8,.12,d+9],[x,.025,z],materials.concrete);
        box([w,h,d],[x,h/2,z],materials.wall);
        box([w,.35,.12],[x,1,z-d/2-.08],materials.trim);
        for(const side of[-1,1]){
            box([w/2+1.1,.18,d+1.4],[x+side*w/4,h+rise/2,z],materials.roof,false,-side*angle);
            const shape=new T.Shape();shape.moveTo(-w/2,0);shape.lineTo(w/2,0);shape.lineTo(0,rise);shape.closePath();
            const geometry=new T.ShapeGeometry(shape);if(side>0)geometry.rotateY(Math.PI);geometry.translate(x,h,z+side*(d/2+.015));batch(geometry,materials.wall);
        }
        box([.25,.22,d+1.6],[x,h+rise,z],materials.trim);
        for(let dx=-w/2+5;dx<w/2-3;dx+=9){
            box([5.2,1.2,.08],[x+dx,h-2,z-d/2-.06],materials.window);
            box([.1,1.25,.12],[x+dx,h-2,z-d/2-.11],materials.trim,true);
        }
        for(const dx of[-w*.28,0,w*.28]){
            box([5,4.2,.15],[x+dx,2.15,z-d/2-.1],materials.door);
            for(const side of[-1,1])box([.15,4.4,.3],[x+dx+side*2.58,2.2,z-d/2-.16],materials.trim);
            for(let y=.5;y<4.2;y+=.45)box([4.9,.035,.04],[x+dx,y,z-d/2-.2],materials.trim,true);
        }
        for(let dx=-w/2+2;dx<w/2;dx+=3.5){
            const side=dx<0?-1:1,y=h+rise-Math.abs(dx)*Math.tan(angle)+.13;
            box([.035,.07,d+1.3],[x+dx,y,z],materials.trim,true,-side*angle);
        }
        for(const dz of[-d*.27,d*.27]){
            box([1.6,.65,2.6],[x,h+rise+.38,z+dz],materials.trim,true);
            box([2,.12,3],[x,h+rise+.78,z+dz],materials.roof,true);
        }
    }
    function operations(b) {
        const {x,z,width:w,depth:d,height:h}=b;
        box([w+6,.15,d+6],[x,.04,z],materials.concrete);
        box([w+6,.04,7],[x,.035,z-d/2-5.5],materials.concrete);
        box([w,h,d],[x,h/2,z],materials.wall);box([w+1.2,.28,d+1.2],[x,h+.12,z],materials.trim);
        for(const y of[1.6,3.9])for(let dx=-w/2+2;dx<w/2;dx+=3.8)box([2.6,1.25,.1],[x+dx,y,z-d/2-.06],materials.window);
        box([2.1,2.7,.18],[x,1.4,z-d/2-.12],materials.door);box([4.5,.15,2.6],[x,3.2,z-d/2-.8],materials.roof);
        for(const dx of[-3,3])box([2,.8,1.7],[x+dx,h+.65,z+2],materials.trim,true);
        for(let i=0;i<7;i++){
            const px=x-w/2+2+i*3;box([.1,.025,5],[px,.07,z-d/2-6.5],materials.marking,true);
        }
    }
    const {plot}=layout,cx=(plot.minX+plot.maxX)/2,cz=(plot.minZ+plot.maxZ)/2,w=plot.maxX-plot.minX,d=plot.maxZ-plot.minZ;
    // Perimeter lawns and circulation routes stay outside the equipment fence.
    box([w+100,.035,18],[cx,.005,plot.maxZ+65],materials.lawn);
    box([15,.035,d+70],[plot.minX-20,.005,cz+12],materials.lawn);
    box([16,.035,d+65],[plot.maxX+72,.005,cz+12],materials.lawn);
    box([72,.055,6],[plot.maxX+36,-.003,0],materials.road);
    box([6,.055,d/2+42],[plot.maxX+16,-.003,(plot.maxZ+40)/2],materials.road);
    box([w+36,.055,6],[cx+10,.002,plot.maxZ+14],materials.road);
    for(let x=plot.maxX+4;x<plot.maxX+65;x+=7)box([3,.02,.12],[x,.04,0],materials.marking,true);
    layout.buildings.forEach(b=>b.kind==='factory'?factory(b):operations(b));
    for(const [buckets,group]of[[baseBuckets,root],[fineBuckets,fine]])for(const [material,parts]of buckets){
        const geometry=mergeGeometries(parts,false);parts.forEach(g=>g.dispose());
        const mesh=new T.Mesh(geometry,material);mesh.castShadow=true;mesh.receiveShadow=true;group.add(mesh);
    }
    const trees=layout.trees,trunks=new T.InstancedMesh(new T.CylinderGeometry(.12,.2,1,7),materials.bark,trees.length);
    const crownGeometry=new T.IcosahedronGeometry(1,2),points=crownGeometry.attributes.position;
    for(let i=0;i<points.count;i++){const x=points.getX(i),y=points.getY(i),z=points.getZ(i),scale=1+.08*Math.sin(x*13+y*7+z*11);points.setXYZ(i,x*scale,y*scale,z*scale);const length=Math.hypot(x,y,z);crownGeometry.attributes.normal.setXYZ(i,x/length,y/length,z/length);}
    const crowns=new T.InstancedMesh(crownGeometry,materials.leaves,trees.length*3),simple=new T.InstancedMesh(new T.IcosahedronGeometry(1,1),materials.leaves,trees.length);
    trees.forEach((tree,i)=>{
        dummy.position.set(tree.x,tree.height*.25,tree.z);dummy.scale.set(1,tree.height*.5,1);dummy.rotation.set(0,tree.turn,0);dummy.updateMatrix();trunks.setMatrixAt(i,dummy.matrix);
        for(let j=0;j<3;j++){
            const angle=tree.turn+j*Math.PI*2/3,spread=j?tree.radius*.38:0;
            dummy.position.set(tree.x+Math.cos(angle)*spread,tree.height*(j===0?.74:.61),tree.z+Math.sin(angle)*spread);dummy.scale.set(tree.radius*(j===0?.85:.75),tree.height*(j===0?.3:.23),tree.radius*.8);dummy.rotation.set(0,angle,0);dummy.updateMatrix();crowns.setMatrixAt(i*3+j,dummy.matrix);crowns.setColorAt(i*3+j,new T.Color().setHSL(.26+tree.tint*.045,.19+tree.tint*.12,.24+tree.tint*.12));
        }
        dummy.position.set(tree.x,tree.height*.65,tree.z);dummy.scale.set(tree.radius,tree.height*.36,tree.radius);dummy.updateMatrix();simple.setMatrixAt(i,dummy.matrix);simple.setColorAt(i,new T.Color().setHSL(.28,.24,.29+tree.tint*.06));
    });
    for(const mesh of[trunks,crowns,simple]){mesh.castShadow=true;mesh.receiveShadow=true;root.add(mesh);}simple.visible=false;
    let mode='standard';
    return {
        quality(value){mode=value;fine.visible=value!=='low';crowns.visible=value!=='low';simple.visible=value==='low';},
        stats(){return {source:layout.source,buildings:layout.buildings.length,trees:trees.length,crownInstances:trees.length*(mode==='low'?1:3),fineVisible:fine.visible};}
    };
}
