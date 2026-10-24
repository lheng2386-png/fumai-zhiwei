import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const dir=path.join(root,'artifacts/WebGL'),build=path.join(dir,'Build');
const scene=JSON.parse(fs.readFileSync(path.join(root,'artifacts/unity-scene-source.json'),'utf8'));
if(scene.scene_assets!==602||!scene.max_receipt_sha256||!scene.unity_version)throw Error('Native scene provenance missing');
const wasm=fs.readdirSync(build).filter(f=>f.endsWith('.wasm'));
if(!wasm.length)throw Error('A real Unity Wasm build is required');
for(const file of wasm){const handle=fs.openSync(path.join(build,file),'r'),header=Buffer.alloc(8);fs.readSync(handle,header,0,8,0);fs.closeSync(handle);if(!header.equals(Buffer.from([0,97,115,109,1,0,0,0])))throw Error('Invalid Wasm header');}
const html=fs.readFileSync(path.join(dir,'index.html'),'utf8');
if(html.includes('{{{')||!html.includes('createUnityInstance'))throw Error('Unprocessed Unity template');
const files=[];const walk=d=>{for(const item of fs.readdirSync(d,{withFileTypes:true})){const p=path.join(d,item.name);if(item.isDirectory())walk(p);else if(item.name!=='release.json')files.push({path:path.relative(dir,p).replaceAll('\\','/'),bytes:fs.statSync(p).size,sha256:crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')});}};walk(dir);
const report={schema:'fumai-unity-release-v1',engine:'Unity WebGL / IL2CPP Wasm',source:'constructed_demo',verified_at:new Date().toISOString(),...scene,files};
fs.writeFileSync(path.join(dir,'release.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:'native_unity_output_verified',files:files.length,wasm:wasm.length}));
