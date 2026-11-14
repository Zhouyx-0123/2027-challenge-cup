const test=require('node:test'),assert=require('node:assert/strict');
const {save}=require('../dist/downloads.js');
function windowMock(){
 const events=[],link={click(){events.push('click');},remove(){events.push('remove');}};
 const host={events,link,Blob:class {constructor(parts,options){this.parts=parts;this.type=options.type;}},
  URL:{createObjectURL(blob){events.push(blob);return'blob:ledger-test';},revokeObjectURL(url){events.push(url);}},
  document:{body:{append(node){events.push(node);}},createElement(tag){assert.equal(tag,'a');return link;}},
  setTimeout(fn,ms){assert.equal(ms,30000);host.cleanup=fn;}};
 host.parent=host;return host;
}
test('独立下载保留字节、文件名和类型，并延后释放Blob',()=>{
 const host=windowMock(),data=Uint8Array.from([1,2,3]);save(data,'application/pdf','说明册.pdf',host);
 assert.deepEqual(host.events[0].parts,[data]);assert.equal(host.events[0].type,'application/pdf');
 assert.equal(host.link.download,'说明册.pdf');assert.equal(host.link.href,'blob:ledger-test');
 assert.deepEqual(host.events.slice(2),['click','remove']);host.cleanup();assert.equal(host.events.at(-1),'blob:ledger-test');
});
test('同源嵌入使用外层文档承接下载，跨域嵌入回退到自身',()=>{
 const outer=windowMock(),inner=windowMock();inner.parent=outer;save('xlsx','application/zip','试算.xlsx',inner);
 assert.equal(inner.events.length,0);assert.equal(outer.link.download,'试算.xlsx');
 const blocked=windowMock();Object.defineProperty(blocked,'parent',{get(){throw new Error('Cross origin');}});
 save('{}','application/json','备份.json',blocked);assert.equal(blocked.link.download,'备份.json');
});
