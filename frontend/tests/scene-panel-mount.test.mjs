import assert from 'node:assert/strict';
import * as T from '../dist/vendor/three-r180/three.module.min.js';
import {equipmentRecipe} from '../dist/scene-equipment-data.js';
import {panelPose,panelMount,mountSpec} from '../dist/scene-panel-mount.js';
import {demoManifest,validateManifest} from '../dist/scene-data.js';

const panel=equipmentRecipe.models.find(m=>m.id==='panel'),rack=equipmentRecipe.models.find(m=>m.id==='rack');
const back=panel.parts.find(p=>p.name==='backsheet'),underside=back.center[1]-back.size[1]/2;
for(const part of rack.parts){assert.ok(part.center[1]+part.size[1]/2<=underside+1e-10,part.name+' must remain below the module backsheet');}
const junction=panel.parts.find(p=>p.name==='junction_box');
for(const part of rack.parts){
    assert.ok(!part.center.every((v,i)=>Math.abs(v-junction.center[i])<(part.size[i]+junction.size[i])/2),'Upper rails must clear the junction box');
}
function maximumLocalY(geometry,matrix,inverse){
    const transform=inverse.clone().multiply(matrix),points=geometry.attributes.position,v=new T.Vector3();let maximum=-Infinity;
    for(let i=0;i<points.count;i++)maximum=Math.max(maximum,v.fromBufferAttribute(points,i).applyMatrix4(transform).y);
    return maximum;
}
const geometries={posts:new T.BoxGeometry(...mountSpec.post_size),pads:new T.BoxGeometry(...mountSpec.pad_size),braces:new T.CylinderGeometry(mountSpec.brace_radius,mountSpec.brace_radius,1,8)};
const cases=[{position:[0,1.25,0],rotation:[-24,0,0]},{position:[11,1.25,-8],rotation:[-24,90,0]},{position:[-3,1.8,6],rotation:[-35,40,8]},{position:[2,1.7,9],rotation:[18,155,-6]}];
for(const asset of cases){
    const mount=panelMount(asset),inverse=panelPose(asset).invert();assert.ok(mount.supported);assert.ok(mount.upper.equals(panelPose(asset)));
    for(const kind of Object.keys(geometries))for(const matrix of mount[kind])assert.ok(maximumLocalY(geometries[kind],matrix,inverse)<underside-1e-3,kind+' must not pierce the panel');
    for(let i=0;i<mount.posts.length;i++){
        const top=new T.Vector3(0,.5,0).applyMatrix4(mount.posts[i]),bottom=new T.Vector3(0,-.5,0).applyMatrix4(mount.posts[i]);
        assert.ok(Math.abs(top.clone().applyMatrix4(inverse).y-mountSpec.support_plane_y)<1e-9,'Post tops must meet the cross-rail anchors');
        assert.ok(Math.abs(bottom.y-(mountSpec.ground_y+mountSpec.pad_size[1]))<1e-9,'Posts must sit on concrete feet');
        const padBottom=new T.Vector3(0,-mountSpec.pad_size[1]/2,0).applyMatrix4(mount.pads[i]);assert.ok(Math.abs(padBottom.y-mountSpec.ground_y)<1e-9,'Concrete feet must remain on the ground');
    }
}
const modules=validateManifest(demoManifest([])).assets.filter(a=>a.type==='panel');assert.ok(modules.every(a=>panelMount(a).supported));
for(const asset of[{position:[0,0,0],rotation:[0,0,0]},{position:[0,1.25,0],rotation:[90,0,0]}])assert.equal(panelMount(asset).supported,false,'Do not fabricate an underground or vertical ground rack');
for(const geometry of Object.values(geometries))geometry.dispose();
console.log(JSON.stringify({poseCases:cases.length,groundSupportedModules:modules.length,panelClearance:'passed',junctionClearance:'passed',anchorAlignment:'passed',unsupportedPoses:2}));
