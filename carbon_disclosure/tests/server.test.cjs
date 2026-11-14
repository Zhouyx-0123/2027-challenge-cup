const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {createServer} = require('../server.cjs');

async function withServer(run) {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const request = (url, method='GET') => new Promise((resolve, reject) => {
    const req = http.request({host:'127.0.0.1', port:server.address().port, path:url, method,
      headers:{Connection:'close'}}, res => {
      const chunks=[];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({status:res.statusCode, headers:res.headers, body:Buffer.concat(chunks)}));
    });
    req.on('error', reject); req.end();
  });
  try { await run(request); }
  finally { await new Promise(resolve => server.close(resolve)); }
}

test('可实际启动HTTP服务，首页依赖和PDF逐个可读且与交付文件相同', async () => withServer(async request => {
  const home = await request('/'); assert.equal(home.status, 200);
  const html = home.body.toString();
  const version = require('../package.json').version.split('.').slice(0,2).join('.');
  assert.ok(html.includes('披露碳账 · v'+version));
  const assets = [...html.matchAll(/(?:src|href)="([^"#]+\.(?:js|css))"/g)].map(m=>m[1]);
  assert.equal(assets.length, 11);
  for (const file of [...assets, 'sample.csv', 'methodology.pdf']) {
    const response = await request('/'+file);
    assert.equal(response.status, 200, file);
    assert.deepEqual(response.body, fs.readFileSync(path.join(__dirname,'../dist',file)));
  }
  assert.equal((await request('/methodology.pdf')).headers['content-type'], 'application/pdf');
}));

test('HEAD健康检查不返回正文，GET健康检查报告就绪', async () => withServer(async request => {
  const response = await request('/healthz');
  assert.equal(JSON.parse(response.body).status,'ok');
  const head = await request('/methodology.pdf','HEAD'); assert.equal(head.status,200);
  assert.equal(head.body.length,0); assert.ok(Number(head.headers['content-length'])>1000);
}));

test('服务只暴露页面目录，错误请求不会中止服务', async () => withServer(async request => {
  for (const file of ['/server.cjs','/../package.json','/%2e%2e%5cpackage.json','/no-such-file']) {
    assert.ok([403,404].includes((await request(file)).status),file);
  }
  assert.equal((await request('/%ZZ')).status,400);
  assert.equal((await request('/%00')).status,400);
  assert.equal((await request('/','POST')).status,405);
  assert.equal((await request('/')).status,200);
}));
