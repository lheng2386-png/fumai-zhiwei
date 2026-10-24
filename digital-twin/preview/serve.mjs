// Local geometry QA only. This is explicitly not a Unity build.
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const vendor=path.resolve(root,'../frontend/dist/vendor');
const types={'.html':'text/html; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json'};
http.createServer(async(req,res)=>{try{const url=new URL(req.url,'http://localhost');const base=url.pathname.startsWith('/vendor/')?vendor:root;const relative=url.pathname==='/'?'preview/index.html':url.pathname.replace(/^\/(?:vendor\/)?/,'');const file=path.resolve(base,relative);if(!file.startsWith(base+path.sep))throw Error('Invalid path');const data=await fs.readFile(file);res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'}).end(data);}catch{res.writeHead(404).end('Not found');}}).listen(4206,'127.0.0.1',()=>console.log('Geometry QA: http://127.0.0.1:4206'));
