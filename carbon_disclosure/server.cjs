'use strict';
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');

const MIME = {'.html':'text/html; charset=utf-8', '.css':'text/css; charset=utf-8',
  '.js':'text/javascript; charset=utf-8', '.csv':'text/csv; charset=utf-8', '.pdf':'application/pdf'};

function createServer(root = path.join(__dirname, 'dist')) {
  const base = path.resolve(root);
  return http.createServer(async (req, res) => {
    const end = (status, body, type = 'text/plain; charset=utf-8') => {
      const bytes = Buffer.from(body);
      res.writeHead(status, {'Content-Type':type, 'Content-Length':bytes.length,
        'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'});
      res.end(req.method === 'HEAD' ? undefined : bytes);
    };
    if (!['GET', 'HEAD'].includes(req.method)) return end(405, 'GET and HEAD only');
    let pathname;
    try { pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname); }
    catch { return end(400, 'Invalid URL'); }
    if (pathname.includes('\0')) return end(400, 'Invalid URL');
    if (pathname === '/healthz') return end(200, '{"status":"ok","app":"carbon-disclosure"}', 'application/json');
    let file = path.resolve(base, '.' + (pathname === '/' ? '/index.html' : pathname));
    if (!file.startsWith(base + path.sep)) return end(403, 'Forbidden');
    try {
      file = await fs.realpath(file);
      if (!file.startsWith(base + path.sep)) return end(403, 'Forbidden');
      const data = await fs.readFile(file);
      end(200, data, MIME[path.extname(file)] || 'application/octet-stream');
    } catch { end(404, 'Not found'); }
  });
}

if (require.main === module) {
  const port = Number(process.env.PORT || 4318);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    console.error('PORT must be between 0 and 65535'); process.exitCode = 1;
  } else {
    const server = createServer();
    server.on('error', error => { console.error('Cannot start ledger:', error.message); process.exitCode = 1; });
    server.listen(port, '127.0.0.1', () => console.log('Carbon ledger: http://127.0.0.1:' + server.address().port));
  }
}
module.exports = {createServer};
