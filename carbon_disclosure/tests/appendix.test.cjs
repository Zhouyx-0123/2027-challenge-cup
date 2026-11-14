const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const L=require('../dist/core.js'),A=require('../dist/appendix.js'),X=require('../dist/xlsx.js');
const csv=fs.readFileSync(path.join(__dirname,'../dist/sample.csv'),'utf8'),result=L.process(csv),meta={filename:'sample.csv',nature:'模拟',unit:'co2e'};
test('附录按333条接收事实和4种方法版本关联，已有索引不伪装成证据齐全',()=>{
 const a=A.create(result,meta),c=A.completeness(result,a);assert.equal(c.facts,333);assert.equal(c.factors,4);assert.equal(c.recorded,0);assert.equal(c.factorRecorded,0);
 const f=Object.values(a.factors).find(f=>f.factor_id==='F-DIESEL-TTW');assert.equal(f.value,'2.63');assert.equal(f.authoritative_source,'');assert.match(f.source_note,/模拟指定/);assert.equal(A.recordGaps(a.records[result.main[0].fact_id]).length,4);
});
test('真实或未注明批次不自动套用模拟因子，未知版本也不套用',()=>{
 for(const nature of ['真实','未注明']){const a=A.create(result,{nature});assert.ok(Object.values(a.factors).every(f=>f.value===''));}
 const r={main:[{...result.main[0],factor_version:'future-v2'}],side:[]};assert.equal(Object.values(A.create(r,meta).factors)[0].value,'');
});
test('编辑因子与逐条依据不更改任何原始排放、范围、期间或主账总量',()=>{
 const before=JSON.stringify(result.main.map(f=>f.raw)),total=L.plain(L.sum(result.main)),a=A.create(result,meta),id=result.main[0].fact_id;
 Object.values(a.factors)[0].value='9999';a.records[id]={inputs:'输入值0 L（上游提供）',formula:'说明文本，不执行',input_source:'上游资料',rounding:'按原说明',evidence_note:'测试证据登记，未经核验',evidence_url:''};
 assert.equal(A.recordGaps(a.records[id]).length,0);assert.equal(A.completeness(result,a).recorded,1);assert.equal(L.plain(L.sum(result.main)),total);assert.equal(JSON.stringify(result.main.map(f=>f.raw)),before);
});
test('附录导出跟随主账/旁证筛选，并将待补资料明确列出',()=>{
 const a=A.create(result,meta),filter={period:'2026-09',scope:'1'},s=A.sheets(result,filter,meta,a,L),expected=[...L.filter(result.main,filter),...L.filter(result.side,filter)];
 assert.equal(s.length,5);assert.equal(s[3].rows.length-1,expected.length);assert.equal(expected.length,125);assert.equal(s[2].rows.length-1,2);assert.ok(s[4].rows.some(r=>r[0]==='逐条追溯'));assert.equal(s[3].rows[1][5].number,result.main[0].emission_kg);
});
test('备份仅可恢复到同一CSV，既有已登记内容与手动清空均保留',()=>{
 const a=A.create(result,meta);a.notes.entity='演示主体（测试资料）';a.notes.scope1='';a.records[result.main[0].fact_id].inputs='实际输入待补说明';
 const b=A.restore(A.backup(csv,a),csv,result,meta);assert.equal(b.notes.entity,a.notes.entity);assert.equal(b.notes.scope1,'');assert.equal(b.records[result.main[0].fact_id].inputs,'实际输入待补说明');
 assert.throws(()=>A.restore(A.backup(csv,a),csv+'\n',result,meta),/不匹配/);assert.throws(()=>A.restore('bad',csv,result,meta),/JSON/);
});
test('恢复只读取已定义字段和当前事实，不注入新增排放或伪造审核结论',()=>{
 const a=A.create(result,meta);a.records['fake-id']={inputs:'伪造'};a.notes.approved=true;a.records[result.main[0].fact_id].approved=true;
 const b=A.restore(A.backup(csv,a),csv,result,meta);assert.equal(b.notes.approved,undefined);assert.equal(b.records['fake-id'],undefined);assert.equal(b.records[result.main[0].fact_id].approved,undefined);assert.equal(result.main.length,328);
});
test('链接只允许无凭据的http/https，不将文件地址或脚本作为可执行入口',()=>{
 for(const u of ['javascript:alert(1)','data:text/html,hi','file:///C:/secret','https://user:pass@example.com','not a url'])assert.equal(A.link(u),'');assert.equal(A.link('https://example.com/ref'),'https://example.com/ref');
});
test('因子编号或事实ID与对象原型同名也保持普通业务键',()=>{
 const f={...result.main[0],fact_id:'__proto__',factor_id:'constructor'},r={main:[f],side:[]},a=A.create(r,meta);assert.equal(Object.values(a.factors)[0].value,'');assert.equal(A.recordGaps(a.records.__proto__).length,4);assert.equal(Object.getPrototypeOf(a.records),null);
});
test('参考公式不能替代逐条实际公式；全部材料登记后仍标注未核验',()=>{
 const a=A.create(result,meta),f=result.main[0];assert.ok(a.factors[A.key(f)].reference_formula);assert.ok(A.recordGaps(a.records[f.fact_id]).includes('实际计算说明'));
 for(const [k]of A.TRACE_FIELDS)a.records[f.fact_id][k]=k==='evidence_url'?'':'示例登记';const sheets=A.sheets(result,{search:f.fact_id},meta,a,L);assert.equal(sheets[3].rows[1].at(-1),'已登记，未经核验');
});
test('完整工作簿包含11张表，主线结果保持不变',()=>{
 const a=A.create(result,meta),s=[...L.exportTables(result,{},meta),...A.sheets(result,{},meta,a,L)],bytes=X.create(s);assert.equal(s.length,11);assert.equal(s[0].rows.find(r=>r[0]==='合计')[1].number,'98569.602');assert.equal(s[8].rows[1][3].number,'2.63');assert.ok(Buffer.from(bytes).includes(Buffer.from('sheet11.xml')));
});
