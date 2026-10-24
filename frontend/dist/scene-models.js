import {GLTFLoader} from './vendor/three-r180/GLTFLoader.js';
import {MeshoptDecoder} from './vendor/three-r180/meshopt_decoder.module.js';
const MAX_BYTES=25*1024*1024;
export async function loadEngineeringModel(path){
 if(!/^\.\/models\/[a-zA-Z0-9_/-]+\.glb$/.test(path))throw Error('模型路径无效');
 const response=await fetch(new URL(path,location.href.split('#')[0]),{credentials:'same-origin'});
 if(!response.ok||Number(response.headers.get('Content-Length'))>MAX_BYTES)throw Error('模型不可用或超出 25 MB');
 const reader=response.body.getReader(),chunks=[];let bytes=0;
 for(;;){const {done,value}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>MAX_BYTES){await reader.cancel();throw Error('模型超出 25 MB');}chunks.push(value);}
 const data=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.length;}
 const view=new DataView(data.buffer);if(bytes<20||view.getUint32(0,true)!==0x46546c67||view.getUint32(4,true)!==2||view.getUint32(8,true)!==bytes||view.getUint32(16,true)!==0x4e4f534a)throw Error('模型必须是自包含 GLB 2.0');
 const length=view.getUint32(12,true);if(length>bytes-20)throw Error('模型 JSON 无效');
 const manifest=JSON.parse(new TextDecoder().decode(data.subarray(20,20+length)));
 for(const item of [...(manifest.buffers||[]),...(manifest.images||[])])if(item.uri&&!/^data:(?:image\/|application\/octet-stream;)/.test(item.uri))throw Error('GLB 不允许外部资源引用');
 if((manifest.extensionsRequired||[]).includes('KHR_draco_mesh_compression'))throw Error('当前接入支持普通或 Meshopt 压缩 GLB，请先转换 Draco 模型');
 const loader=new GLTFLoader();if(MeshoptDecoder.supported){await MeshoptDecoder.ready;loader.setMeshoptDecoder(MeshoptDecoder);}
 return (await loader.parseAsync(data.buffer,'')).scene;
}
