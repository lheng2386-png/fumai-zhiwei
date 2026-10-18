import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), 'dist');
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml' };
const port = Number(process.env.PORT || 4173);
const server = http.createServer(async (req, res) => {
    try {
        const url = new URL(req.url, 'http://localhost');
        const relative = decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname);
        const filename = path.resolve(root, '.' + relative);
        if (filename !== root && !filename.startsWith(root + path.sep)) {
            res.writeHead(403).end('Forbidden');
            return;
        }
        const content = await fs.readFile(filename);
        res.writeHead(200, { 'Content-Type': types[path.extname(filename)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
        res.end(content);
    } catch {
        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }).end('Not found');
    }
});
server.listen(port, '127.0.0.1', () => console.log(`Local: http://127.0.0.1:${port}`));
