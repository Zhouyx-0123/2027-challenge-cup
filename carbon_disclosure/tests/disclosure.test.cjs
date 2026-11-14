const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const L=require('../dist/core.js'),A=require('../dist/appendix.js'),D=require('../dist/disclosure.js');
const csv=fs.readFileSync(path.join(__dirname,'../dist/sample.csv'),'utf8'),result=L.process(csv),meta={nature:'模拟',unit:'co2e',filename:'sample.csv'};
test('披露主表按实际两个月列示三范围，仍只汇总328条legal主账',()=>{
 const r=D.report(result,{},L);assert.deepEqual(r.months,['2026-09','2026-10']);assert.equal(r.count,328);assert.equal(r.rows.length,3);assert.equal(L.plain(r.total),'98569.602');assert.deepEqual(r.rows.map(row=>L.fixed(row.total,3)),['73432.760','20044.800','5092.042']);
});
test('披露筛选只列所选范围，不把被筛除范围误列成零',()=>{
 const r=D.report(result,{period:'2026-09',scope:'1'},L);assert.equal(r.count,120);assert.deepEqual(r.months,['2026-09']);assert.deepEqual(r.rows.map(row=>row.scope),['1']);assert.equal(L.plain(r.total),'37471.063');assert.equal(D.report(result,{search:'absent-record-id'},L).count,0);
});

test('未提供范围与实际零排放分别标记，OBD入账同样进入披露主表',()=>{
 const f={...result.main[0],method:'obd',amount:L.decimal('0'),emission_kg:'0'};
 const r=D.report({main:[f]}, {}, L);assert.equal(r.count,1);assert.equal(r.rows[0].provided,true);assert.equal(L.plain(r.rows[0].total),'0');assert.equal(r.rows[1].provided,false);assert.equal(r.rows[1].monthlyProvided[0],false);
});
test('当前参考目录按截图选定港交所、发改委、电力公告及范围三标准',()=>{
 assert.deepEqual(D.REFERENCES.slice(0,4).map(r=>r.id),['HKEX-ENV','NDRC-TRANSPORT','MEE-GRID-2023','GHGP-SCOPE3-2011']);assert.ok(D.REFERENCES.every(r=>!r.name.includes('GB/T')));assert.ok(D.REFERENCES[0].status.includes('2025-01-01'));assert.ok(D.ADOPTION.includes('2011'));assert.ok(D.sections.some(s=>s.paragraphs.some(p=>p.includes('2.614'))));
});
test('短附注保留项目参数与官方参考的差异，未知批次不套用模拟参数',()=>{
 const context={window:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../dist/sample.js'),'utf8'),context);const a=context.window.LEDGER_SAMPLE.appendix;
 const notes=D.footnotes(meta,a);assert.equal(notes.length,4);assert.ok(notes[0].text.includes('2.63为项目'));assert.ok(notes[1].text.includes('0.5306'));assert.ok(notes[1].text.includes('0.5568'));
 const unknown=D.footnotes({nature:'未注明'},A.create(result,{nature:'未注明'}));assert.ok(!unknown[0].text.includes('2.63为项目'));assert.ok(!unknown[1].text.includes('模拟结果保留原参数'));assert.ok(unknown[3].text.startsWith('未注明'));
});
test('Excel附注采用同一组短注及参考目录，既有手动清空保留',()=>{
 const a=A.create(result,meta);a.notes.scope1='';const b=A.create(result,meta,a);assert.equal(b.notes.scope1,'');assert.ok(b.notes.standard_choice.includes('港交所'));
 const sheets=A.sheets(result,{},meta,b,L);for(const n of D.footnotes(meta,b))assert.ok(sheets[0].rows.some(row=>row[0]==='表下注'+n.id+' '+n.title&&row[1]===n.text));assert.equal(sheets[1].rows.length-1,5);assert.equal(L.plain(L.summary(result.main).total),'98569.602');
});
test('单文件可嵌入的PDF资源与独立说明册字节一致，页面资源齐全',()=>{
 const base=path.join(__dirname,'../dist'),context={window:{}};vm.runInNewContext(fs.readFileSync(path.join(base,'manual-pdf.js'),'utf8'),context);const bytes=Buffer.from(context.window.LEDGER_MANUAL_PDF,'base64');assert.equal(bytes.subarray(0,4).toString(),'%PDF');assert.deepEqual(bytes,fs.readFileSync(path.join(base,'methodology.pdf')));
 const html=fs.readFileSync(path.join(base,'index.html'),'utf8'),scripts=[...html.matchAll(/<script src="([^"]+)"/g)].map(m=>m[1]);assert.equal(scripts.length,9);for(const file of scripts)assert.ok(fs.existsSync(path.join(base,file)));assert.ok(scripts.indexOf('disclosure.js')<scripts.indexOf('appendix.js'));for(const id of ['report-footnotes','ledger-footnotes','methodology-dialog','close-methodology','methodology-content'])assert.ok(html.includes('id="'+id+'"'));
});
