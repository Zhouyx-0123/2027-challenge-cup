const test=require('node:test'),assert=require('node:assert/strict');
const L=require('../dist/core.js'),A=require('../dist/appendix.js'),X=require('../dist/xlsx.js');
const base={fact_id:'F1',trip_id:'T1',plate:'苏A12346',vehicle_id:'V1',period:'2026-09',scope:'1',activity_type:'柴油',emission_kg:'0.1',method:'legal',factor_id:'D',factor_version:'v1',computed_at:'2026-11-09T09:00:00+08:00',evidence_ref:'E1',备注:''};
const csv=rows=>[L.FIELDS,...rows.map(r=>L.FIELDS.map(k=>r[k]??''))].map(r=>r.map(v=>'"'+String(v).replace(/"/g,'""')+'"').join(',')).join('\n');
const result=rows=>L.process(csv(rows.map((r,i)=>({...base,fact_id:'F'+i,trip_id:'T'+i,...r}))));

test('一辆车跨月份和范围合为一行，保留十进制精度及全部原始事实',()=>{
 const r=result([{emission_kg:'0.1'},{period:'2026-10',emission_kg:'0.2'},{period:'2026-10',scope:'3',activity_type:'上游燃料',emission_kg:'0.04'}]);
 const g=L.vehicleSummary(r.main);assert.equal(g.length,1);assert.equal(g[0].count,3);assert.equal(L.plain(g[0].amount),'0.34');assert.equal(L.plain(g[0].scopes['1']),'0.3');assert.equal(L.plain(g[0].scopes['3']),'0.04');assert.equal(g[0].scopeCounts['2'],0);assert.deepEqual(g[0].periods,['2026-09','2026-10']);assert.deepEqual(g[0].facts.map(f=>f.fact_id),r.main.map(f=>f.fact_id));
});
test('没有车牌的同一车辆按车辆编号合并，两个编号保持分开',()=>{
 const r=result([{plate:'',vehicle_id:'VIN-A'},{plate:'',vehicle_id:'VIN-A',emission_kg:'0.2'},{plate:'',vehicle_id:'VIN-B'}]),g=L.vehicleSummary(r.main);
 assert.equal(g.length,2);assert.equal(L.plain(g.find(x=>x.vehicleIds[0]==='VIN-A').amount),'0.3');assert.ok(g.every(x=>x.hasVehicle));
});
test('只有车牌的记录可与唯一对应车辆编号关联，车牌空格和大小写可归一',()=>{
 const r=result([{plate:' 苏a12346 ',vehicle_id:'V1'},{plate:'苏A12346',vehicle_id:'',period:'2026-10',emission_kg:'0.2'}]);
 const g=L.vehicleSummary(r.main);assert.equal(g.length,1);assert.equal(g[0].count,2);assert.deepEqual(g[0].vehicleIds,['V1']);
 const filtered=L.vehicleSummary(L.filter(r.main,{period:'2026-10'}),r.main);assert.equal(filtered[0].key,'id:V1');
});
test('同车牌关联不同车辆编号时不会误合并，只有车牌的记录独立保留',()=>{
 const r=result([{vehicle_id:'V1'},{vehicle_id:'V2'},{vehicle_id:''}]),g=L.vehicleSummary(r.main);
 assert.equal(g.length,3);assert.ok(g.every(x=>x.identityWarning));assert.equal(L.plain(g.reduce((s,x)=>L.add(s,x.amount),L.zero())),L.plain(L.sum(r.main)));
});
test('无任何车辆标识的记录逐项保留，合法零与范围缺失区分',()=>{
 const r=result([{vehicle_id:'',plate:'',trip_id:'',scope:'2',activity_type:'外购电力',emission_kg:'0'},{vehicle_id:'',plate:'',trip_id:'',scope:'2',activity_type:'外购电力',emission_kg:'2'}]);
 const g=L.vehicleSummary(r.main);assert.equal(g.length,2);assert.ok(g.every(x=>!x.hasVehicle));assert.equal(g[0].scopeCounts['2'],1);assert.equal(L.plain(g[0].scopes['2']),'0');
 const rows=L.disclosureTables(r)[0].rows.slice(3);assert.equal(rows[0][3],'未提供记录');assert.equal(rows[0][4].number,'0');
});
test('来源饼图计数包括有效主账与旁证，排除重投和被拒且不重复累计碳',()=>{
 const legal={...base,emission_kg:'2'},obd={...base,fact_id:'O',method:'obd',emission_kg:'3'},vsp={...base,fact_id:'V',method:'vsp',emission_kg:'5'},other={...base,fact_id:'O2',trip_id:'T2',method:'obd',emission_kg:'0.3'};
 const r=L.process(csv([legal,obd,vsp,other,{...other,备注:'重投'},{...base,fact_id:'BAD',emission_kg:'-3'}])),s=L.sourceSummary(r);
 assert.equal(s.count,4);assert.deepEqual(s.sources.map(x=>[x.method,x.count,x.mainCount,x.sideCount]),[['obd',2,1,1],['legal',1,1,0],['vsp',1,0,1]]);assert.equal(L.plain(L.sum(r.main)),'2.3');assert.equal(L.plain(s.sources[0].mainAmount),'0.3');
});
test('车辆表和来源构成使用相同筛选，车辆汇总可对回主账总量',()=>{
 const r=result([{vehicle_id:'A'},{vehicle_id:'B',plate:'沪B99999',period:'2026-10'},{vehicle_id:'A',period:'2026-10',scope:'3',activity_type:'上游燃料'}]);
 const filter={period:'2026-10',search:'苏A12346'},facts=L.filter(r.main,filter),g=L.vehicleSummary(facts,r.main),s=L.sourceSummary(r,filter);
 assert.equal(g.length,1);assert.equal(g[0].count,1);assert.equal(g[0].scopeCounts['3'],1);assert.equal(s.count,1);assert.equal(L.plain(L.sum(facts)),L.plain(g[0].amount));
});
test('大额和微小数值的占比不会溢出，零合计不生成虚假占比',()=>{
 assert.equal(L.proportion(L.decimal('1e200'),L.decimal('2e200')),0.5);assert.equal(L.proportion(L.decimal('1e-200'),L.decimal('2e-200')),0.5);assert.equal(L.proportion(L.zero(),L.zero()),0);
});
test('完整导出保留原11张表并加入车辆及来源两表，车辆总量与明细一致',()=>{
 const r=result([{emission_kg:'0.1'},{emission_kg:'0.2'}]),meta={unit:'co2',nature:'真实'},a=A.create(r,meta),sheets=[...L.exportTables(r,{},meta),...A.sheets(r,{},meta,a,L),...L.disclosureTables(r,{},meta)];
 assert.equal(sheets.length,13);assert.equal(sheets[11].name,'12_车辆汇总');assert.equal(sheets[11].rows[3][6].number,'0.3');assert.equal(sheets[11].rows[3][7],2);assert.equal(sheets[12].rows[5][1],2);assert.ok(Buffer.from(X.create(sheets)).includes(Buffer.from('sheet13.xml')));
});
