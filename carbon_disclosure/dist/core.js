/* Independent ledger domain logic. No activity-to-emission calculation. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CarbonLedger = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const FIELDS = ['fact_id','trip_id','plate','vehicle_id','period','scope','activity_type','emission_kg','method','factor_id','factor_version','computed_at','evidence_ref','备注'];
  const BUSINESS = FIELDS.filter(k => !['computed_at','备注'].includes(k));
  const BOOKING_RULE = '按同一趟次、月份、范围与活动匹配：有legal结果时优先采用；没有legal时采用OBD实测结果；VSP仅作旁证。同一来源存在多条候选时暂不汇总，避免重复；没有趟次的记录按fact_id独立接收。';
  const zero = () => ({ n: 0n, s: 0 });
  function decimal(value) {
    const text = String(value).trim();
    const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text);
    if (!match || text.length > 200) throw new Error('不是有效数值');
    const exponent = Number(match[4] || 0);
    if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 200) throw new Error('数值超出可处理范围');
    let s = (match[3] || '').length - exponent;
    let n = BigInt((match[2] + (match[3] || '')).replace(/^0+(?=\d)/, '')) * (match[1] === '-' ? -1n : 1n);
    if (s < 0) { n *= 10n ** BigInt(-s); s = 0; }
    while (s && n % 10n === 0n) { n /= 10n; s--; }
    return { n, s };
  }
  function add(a,b) {
    const s = Math.max(a.s,b.s);
    return { n:a.n * 10n ** BigInt(s-a.s) + b.n * 10n ** BigInt(s-b.s), s };
  }
  function plain(a) {
    const sign = a.n < 0n ? '-' : '';
    let text = (a.n < 0n ? -a.n : a.n).toString().padStart(a.s+1,'0');
    return sign + (a.s ? text.slice(0,-a.s)+'.'+text.slice(-a.s) : text);
  }
  function fixed(a,places=3,tonnes=false) {
    const s = a.s + (tonnes ? 3 : 0);
    const negative = a.n < 0n;
    let n = negative ? -a.n : a.n;
    if (s > places) { const d=10n ** BigInt(s-places); n=(n+d/2n)/d; }
    else n *= 10n ** BigInt(places-s);
    const text = n.toString().padStart(places+1,'0');
    return (negative && n !== 0n ? '-' : '') + (places ? text.slice(0,-places)+'.'+text.slice(-places) : text);
  }
  function sum(facts) { return facts.reduce((v,f)=>add(v,f.amount),zero()); }
  function parseCSV(source) {
    const text = String(source).replace(/^\uFEFF/,'');
    const records=[]; let row=[], field='', quoted=false, closed=false, line=1,start=1;
    const pushField=()=>{row.push(field);field='';closed=false;};
    const pushRow=()=>{pushField(); if(row.some(v=>v.trim()!=='')) records.push({cells:row,line:start}); row=[];start=line;};
    for(let i=0;i<text.length;i++) {
      const c=text[i];
      if(quoted) {
        if(c==='"') { if(text[i+1]==='"'){field+='"';i++;} else {quoted=false;closed=true;} }
        else {field+=c;if(c==='\n')line++;}
      } else if(c==='"') {
        if(field.length || closed) throw new Error(`第${line}行引号位置不正确`);
        quoted=true;
      } else if(c===',') pushField();
      else if(c==='\n' || c==='\r') {
        if(c==='\r' && text[i+1]==='\n')i++;
        line++;pushRow();start=line;
      } else {
        if(closed && !/\s/.test(c))throw new Error(`第${line}行引号后存在多余字符`);
        if(!closed)field+=c;
      }
    }
    if(quoted) throw new Error('CSV存在未闭合的引号');
    if(field.length || row.length || closed)pushRow();
    if(!records.length)throw new Error('文件为空，请选择包含表头的CSV');
    const header=records.shift().cells.map(v=>v.trim());
    if(new Set(header).size!==header.length)throw new Error('表头存在重复列名');
    const missing=FIELDS.filter(k=>!header.includes(k));
    if(missing.length)throw new Error('缺少必要列：'+missing.join('、'));
    return {header,rows:records.map(r=>({line:r.line,cells:r.cells,expected:header.length,raw:Object.fromEntries(header.map((h,i)=>[h,r.cells[i]??'']))})),extra:header.filter(k=>!FIELDS.includes(k))};
  }
  function fingerprint(raw) {
    return JSON.stringify(BUSINESS.map(k=>{
      const v=String(raw[k]??'').trim();
      if(k==='emission_kg') {try{return plain(decimal(v));}catch{return v;}}
      return v;
    }));
  }
  function validate(row) {
    const f=Object.fromEntries(FIELDS.map(k=>[k,String(row.raw[k]??'').trim()]));
    const reasons=[];let amount=null;
    if(row.cells.length!==row.expected)reasons.push('本行列数与表头不一致');
    if(!f.fact_id)reasons.push('缺少fact_id');
    if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(f.period))reasons.push('period须为有效的YYYY-MM');
    if(!['1','2','3'].includes(f.scope))reasons.push('scope不在1/2/3');
    if(!f.emission_kg)reasons.push('缺少排放量，不能补0');
    else {try{amount=decimal(f.emission_kg);if(amount.n<0n)reasons.push('负排放不符合本批接收规则');}catch{reasons.push('排放量不是有效数值');}}
    if(!f.factor_version)reasons.push('缺少factor_version');
    if(!f.factor_id)reasons.push('缺少factor_id');
    if(!['legal','obd','vsp'].includes(f.method))reasons.push('method不在已约定轨道内');
    if(!f.activity_type)reasons.push('缺少activity_type');
    return {...f,amount,line:row.line,raw:row.raw,reasons};
  }
  function process(source) {
    const parsed=parseCSV(source);
    const groups=new Map(),missing=[];
    for(const row of parsed.rows) {
      const id=String(row.raw.fact_id??'').trim();
      if(!id){missing.push(row);continue;}
      if(!groups.has(id))groups.set(id,[]);
      groups.get(id).push(row);
    }
    const main=[],side=[],rejected=[],duplicates=[],conflicts=[],valid=[];
    for(const row of missing) rejected.push({...validate(row),kind:'rejected'});
    for(const group of groups.values()) {
      if(new Set(group.map(r=>fingerprint(r.raw))).size>1) {
        for(const row of group)conflicts.push({...validate(row),kind:'conflict',reasons:['同一fact_id的核心内容冲突，整组暂不计入']});
        continue;
      }
      const fact=validate(group[0]);
      for(const row of group.slice(1))duplicates.push({...validate(row),kind:'duplicate',reasons:[`重复投递，保留第${group[0].line}行的业务事实`],originalLine:group[0].line});
      if(fact.reasons.length)rejected.push({...fact,kind:'rejected'});
      else valid.push(fact);
    }
    // The calculation source is preserved. Booking is a separate decision.
    // TTW and WTT remain separate because scope and activity are in the key.
    const events=new Map();
    for(const fact of valid){
      const key=fact.trip_id?JSON.stringify([fact.period,fact.trip_id,fact.scope,fact.activity_type]):JSON.stringify(['fact',fact.fact_id]);
      if(!events.has(key))events.set(key,[]);
      events.get(key).push(fact);
    }
    for(const group of events.values()){
      const preferred=group.some(f=>f.method==='legal')?'legal':group.some(f=>f.method==='obd')?'obd':null;
      const candidates=group.filter(f=>f.method===preferred);
      for(const fact of group){
        if(fact.method!==preferred){
          const booking_reason=fact.method==='vsp'?'VSP模型结果仅作旁证，不加入主账。':'同一趟次、月份、范围与活动已有legal主账，OBD结果保留为旁证，不重复相加。';
          side.push({...fact,kind:'side',booking_reason});
        }else if(candidates.length>1){
          conflicts.push({...fact,kind:'conflict',reasons:['同一趟次、月份、范围与活动存在多条'+preferred+'候选，无法确定唯一入账结果；请上游合并分项或明确趟次编号。']});
        }else{
          main.push({...fact,kind:'main',booking_reason:preferred==='legal'?'采用上游legal结果入账。':'本项没有legal结果，采用OBD实测结果入账，原计算来源保持obd。'});
        }
      }
    }
    for(const list of [main,side,conflicts])list.sort((a,b)=>a.line-b.line);
    return {main,side,rejected,duplicates,conflicts,rowCount:parsed.rows.length,uniqueCount:groups.size,extra:parsed.extra,header:parsed.header};
  }
  function filter(facts,filters={}) {
    const q=String(filters.search||'').trim().toLowerCase();
    return facts.filter(f=>(!filters.period || f.period===filters.period) && (!filters.scope || f.scope===filters.scope) && (!q || [f.plate,f.vehicle_id,f.trip_id,f.fact_id].some(v=>String(v).toLowerCase().includes(q))));
  }
  function summary(facts) {
    const scopes={'1':zero(),'2':zero(),'3':zero()};const months=new Map(),activities=new Map();
    for(const f of facts) {
      scopes[f.scope]=add(scopes[f.scope],f.amount);
      if(!months.has(f.period))months.set(f.period,{'1':zero(),'2':zero(),'3':zero(),total:zero(),count:0});
      const m=months.get(f.period);m[f.scope]=add(m[f.scope],f.amount);m.total=add(m.total,f.amount);m.count++;
      const a=activities.get(f.activity_type)||{amount:zero(),count:0};a.amount=add(a.amount,f.amount);a.count++;activities.set(f.activity_type,a);
    }
    return {total:sum(facts),scopes,months:[...months].sort((a,b)=>a[0].localeCompare(b[0])).map(([period,v])=>({period,...v})),activities:[...activities].map(([name,v])=>({name,...v})),count:facts.length};
  }
  function proportion(part,total) {
    if(total.n===0n)return 0;
    const scale=Math.max(part.s,total.s);
    const numerator=part.n*10n**BigInt(scale-part.s),denominator=total.n*10n**BigInt(scale-total.s);
    return Number(numerator*1000000n/denominator)/1000000;
  }
  function sourceSummary(result,filters={}) {
    const main=filter(result.main,filters),side=filter(result.side,filters);
    const sources=['obd','legal','vsp'].map(method=>{
      const booked=main.filter(f=>f.method===method),supporting=side.filter(f=>f.method===method);
      return {method,count:booked.length+supporting.length,mainCount:booked.length,sideCount:supporting.length,mainAmount:sum(booked)};
    });
    return {sources,count:main.length+side.length};
  }
  const identityText=value=>String(value||'').trim().toUpperCase();
  const plateText=value=>identityText(value).replace(/\s+/g,'');
  function vehicleSummary(facts,identityFacts=facts) {
    const plateIds=new Map(),groups=new Map();
    for(const f of identityFacts){
      const plate=plateText(f.plate),id=identityText(f.vehicle_id);
      if(plate&&id){if(!plateIds.has(plate))plateIds.set(plate,new Set());plateIds.get(plate).add(id);}
    }
    for(const f of facts){
      const plate=plateText(f.plate),id=identityText(f.vehicle_id),known=plateIds.get(plate);
      const resolved=id||(known?.size===1?[...known][0]:'');
      const key=resolved?'id:'+resolved:plate?'plate:'+plate:'unassigned:'+JSON.stringify([f.fact_id,f.line]);
      if(!groups.has(key))groups.set(key,{key,vehicleIds:new Set(),plates:new Set(),periods:new Set(),scopes:{'1':zero(),'2':zero(),'3':zero()},scopeCounts:{'1':0,'2':0,'3':0},methods:{obd:0,legal:0,vsp:0},amount:zero(),count:0,facts:[],hasVehicle:!!(plate||id),identityWarning:!!(plate&&known?.size>1)});
      const group=groups.get(key);
      group.identityWarning=group.identityWarning||!!(plate&&known?.size>1);
      if(resolved)group.vehicleIds.add(resolved);if(plate)group.plates.add(plate);group.periods.add(f.period);
      group.scopes[f.scope]=add(group.scopes[f.scope],f.amount);group.scopeCounts[f.scope]++;
      group.methods[f.method]++;group.amount=add(group.amount,f.amount);group.count++;group.facts.push(f);
    }
    return [...groups.values()].map(g=>({...g,plates:[...g.plates].sort(),vehicleIds:[...g.vehicleIds].sort(),periods:[...g.periods].sort()}))
      .sort((a,b)=>Number(b.hasVehicle)-Number(a.hasVehicle)||(a.plates[0]||a.vehicleIds[0]||a.key).localeCompare(b.plates[0]||b.vehicleIds[0]||b.key,'zh-CN',{numeric:true}));
  }
  const numeric=(value)=>({number:typeof value==='object'?plain(value):String(value)});
  function disclosureTables(result,filters={},meta={}) {
    const facts=filter(result.main,filters),groups=vehicleSummary(facts,result.main),source=sourceSummary(result,filters);
    const conditions=[filters.period||'全部月份',filters.scope?'范围'+filters.scope:'全部范围',filters.search||'无关键词'].join('；');
    return [
      {name:'12_车辆汇总',widths:[26,32,28,24,24,24,24,18,36],headerRow:3,rows:[['车辆排放汇总；范围和合计单位kg；仅有效主账'],['筛选条件',conditions],['车牌 / 对象','车辆编号','归属月份','范围一','范围二','范围三','排放合计','原始记录数','计算来源（条）'],...groups.map(g=>[g.plates.join('、')||(g.hasVehicle?'未提供车牌':'未关联车辆 · 单独列示'),g.vehicleIds.join('、'),g.periods.join('、'),...['1','2','3'].map(k=>g.scopeCounts[k]?numeric(g.scopes[k]):'未提供记录'),numeric(g.amount),g.count,Object.entries(g.methods).filter(([,n])=>n).map(([k,n])=>k.toUpperCase()+' '+n).join('；')])]},
      {name:'13_来源条数构成',widths:[24,22,24,22,22,36],headerRow:4,rows:[['计算来源构成：按有效接收记录条数统计'],['筛选条件',conditions],['统计说明','去重后的有效主账＋旁证；重复投递、被拒及冲突不计入。旁证不计入主账排放总量。'],['计算来源','有效记录数','条数占比（%）','主账条数','旁证条数','计入主账排放（kg）'],...source.sources.map(s=>[s.method.toUpperCase(),s.count,source.count?numeric(decimal(String(s.count/source.count*100))):'无有效记录',s.mainCount,s.sideCount,s.mainCount?numeric(s.mainAmount):'未提供主账记录'])]}
    ];
  }
  function exportTables(result,filters={},meta={}) {
    const main=filter(result.main,filters),side=filter(result.side,filters),s=summary(main);
    const unit=meta.unit==='co2'?'kgCO₂':meta.unit==='co2e'?'kgCO₂e':'kg（气体口径待确认）';
    const conditions=[filters.period||'全部月份',filters.scope?'范围'+filters.scope:'全部范围',filters.search||'无关键词'].join('；');
    const reported=(value,scope,period)=>main.some(f=>(!scope||f.scope===scope)&&(!period||f.period===period))?numeric(value):'未提供记录';
    const factRows=facts=>facts.map(f=>[...FIELDS.map(k=>k==='emission_kg'&&f.amount?numeric(f.amount):f[k]),f.booking_reason||'']);
    return [
      {name:'01_试算汇总',widths:[34,30,28],rows:[['碳账试算汇总','未经人工业务审核'],['来源文件',meta.filename||''],['数据性质',meta.nature||'未注明'],['筛选条件',conditions],['项目','数值','单位'],['当前主账记录数',main.length,'条'],['范围一',reported(s.scopes['1'],'1'),unit],['范围二',reported(s.scopes['2'],'2'),unit],['范围三（已提供类别）',reported(s.scopes['3'],'3'),unit],['合计',reported(s.total),unit],['当前旁证记录数',side.length,'条'],['本批原始行数',result.rowCount,'条'],['本批被拒记录',result.rejected.length,'条'],['本批重投记录',result.duplicates.length,'条'],['本批冲突记录',result.conflicts.length,'条'],['说明','汇总采用有效legal或OBD主账；同一排放的其他来源不重复相加。'],['说明','范围三仅表示本批已提供活动；计算依据附表后补。']],headerRow:5},
      {name:'02_月度范围',widths:[20,24,24,24,24,16],rows:[['月度试算；单位：'+unit],['筛选条件',conditions],['月份','范围一','范围二','范围三（已提供类别）','合计','主账条数'],...s.months.map(m=>[m.period,reported(m['1'],'1',m.period),reported(m['2'],'2',m.period),reported(m['3'],'3',m.period),numeric(m.total),m.count])],headerRow:3},
      {name:'03_主账明细',widths:[...FIELDS.map((_,i)=>[0,1,12,13].includes(i)?34:22),70],rows:[['有效主账明细；未经人工业务审核；单位：'+unit],['筛选条件',conditions],[...FIELDS,'入账说明'],...factRows(main)],headerRow:3},
      {name:'04_旁证明细',widths:[...FIELDS.map(()=>25),70],rows:[['旁证不计入主账总量；单位：'+unit],['筛选条件',conditions],[...FIELDS,'旁证说明'],...factRows(side)],headerRow:3},
      {name:'05_处理清单_全批',widths:[16,14,36,65,...FIELDS.slice(1).map(()=>24)],rows:[['全批处理清单，不随明细筛选缩减'],['处理类型','CSV起始行','fact_id','原因',...FIELDS.slice(1)],...[...result.rejected,...result.duplicates,...result.conflicts].sort((a,b)=>a.line-b.line).map(f=>[f.kind==='rejected'?'被拒':f.kind==='duplicate'?'重复投递':'内容冲突',f.line,f.fact_id,f.reasons.join('；'),...FIELDS.slice(1).map(k=>f.raw[k]??'')])],headerRow:2},
      {name:'06_口径说明',widths:[30,105],rows:[['口径说明','首期试算'],['处理范围','接收已计算排放，不乘因子生成原始排放。'],['主账与旁证',BOOKING_RULE],['期间与范围','使用上游period和scope；不以开票日期替代。'],['重复规则','相同fact_id且核心内容一致只计一次，计算时间/备注差异不视为新事实。'],['冲突规则','相同fact_id核心内容不同，或同一排放事项存在多条同源候选时，暂不计入并列明原因。'],['接收规则','负值、缺因子或版本、非法范围/期间/方法、缺活动等列入被拒清单。'],['未知与零','未知不补0；合法0值保留。'],['精度','汇总采用十进制精确加总，页面显示时舍入；Excel明细保存输入精度。'],['范围三','仅涵盖本批已提供类别，不表示价值链完整覆盖。'],['核验状态','仅完成系统规则检查，未完成人工业务审核或三方油量核验。'],['计算与证据','保留因子引用、证据索引；依据附表后补，编号不代表证据已核验。'],['单位',unit],['主账筛选条件',conditions],['异常范围','处理清单为全批，避免筛选隐藏原始导入异常。'],['当前版本','披露碳账 v0.9']],headerRow:1}
    ];
  }
  return {FIELDS,BOOKING_RULE,decimal,add,plain,fixed,zero,sum,parseCSV,process,filter,summary,proportion,sourceSummary,vehicleSummary,disclosureTables,exportTables};
});
