import assert from 'node:assert/strict';
import fs from 'node:fs';
import {demoManifest,validateManifest,assetEvents,pendingEvents} from '../dist/scene-data.js';
import {MeshoptDecoder} from '../dist/vendor/three-r180/meshopt_decoder.module.js';
const scene=validateManifest(demoManifest([]));
assert.equal(scene.assets.length,602);assert.equal(scene.assets.filter(a=>a.type==='panel').length,576);
assert.equal(new Set(scene.assets.map(a=>a.id)).size,602);
for(const mutate of [s=>s.assets.push(s.assets[0]),s=>s.assets[0].position[1]=NaN,s=>s.assets[0].model='https://example.test/model.glb',s=>s.assets[0].model='./models/../x.glb',s=>s.assets[0].group='missing',s=>s.units='mm',s=>s.assets[0].scale=[0,1,1]]){
 const value=structuredClone(scene);mutate(value);assert.throws(()=>validateManifest(value));
}
const inverter=scene.assets.find(a=>a.id==='inv_07'),events=[{inverter:'inv_07',review_status:'unreviewed'}];
assert.equal(pendingEvents(inverter,events,'constructed_demo').length,1);
assert.equal(assetEvents(inverter,events,'field_survey').length,0,'Imported scene must not inherit demo business events');
await MeshoptDecoder.ready;assert.ok(MeshoptDecoder.supported);
const glb=fs.readFileSync(new URL('../dist/models/meshopt-sample.glb',import.meta.url));const len=glb.readUInt32LE(12);const json=JSON.parse(glb.subarray(20,20+len).toString());const bin=glb.subarray(28+len);const ext=json.bufferViews[0].extensions.EXT_meshopt_compression;
const target=new Uint8Array(ext.count*ext.byteStride);MeshoptDecoder.decodeGltfBuffer(target,ext.count,ext.byteStride,bin.subarray(ext.byteOffset,ext.byteOffset+ext.byteLength),ext.mode,ext.filter);
assert.deepEqual([...new Float32Array(target.buffer)],[-1,0,-1,1,0,-1,1,2,-1,-1,2,-1,-1,0,1,1,0,1,1,2,1,-1,2,1]);
console.log(JSON.stringify({manifest:'passed',invalidCases:7,demoIsolation:'passed',wasmDecodedVertices:8,compressedAttributeBytes:ext.byteLength}));
