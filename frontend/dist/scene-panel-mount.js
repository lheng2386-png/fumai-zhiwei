import * as T from './vendor/three-r180/three.module.min.js';
import {equipmentRecipe} from './scene-equipment-data.js';

export const mountSpec = equipmentRecipe.models.find(model => model.id === 'rack').mount;

/** Same local-to-world transform for the module surface, pick proxy and upper rails. */
export function panelPose(asset, target = new T.Matrix4()) {
    const euler = new T.Euler(...asset.rotation.map(value => value * Math.PI / 180), 'XYZ');
    return target.makeRotationFromEuler(euler).setPosition(...asset.position);
}

/** Uprights remain vertical; their top anchors are derived from the tilted cross rails. */
export function panelMount(asset) {
    const upper = panelPose(asset),normal = new T.Vector3(0,1,0).transformDirection(upper);
    const baseTop = mountSpec.ground_y + mountSpec.pad_size[1];
    const feet = [];
    for (const x of [-mountSpec.leg_x,mountSpec.leg_x]) {
        for (const z of [-mountSpec.leg_z,mountSpec.leg_z]) {
            feet.push(new T.Vector3(x,mountSpec.support_plane_y,z).applyMatrix4(upper));
        }
    }
    // A vertical/underground panel has no justified ground-standing installation.
    if (normal.y < .25 || feet.some(point => point.y <= baseTop + .08)) {
        return {supported:false,upper:null,posts:[],pads:[],braces:[]};
    }
    const xAxis = new T.Vector3(1,0,0).transformDirection(upper);
    const yaw = -Math.atan2(xAxis.z,xAxis.x);
    const rotation = new T.Quaternion().setFromAxisAngle(new T.Vector3(0,1,0),yaw);
    const posts = [],pads = [],braces = [];
    for (const point of feet) {
        const height = point.y - baseTop;
        posts.push(new T.Matrix4().compose(new T.Vector3(point.x,baseTop+height/2,point.z),rotation,new T.Vector3(1,height,1)));
        pads.push(new T.Matrix4().compose(new T.Vector3(point.x,mountSpec.ground_y+mountSpec.pad_size[1]/2,point.z),rotation,new T.Vector3(1,1,1)));
    }
    for (let side = 0; side < 2; side++) {
        const a = feet[side*2],b = feet[side*2+1];
        const start = new T.Vector3(a.x,baseTop+.08,a.z),end = new T.Vector3(b.x,b.y-.08,b.z);
        const delta = end.clone().sub(start),length = delta.length();
        braces.push(new T.Matrix4().compose(start.clone().add(end).multiplyScalar(.5),new T.Quaternion().setFromUnitVectors(new T.Vector3(0,1,0),delta.normalize()),new T.Vector3(1,length,1)));
    }
    return {supported:true,upper,posts,pads,braces};
}
