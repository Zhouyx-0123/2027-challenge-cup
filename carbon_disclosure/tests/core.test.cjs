const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
const L=require('../dist/core.js'),X=require('../dist/xlsx.js');
const sample=fs.readFileSync(path.join(__dirname,'../dist/sample.csv'),'utf8');
const cell=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
function csv(rows){return L.FIELDS.map(cell).join(',')+'\r\n'+rows.map(r=>L.FIELDS.map(k=>cell(r[k])).join(',')).join('\r\n');}
const base={fact_id:'F1',trip_id:'T1',plate:'沪A12345',vehicle_id:'V1',period:'2026-09',scope:'1',activity_type:'柴油',emission_kg:'0.1',method:'legal',factor_id:'D',factor_version:'v1',computed_at:'2026-11-09T09:00:00+08:00',evidence_ref:'E1',备注:'测试'};

test('负责人模拟数据：342行，328主账、5旁证、3重投、6被拒，分项守恒',()=>{
 const r=L.process(sample);assert.equal(r.rowCount,342);assert.equal(r.uniqueCount,339);assert.equal(r.main.length,328);assert.equal(r.side.length,5);assert.equal(r.duplicates.length,3);assert.equal(r.rejected.length,6);assert.equal(r.conflicts.length,0);
 assert.equal(r.main.length+r.side.length+r.duplicates.length+r.rejected.length+r.conflicts.length,r.rowCount);
 const s=L.summary(r.main);assert.equal(L.fixed(s.total), '98569.602');assert.equal(L.fixed(s.scopes['1']),'73432.760');assert.equal(L.fixed(s.scopes['2']),'20044.800');assert.equal(L.fixed(s.scopes['3']),'5092.042');
 assert.deepEqual(s.months.map(m=>[m.period,L.fixed(m.total)]),[['2026-09','49852.732'],['2026-10','48716.870']]);
});
test('重复导入同样内容不会增加主账或排放，时间与备注变化仍为重投',()=>{
 const r=L.process(csv([base,{...base,computed_at:'2026-11-10T10:00:00+08:00',备注:'重复投递'}]));assert.equal(r.main.length,1);assert.equal(r.duplicates.length,1);assert.equal(L.plain(L.sum(r.main)),'0.1');
 const a=L.process(sample),b=L.process(sample);assert.equal(L.plain(L.sum(a.main)),L.plain(L.sum(b.main)));
});
test('同编号核心内容冲突时整组不入汇总，与输入先后顺序无关',()=>{
 for(const rows of [[base,{...base,emission_kg:'999'}],[{...base,emission_kg:'999'},base]]){const r=L.process(csv(rows));assert.equal(r.main.length,0);assert.equal(r.conflicts.length,2);}
});
test('同趟不同范围事实可共存，旁证永不叠加到主账',()=>{
 const r=L.process(csv([base,{...base,fact_id:'F2',scope:'3',factor_id:'W',activity_type:'上游燃料',emission_kg:'0.2'},{...base,fact_id:'F3',method:'obd',emission_kg:'10000'}]));assert.equal(r.main.length,2);assert.equal(r.side.length,1);assert.equal(L.plain(L.sum(r.main)),'0.3');
});
test('未知不补0，合法0保留；非法范围、月份、版本等被拒',()=>{
 const changes=[{emission_kg:''},{emission_kg:'-1'},{emission_kg:'NaN'},{factor_version:''},{factor_id:''},{scope:'0'},{period:'2026-13'},{method:'unknown'},{activity_type:''},{fact_id:''}];
 const r=L.process(csv([...changes.map((c,i)=>({...base,fact_id:'R'+i,...c})),{...base,fact_id:'ZERO',emission_kg:'0'}]));assert.equal(r.rejected.length,10);assert.equal(r.main.length,1);assert.equal(L.plain(L.sum(r.main)),'0');
});
test('非行程电力没有车牌或趟次仍可接收',()=>{
 const r=L.process(csv([{...base,trip_id:'',plate:'',vehicle_id:'SITE-01',scope:'2',activity_type:'电力'}]));assert.equal(r.main.length,1);
});
test('十进制精确加总与舍入，不累积二进制浮点误差',()=>{
 assert.equal(L.plain(L.add(L.decimal('0.1'),L.decimal('0.2'))),'0.3');assert.equal(L.fixed(L.decimal('999.9995')),'1000.000');assert.equal(L.fixed(L.decimal('98569.602'),3,true),'98.570');assert.equal(L.plain(L.decimal('1.25e-3')),'0.00125');
 const many=Array.from({length:1000},(_,i)=>({...base,fact_id:'F'+i,trip_id:'T'+i,emission_kg:'0.001'}));assert.equal(L.fixed(L.sum(L.process(csv(many)).main)),'1.000');
});

test('只有OBD的实测批次自动入账，TTW与WTT按范围分别计入，原值与来源不改写',()=>{
 const rows=[{...base,fact_id:'M-TTW',method:'obd',emission_kg:'12.345'}, {...base,fact_id:'M-WTT',method:'obd',scope:'3',activity_type:'上游燃料',factor_id:'W',emission_kg:'2.468'}];
 const r=L.process(csv(rows));assert.equal(r.main.length,2);assert.equal(r.side.length,0);assert.equal(L.plain(L.sum(r.main)),'14.813');
 assert.ok(r.main.every(f=>f.method==='obd'&&f.raw.method==='obd'&&f.booking_reason.includes('实测')));
 assert.deepEqual(r.main.map(f=>f.emission_kg),['12.345','2.468']);assert.deepEqual(r.main.map(f=>f.scope),['1','3']);
 const tables=L.exportTables(r,{},{});assert.equal(tables[0].rows.find(row=>row[0]==='合计')[1].number,'14.813');
 assert.equal(tables[0].rows.find(row=>row[0]==='范围二')[1],'未提供记录');assert.equal(tables[1].rows[3][2],'未提供记录');
 assert.equal(tables[2].rows[2].at(-1),'入账说明');assert.ok(tables[2].rows[3].at(-1).includes('实测'));assert.equal(tables[2].rows[3][8],'obd');
});

test('混合批次按事项选择来源：已有legal不叠加OBD，其他趟次OBD仍正常入账且顺序无关',()=>{
 const rows=[base,{...base,fact_id:'OBD-T1',method:'obd',emission_kg:'50'},{...base,fact_id:'OBD-T2',trip_id:'T2',method:'obd',emission_kg:'0.2'},{...base,fact_id:'VSP-T2',trip_id:'T2',method:'vsp',emission_kg:'60'}];
 for(const ordered of [rows,[...rows].reverse()]){const r=L.process(csv(ordered));assert.equal(r.main.length,2);assert.equal(r.side.length,2);assert.equal(L.plain(L.sum(r.main)),'0.3');assert.deepEqual(r.main.map(f=>f.fact_id).sort(),['F1','OBD-T2']);}
});

test('实测重投只计一次，同一事项不同编号的多条同源候选隔离，不静默双计',()=>{
 const f={...base,method:'obd'};const replay=L.process(csv([f,{...f,computed_at:'later'}]));assert.equal(replay.main.length,1);assert.equal(replay.duplicates.length,1);
 const r=L.process(csv([f,{...f,fact_id:'OTHER',emission_kg:'9'}]));assert.equal(r.main.length,0);assert.equal(r.conflicts.length,2);assert.ok(r.conflicts.every(f=>f.reasons[0].includes('多条obd候选')));
 const legal=L.process(csv([base,{...base,fact_id:'OTHER'}]));assert.equal(legal.main.length,0);assert.equal(legal.conflicts.length,2);
});

test('同一趟次跨月份或不同活动的实测事实独立保留，VSP单独上传仍仅作旁证',()=>{
 const f={...base,method:'obd'};const r=L.process(csv([f,{...f,fact_id:'NOV',period:'2026-11'},{...f,fact_id:'OTHER-ACTIVITY',activity_type:'其他燃料'}]));assert.equal(r.main.length,3);
 const v=L.process(csv([{...base,method:'vsp'}]));assert.equal(v.main.length,0);assert.equal(v.side.length,1);assert.match(v.side[0].booking_reason,/模型/);
});

test('无趟次的多笔用电仍按事实编号独立接收，不合并不同账单',()=>{
 const f={...base,trip_id:'',scope:'2',activity_type:'电力'};const r=L.process(csv([f,{...f,fact_id:'BILL-2'}]));assert.equal(r.main.length,2);assert.equal(r.conflicts.length,0);
});
test('UTF-8 BOM、引号内逗号/双引号/换行均保留，错误引号阻止导入',()=>{
 const note='路线,备注"A"\n第二行';const p=L.parseCSV('\ufeff'+csv([{...base,备注:note}]));assert.equal(p.rows[0].raw.备注,note);assert.equal(p.rows[0].line,2);
 assert.throws(()=>L.parseCSV(csv([base])+'\n"未闭合'),/引号/);assert.throws(()=>L.parseCSV('fact_id,scope\n1,1'),/缺少必要列/);
});
test('列数异常不能静默丢弃，重复表头拒绝',()=>{
 const broken=csv([base])+',意外列';assert.match(L.process(broken).rejected[0].reasons.join(),/列数/);
 assert.throws(()=>L.parseCSV(L.FIELDS.join(',')+',scope\n'),/重复列名/);
});
test('筛选后的Excel与页面使用相同事实集合；异常清单保留全批',()=>{
 const r=L.process(sample),f={period:'2026-09',scope:'1',search:'苏A12346'},facts=L.filter(r.main,f),summary=L.summary(facts),sheets=L.exportTables(r,f,{filename:'sample.csv',unit:'co2e',nature:'模拟'});
 assert.ok(facts.length>0);assert.equal(sheets[2].rows.length-3,facts.length);assert.equal(sheets[0].rows.find(row=>row[0]==='合计')[1].number,L.plain(summary.total));assert.equal(sheets[4].rows.length-2,9);
});
test('CSV文本不作为公式输出；XLSX为实际ZIP工作簿',()=>{
 const r=L.process(csv([{...base,备注:'=HYPERLINK("https://example.invalid")'}]));const sheets=L.exportTables(r,{},{}),bytes=X.create(sheets);
 assert.equal(Buffer.from(bytes).readUInt32LE(0),0x04034b50);const xml=Buffer.from(bytes).toString('utf8');assert.ok(xml.includes('inlineStr'));assert.ok(xml.includes('=HYPERLINK'));assert.ok(!xml.includes('<f>'));assert.ok(xml.includes('xl/worksheets/sheet6.xml'));
});
test('任意文本仅进入单元格，不破坏XML结构',()=>{
 const bytes=Buffer.from(X.create([{name:'测试',rows:[['表头'],['<script>&"\u0001']]}]));let offset=0,sheet='';
 while(bytes.readUInt32LE(offset)===0x04034b50){const size=bytes.readUInt32LE(offset+18),len=bytes.readUInt16LE(offset+26),extra=bytes.readUInt16LE(offset+28),name=bytes.subarray(offset+30,offset+30+len).toString(),start=offset+30+len+extra;if(name==='xl/worksheets/sheet1.xml')sheet=bytes.subarray(start,start+size).toString('utf8');offset=start+size;}
 assert.ok(sheet.includes('&lt;script&gt;&amp;&quot;'));assert.ok(!sheet.includes('\u0001'));
});
