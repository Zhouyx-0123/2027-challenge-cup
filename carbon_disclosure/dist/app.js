(function(){
  'use strict';
  // Trusted host branding is inherited when this workbench is embedded.
  if(document.body.dataset.presentation==='embedded'){
    try{
      const host=getComputedStyle(window.parent.document.documentElement);
      for(const name of ['canvas','canvas-2','surface','ink','ink-2','ink-3','blue','pink','mint','amber','ring','ring-2','glass','glass-2','font-body','font-display','font-mono']){
        const value=host.getPropertyValue('--ct-'+name).trim();
        if(value)document.documentElement.style.setProperty('--ct-'+name,value);
      }
    }catch{/* The shared default palette also supports isolated offline use. */}
  }
  const L=window.CarbonLedger,X=window.LedgerXlsx,A=window.LedgerAppendix,D=window.LedgerDisclosure;
  const $=id=>document.getElementById(id);
  const escape=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  const number=text=>String(text).replace(/\B(?=(\d{3})+(?!\d))/g,',');
  const amount=(d,tonnes=false)=>{const parts=L.fixed(d,3,tonnes).split('.');return number(parts[0])+'.'+parts[1];};
  const labels={fact_id:'事实编号',trip_id:'关联趟次',plate:'车牌',vehicle_id:'车辆 / 对象编号',period:'归属期间',scope:'范围',activity_type:'活动类型',emission_kg:'排放量（kg）',method:'计算来源',factor_id:'因子 / 模型编号',factor_version:'因子 / 模型版本',computed_at:'上游计算时间',evidence_ref:'证据索引',备注:'备注'};
  const titles={overview:['排放总览','查看排放总量、计算来源条数构成，以及范围一、二、三的排放占比。'],ledger:['车辆排放披露','每辆车汇总为一行，查看各范围排放；展开后可追溯全部原始记录。'],issues:['处理清单','记录没有被悄悄丢弃：查看被拒、重复投递及内容冲突的原因。'],appendix:['披露附录','说明本期边界、分类依据、方法因子与逐条计算来源。'],rules:['本期口径','当前系统执行哪些规则，以及这份试算尚未覆盖哪些事项。']};
  const state={result:null,csv:'',filename:'',nature:'未注明',unit:'pending',view:'overview',rail:'main',ledgerMode:'vehicles',vehicleGroups:[],vehicleKey:'',vehiclePage:1,issue:'rejected',page:1,filters:{period:'',scope:'',search:''},history:[],batchId:'',appendix:null};
  const storageKey='carbon-ledger-mainline-v1';let toastTimer;
  const appendixUI=window.createAppendixUI(state,save,toast,error);
  function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4200);}
  function error(message){$('error').textContent=message;$('error').hidden=!message;}
  function unit(tonnes=false){return(tonnes?'t':'kg')+(state.unit==='co2e'?'CO₂e':state.unit==='co2'?'CO₂':'');}
  function readHistory(){try{const h=JSON.parse(localStorage.getItem(storageKey)||'[]');return Array.isArray(h)?h.filter(x=>typeof x.csv==='string'&&typeof x.filename==='string').slice(0,5):[];}catch{return[];}}
  function save(){
    const batch={id:state.batchId,filename:state.filename,csv:state.csv,nature:state.nature,unit:state.unit,appendix:state.appendix,loadedAt:new Date().toISOString()};
    state.history=[batch,...state.history.filter(h=>h.csv!==state.csv)].slice(0,5);
    let stored=true;try{localStorage.setItem(storageKey,JSON.stringify(state.history));}catch{stored=false;toast('本机保存未成功；当前会话仍可使用，请备份附录并导出结果。');}
    renderHistory();return stored;
  }
  function renderHistory(){
    $('batch-history').innerHTML='<option value="">本机历史批次（最多5批）</option>'+state.history.map(h=>`<option value="${escape(h.id)}">${escape(h.filename)} · ${escape(h.nature||'未注明')}</option>`).join('');
  }
  function load(csv,meta={},persist=true){
    try{
      const result=L.process(csv);
      const existing=state.history.find(h=>h.csv===csv);
      Object.assign(state,{result,csv,filename:meta.filename||'排放事实.csv',nature:meta.nature||'未注明',unit:meta.unit||'pending',batchId:meta.id||existing?.id||'B-'+Date.now().toString(36).toUpperCase(),page:1,view:'overview',rail:'main',ledgerMode:'vehicles',vehicleKey:'',vehiclePage:1,filters:{period:'',scope:'',search:''}});
      const bundled=window.LEDGER_SAMPLE;
      const sampleAppendix=state.nature==='模拟'&&csv===bundled?.csv?bundled.appendix:null;
      state.appendix=A.create(result,state,meta.appendix||existing?.appendix||sampleAppendix);
      $('period-filter').innerHTML='<option value="">全部月份</option>'+[...new Set([...result.main,...result.side].map(f=>f.period))].sort().map(p=>`<option>${escape(p)}</option>`).join('');
      $('scope-filter').value='';$('period-filter').value='';$('search').value='';$('nature').value=state.nature;$('unit').value=state.unit;
      error('');if(persist)save();render();
      return true;
    }catch(e){error('导入未完成：'+e.message+'。请检查文件；原批次仍然保留。');return false;}
  }
  function sample(){if(window.LEDGER_SAMPLE){load(window.LEDGER_SAMPLE.csv,{filename:window.LEDGER_SAMPLE.filename,nature:'模拟',unit:'co2e'});toast('已载入负责人提供的模拟数据，未进行人工业务审核。');}}
  function go(view){state.view=titles[view]?view:'overview';state.page=1;render();window.scrollTo(0,0);}
  function render(){
    const [title,subtitle]=titles[state.view];$('page-title').textContent=title;$('breadcrumb-title').textContent=title;$('page-subtitle').textContent=subtitle;
    document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===state.view);b.setAttribute('aria-current',b.dataset.view===state.view?'page':'false');});
    const r=state.result,loaded=!!r;$('loaded-content').hidden=!loaded;$('empty-state').hidden=loaded;$('batch-options').hidden=!loaded;$('export-button').disabled=!loaded;
    if(!loaded)return;
    $('source-name').textContent=state.filename;
    $('source-description').textContent=`${state.nature}数据 · ${number(r.rowCount)}行输入 · ${number(r.uniqueCount)}个非空唯一事实编号`;
    $('batch-note').textContent=`批次 ${state.batchId} · 保存在当前浏览器 · `+(r.extra.length?'额外列已保留在原始批次中':'14字段结构已识别');
    $('import-result').hidden=false;
    const issueCount=r.rejected.length+r.duplicates.length+r.conflicts.length;
    $('import-result').innerHTML=`<strong>已自动生成汇总</strong><span>读入 ${number(r.rowCount)} 条 · 计入主账 ${number(r.main.length)} 条 · 旁证 ${number(r.side.length)} 条 · 需查看处理清单 ${number(issueCount)} 条</span><small>${r.main.length?'计算来源：'+escape([...new Set(r.main.map(f=>f.method.toUpperCase()))].join('、'))+'。原排放量直接接收，未重新计算。':'本批没有可计入主账的记录，请查看旁证记录和处理清单。'} 选择 CSV 后自动处理，也可点击“重新生成汇总”。</small>`;
    $('booking-rule').textContent=L.BOOKING_RULE;
    $('nav-count').textContent=r.main.length;$('nav-issues').textContent=r.rejected.length+r.duplicates.length+r.conflicts.length;
    for(const v of Object.keys(titles))$('view-'+v).hidden=v!==state.view;
    $('filtered-note').textContent=`当前主账 ${L.filter(r.main,state.filters).length} 条`;
    renderOverview();renderLedger();renderIssues();renderRules();appendixUI.render();
  }
  function renderOverview(){
    const r=state.result,facts=L.filter(r.main,state.filters),s=L.summary(facts);
    const metrics=[['主账排放合计',s.total,`${facts.length} 条有效主账 · 当前筛选`,true],['范围一',s.scopes['1'],'本批直接排放记录',false,'1'],['范围二',s.scopes['2'],'本批外购能源排放记录',false,'2'],['范围三',s.scopes['3'],'仅含本批已提供类别',false,'3']];
    $('metrics').innerHTML=metrics.map(([label,value,note,total,scope])=>{const provided=total?facts.length>0:facts.some(f=>f.scope===scope);return `<article class="metric ${total?'total':''}"><div class="metric-label">${scope?`<i class="scope-dot scope-${scope}"></i>`:''}${label}</div><div class="metric-value">${provided?amount(value,true):'—'}<span>${provided?unit(true):''}</span></div><div class="metric-note">${provided?note:total?'当前筛选没有有效主账':'当前筛选未提供该范围记录'}${state.unit==='pending'?' · 气体口径待确认':''}</div></article>`;}).join('');
    renderCharts(facts,s);renderDisclosure();
    const total=Number(L.plain(s.total));
    $('composition').innerHTML=s.activities.length?s.activities.sort((a,b)=>Number(L.plain(b.amount))-Number(L.plain(a.amount))).map(a=>{const share=total?Number(L.plain(a.amount))/total*100:0;return`<div class="composition-item"><div class="composition-label"><span>${escape(a.name)}</span><strong>${share.toFixed(1)}%</strong></div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(0,Math.min(100,share))}%"></div></div><div class="composition-label" style="margin-top:6px;color:#a0aa94;font-size:10px"><span>${a.count} 条记录</span><span>${amount(a.amount,true)} ${unit(true)}</span></div></div>`;}).join(''):'<p class="empty-table">暂无构成数据</p>';
    const quality=[['原始输入',r.rowCount],['有效主账',r.main.length],['旁证保留',r.side.length],['重复投递',r.duplicates.length],['被拒记录',r.rejected.length],['内容冲突',r.conflicts.length]];
    $('quality').innerHTML=quality.map(([label,n],i)=>`<div class="quality-item ${i>=4&&n?'warn':''}"><b>${number(n)}</b><span>${label}</span></div>`).join('');
  }
  function pieMarkup(series,label,empty){
    const positive=series.filter(s=>s.share>0),total=positive.reduce((n,s)=>n+s.share,0);
    let start=-Math.PI/2;
    const shapes=positive.map((s,index)=>{
      const title=escape(`${s.label}：${s.text} · ${(s.share*100).toFixed(1)}%`);
      if(positive.length===1)return`<circle class="pie-slice" tabindex="0" cx="110" cy="110" r="96" fill="${s.color}"><title>${title}</title></circle>`;
      const end=index===positive.length-1?Math.PI*1.5:start+s.share/total*Math.PI*2;
      const a=[110+96*Math.cos(start),110+96*Math.sin(start)],b=[110+96*Math.cos(end),110+96*Math.sin(end)],large=end-start>Math.PI?1:0;
      const shape=`<path class="pie-slice" tabindex="0" d="M110 110 L${a.join(' ')} A96 96 0 ${large} 1 ${b.join(' ')} Z" fill="${s.color}"><title>${title}</title></path>`;start=end;return shape;
    }).join('');
    const svg=`<svg class="disclosure-pie" viewBox="0 0 220 220" role="img" aria-label="${escape(label)}">${shapes||`<circle cx="110" cy="110" r="96" class="pie-empty"/><text x="110" y="115" text-anchor="middle" class="pie-empty-label">${escape(empty)}</text>`}</svg>`;
    return`<div class="pie-content">${svg}<ul class="pie-legend">${series.map(s=>`<li><i style="background:${s.color}"></i><div><b>${escape(s.label)}</b><span>${escape(s.text)}</span>${s.note?`<small>${escape(s.note)}</small>`:''}</div><strong>${s.provided===false?'—':(s.share*100).toFixed(1)+'%'}</strong></li>`).join('')}</ul></div>`;
  }
  function renderCharts(facts,summary){
    const sources=L.sourceSummary(state.result,state.filters),names={obd:'OBD 实测',legal:'legal 测算',vsp:'VSP 模型'},colors={obd:'var(--ct-blue)',legal:'var(--ct-mint)',vsp:'var(--ct-amber)'};
    const sourceSeries=sources.sources.map(s=>({label:names[s.method],color:colors[s.method],share:sources.count?s.count/sources.count:0,provided:!!sources.count,text:`${number(s.count)} 条`,note:`主账 ${s.mainCount} 条 · 旁证 ${s.sideCount} 条`}));
    $('source-chart').innerHTML=pieMarkup(sourceSeries,'OBD、legal和VSP有效接收记录条数占比',sources.count?'无占比':'暂无记录');
    $('source-chart-note').textContent=`当前筛选共 ${number(sources.count)} 条有效接收记录（主账＋旁证）。重复投递、被拒与冲突不计入；旁证排放不加入总排放量。`;
    const scopeColors={'1':'var(--ct-pink)','2':'var(--ct-amber)','3':'var(--ct-mint)'};
    const scopeSeries=['1','2','3'].map(scope=>{const provided=facts.some(f=>f.scope===scope);return{label:'范围'+({'1':'一','2':'二','3':'三'})[scope],color:scopeColors[scope],share:L.proportion(summary.scopes[scope],summary.total),provided,text:provided?amount(summary.scopes[scope])+' '+unit():'未提供记录',note:provided?facts.filter(f=>f.scope===scope).length+' 条主账':''};});
    $('scope-chart').innerHTML=pieMarkup(scopeSeries,'总排放量中范围一、二、三的排放占比',facts.length?'合计为零':'暂无记录');
    $('scope-chart-note').textContent=`与上方排放合计一致，仅统计有效主账。${facts.length?'当前合计 '+amount(summary.total)+' '+unit()+'。':'当前筛选没有有效主账。'}缺少的范围显示“未提供记录”。`;
  }
  const vehicleLabel=g=>g.plates.join('、')||g.vehicleIds.join('、')||'未关联车辆 · 单独列示';
  const scopeValue=(g,k)=>g.scopeCounts[k]?amount(g.scopes[k]):'未提供';
  function renderLedger(){
    const facts=L.filter(state.result[state.rail],state.filters),groups=L.vehicleSummary(facts,state.result[state.rail]);state.vehicleGroups=groups;
    const vehicles=state.ledgerMode==='vehicles',items=vehicles?groups:facts,pageSize=12,pages=Math.max(1,Math.ceil(items.length/pageSize));state.page=Math.min(state.page,pages);
    const start=(state.page-1)*pageSize,unassigned=groups.filter(g=>!g.hasVehicle).length;
    $('ledger-view-mode').value=state.ledgerMode;
    $('ledger-description').textContent=`当前筛选 ${groups.filter(g=>g.hasVehicle).length} 个车辆 / 对象${unassigned?' · '+unassigned+' 项未关联车辆':''} · ${facts.length} 条原始记录 · ${state.rail==='main'?'有效主账汇总，未经人工业务审核':'旁证单独汇总，不进入总排放量'} · ${unit()}`;
    document.querySelectorAll('[data-rail]').forEach(b=>b.classList.toggle('selected',b.dataset.rail===state.rail));
    $('ledger-head').innerHTML=vehicles?'<tr><th>车辆 / 车牌</th><th>归属月份</th><th class="number">范围一</th><th class="number">范围二</th><th class="number">范围三</th><th class="number">排放合计（kg）</th><th>记录数 / 来源</th><th></th></tr>':'<tr><th>归属月份 / 事实编号</th><th>车辆 / 趟次</th><th>范围与活动</th><th class="number">排放量（kg）</th><th>计算来源</th><th></th></tr>';
    $('ledger-body').innerHTML=(vehicles?groups.slice(start,start+pageSize).map(g=>`<tr data-vehicle-row="${escape(g.key)}"><td>${escape(vehicleLabel(g))}<small>${escape(g.plates.length?g.vehicleIds.join('、'):g.hasVehicle?'未提供车牌':g.facts[0].fact_id)}</small>${g.identityWarning?'<small class="identity-warning">同车牌关联多个车辆编号，已分开保留</small>':''}</td><td>${escape(g.periods.length>1?g.periods[0]+' 至 '+g.periods.at(-1):g.periods[0])}</td>${['1','2','3'].map(k=>`<td class="number">${scopeValue(g,k)}</td>`).join('')}<td class="number vehicle-total">${amount(g.amount)}</td><td>${g.count} 条<small>${escape(Object.entries(g.methods).filter(([,n])=>n).map(([m,n])=>m.toUpperCase()+' '+n).join(' · '))}</small></td><td><button class="detail-button" data-vehicle="${escape(g.key)}">展开明细 →</button></td></tr>`):facts.slice(start,start+pageSize).map(f=>`<tr><td>${escape(f.period)}<small>${escape(f.fact_id)}</small></td><td>${escape(f.plate||f.vehicle_id||'未提供')}<small>${escape(f.trip_id||'非行程 / 未提供趟次')}</small></td><td><span class="tag scope">范围${f.scope}</span><small>${escape(f.activity_type)}</small></td><td class="number">${amount(f.amount)}</td><td><span class="tag ${state.rail==='side'?'side':''}">${escape(f.method)}</span></td><td><button class="detail-button" data-detail="${f.line}">详情 →</button></td></tr>`)).join('')||`<tr><td colspan="${vehicles?8:6}" class="empty-table">当前条件下没有记录</td></tr>`;
    $('pagination').innerHTML=`<span>${items.length?`${start+1}–${Math.min(start+pageSize,items.length)}`:'0'} / ${items.length} ${vehicles?'个车辆 / 对象':'条记录'}</span><div><span>${state.page} / ${pages}</span><button data-page="-1" ${state.page<=1?'disabled':''}>上一页</button><button data-page="1" ${state.page>=pages?'disabled':''}>下一页</button></div>`;
  }
  function renderVehicleDetails(){
    const g=state.vehicleGroups.find(g=>g.key===state.vehicleKey);if(!g)return;
    $('vehicle-dialog-title').textContent=vehicleLabel(g);
    $('vehicle-dialog-description').textContent=`${g.periods.join('、')} · ${g.count} 条原始记录 · ${state.rail==='main'?'有效主账':'旁证（未计入总量）'}`;
    $('vehicle-dialog-totals').innerHTML=[['1','范围一'],['2','范围二'],['3','范围三'],['total','合计']].map(([k,label])=>`<div><span>${label}</span><b>${k==='total'?amount(g.amount):scopeValue(g,k)}<small>${k==='total'||g.scopeCounts[k]?' '+unit():''}</small></b></div>`).join('');
    const size=12,pages=Math.max(1,Math.ceil(g.facts.length/size));state.vehiclePage=Math.min(state.vehiclePage,pages);const start=(state.vehiclePage-1)*size;
    $('vehicle-record-body').innerHTML=g.facts.slice(start,start+size).map(f=>`<tr><td>${escape(f.period)}<small>${escape(f.fact_id)}</small></td><td>${escape(f.trip_id||'未提供趟次')}</td><td>范围${f.scope}<small>${escape(f.activity_type)}</small></td><td class="number">${amount(f.amount)}</td><td>${escape(f.method.toUpperCase())}</td><td><button class="detail-button" data-detail="${f.line}">原始详情 →</button></td></tr>`).join('');
    $('vehicle-pagination').innerHTML=`<span>${start+1}–${Math.min(start+size,g.count)} / ${g.count} 条</span><div><span>${state.vehiclePage} / ${pages}</span><button data-vehicle-page="-1" ${state.vehiclePage<=1?'disabled':''}>上一页</button><button data-vehicle-page="1" ${state.vehiclePage>=pages?'disabled':''}>下一页</button></div>`;
  }
  function renderIssues(){
    const list=state.result[state.issue];
    document.querySelectorAll('[data-issue]').forEach(b=>{b.classList.toggle('selected',b.dataset.issue===state.issue);b.textContent=({rejected:'被拒',duplicates:'重投',conflicts:'内容冲突'})[b.dataset.issue]+' '+state.result[b.dataset.issue].length;});
    $('issues-body').innerHTML=list.map(f=>`<tr><td>${f.line}</td><td>${escape(f.fact_id||'缺少编号')}<small>${escape(f.period)}</small></td><td>${escape(f.raw.emission_kg||'未提供')}</td><td>${escape(f.reasons.join('；'))}</td><td><button class="detail-button" data-detail="${f.line}">原始记录 →</button></td></tr>`).join('')||'<tr><td colspan="5" class="empty-table">本批没有此类记录</td></tr>';
  }
  function renderRules(){
    const rows=[['结果状态','首期自动生成试算，未经人工业务审核，不表示原始证据已核验。'],['排放接收','直接接收emission_kg，不用油量、里程或电量乘因子重新生成排放。'],['本批主账',L.BOOKING_RULE],['范围与期间','采用上游scope和period；检查合法性，不按发票日期重新归月。'],['重复与冲突','相同编号、相同核心内容只计一次；同编号内容不同则整组暂不计入；同一排放事项的同源多条候选也列入冲突。'],['被拒规则','负排放、缺因子/版本、非法范围/期间/轨道、缺活动等记录单列原因，不补0。'],['精度与单位','十进制精确加总；页面显示至三位小数，Excel保留输入数值精度。当前单位：'+unit()+'。'],['范围三覆盖','仅汇总本批已提供的活动类别，不表示已完成全部价值链排放统计。'],['披露说明','主表下短附注与独立说明册使用同一组港交所、发改委、电力公告及范围三标准参考文件；实际输入与原始证据缺项仍待补。'],['当前不包含','财务区间油耗、三方油量核验、空转判断、人工业务审核暂不在本期主线中。'],['导出一致性','主账、旁证和月度表使用页面当前筛选；处理清单保留全批并明确标注。'],['本机批次','最近5批保存在当前浏览器；换设备或清除浏览器数据后不保留，Excel可独立留存。']];
    $('rules-content').innerHTML=rows.map(([k,v])=>`<div class="rule-line"><strong>${k}</strong><span>${escape(v)}</span></div>`).join('');
  }

  function sourceLink(id){const r=D.REFERENCES.find(r=>r.id===id);return r&&A.link(r.url)?`<a href="${escape(r.url)}" target="_blank" rel="noopener noreferrer">${escape(({'HKEX-ENV':'港交所原文','NDRC-TRANSPORT':'发改委原文','MEE-GRID-2023':'电力公告原文','GHGP-SCOPE3-2011':'范围三标准原文'})[id]||'来源原文')}</a>`:'';}
  function renderDisclosure(){
    const r=D.report(state.result,state.filters,L),notes=D.footnotes(state,state.appendix);
    $('monthly-unit').textContent=`${state.appendix.notes.entity||'本批报告主体待填写'} · ${state.nature}数据 · ${r.count}条主账 · 单位 ${unit(true)}`;
    $('monthly-head').innerHTML='<tr><th>指标名称</th>'+r.months.map(p=>`<th class="number"><button class="report-month" data-month="${p}">${p.slice(0,4)}年${Number(p.slice(5))}月</button></th>`).join('')+'<th class="number">期间合计</th><th class="report-unit">单位</th></tr>';
    $('monthly-body').innerHTML=r.count?r.rows.map(row=>`<tr><td>${escape(row.label)}<sup><a href="#report-note-${row.scope}" aria-label="查看范围${row.scope}附注">${row.scope}</a></sup></td>${row.values.map((d,i)=>`<td class="number">${row.monthlyProvided[i]?amount(d,true):'未提供'}</td>`).join('')}<td class="number">${row.provided?amount(row.total,true):'未提供'}</td><td class="report-unit">${unit(true)}</td></tr>`).join(''):`<tr><td colspan="${r.months.length+3}" class="empty-table">当前筛选没有有效主账记录</td></tr>`;
    $('monthly-foot').innerHTML=r.count?`<tr><td>温室气体排放总量<sup><a href="#report-note-4" aria-label="查看汇总附注">4</a></sup></td>${r.monthlyTotals.map(d=>`<td class="number">${amount(d,true)}</td>`).join('')}<td class="number">${amount(r.total,true)}</td><td class="report-unit">${unit(true)}</td></tr>`:'';
    for(const [id,prefix]of [['report-footnotes','report'],['ledger-footnotes','ledger']])$(''+id).innerHTML=notes.map(n=>`<p id="${prefix}-note-${n.id}"><sup>${n.id}</sup><span><b>${escape(n.title)}</b> ${escape(n.text)} <span class="footnote-links">${n.sources.map(sourceLink).join(' · ')}</span></span></p>`).join('')+'<div class="manual-inline-link"><button class="text-button" data-manual="read">查看独立核算与披露说明册 →</button></div>';
  }
  function openMethodology(){
    const table=spec=>'<div class="table-scroll"><table><thead><tr>'+spec.headers.map(h=>'<th>'+escape(h)+'</th>').join('')+'</tr></thead><tbody>'+spec.rows.map(row=>'<tr>'+row.map(c=>'<td>'+escape(c)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
    $('methodology-content').innerHTML='<p class="manual-lead">主表下提供短附注，本册提供分类条件、方法参数、资料来源与版本说明。统计数字仍取上游结果。</p>'+D.sections.map((section,i)=>`<section class="manual-section"><div class="manual-section-number">${String(i+1).padStart(2,'0')}</div><h3>${escape(section.title)}</h3>${section.paragraphs.map(t=>'<p>'+escape(t)+'</p>').join('')}${section.table?table(section.table):''}${section.references?D.REFERENCES.map(ref=>`<article class="manual-reference"><b>${escape(ref.name)}</b><p>${escape(ref.version)} · ${escape(ref.section)}</p><p>${escape(ref.use)}</p><small>${escape(ref.status)}</small> ${sourceLink(ref.id)}</article>`).join(''):''}</section>`).join('');
    $('methodology-dialog').showModal();$('methodology-content').scrollTop=0;
  }
  function downloadMethodology(){
    if(!window.LEDGER_MANUAL_PDF){error('说明册文件尚未载入，请重新打开完整版本。');return;}
    const bytes=Uint8Array.from(atob(window.LEDGER_MANUAL_PDF),c=>c.charCodeAt(0));window.LedgerDownloads.save(bytes,'application/pdf','碳账核算与披露说明册_20261112_v0.6.pdf');toast('独立说明册PDF已生成，与主表附注使用同一组参考文件。');
  }
  $('close-methodology').addEventListener('click',()=>$('methodology-dialog').close());
  document.addEventListener('click',e=>{const b=e.target.closest('[data-manual]');if(!b)return;if(b.dataset.manual==='read')openMethodology();else downloadMethodology();});

  function detail(line){
    const f=['main','side','rejected','duplicates','conflicts'].flatMap(k=>state.result[k]).find(x=>x.line===line);if(!f)return;
    const status=f.kind==='main'?'有效主账 · '+f.booking_reason+' 系统规则通过，未经人工业务审核':f.kind==='side'?'旁证记录 · '+f.booking_reason:f.reasons.join('；');
    $('detail-content').innerHTML=`<div class="detail-status">${escape(status)} · CSV第${f.line}行</div><dl class="detail-grid">${L.FIELDS.map(k=>`<div class="detail-row"><dt>${labels[k]}<small style="display:block;font-size:9px">${escape(k)}</small></dt><dd>${escape(f.raw[k]||'未提供')}</dd></div>`).join('')}</dl>${appendixUI.traceDetails(f)}`;
    $('detail-dialog').showModal();
  }
  async function readFile(file){
    if(!file)return;if(!/\.csv$/i.test(file.name)){error('请选择CSV文件。');return;}
    $('upload-button').disabled=true;$('dropzone').setAttribute('aria-busy','true');
    try{const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());if(load(text,{filename:file.name,nature:'未注明',unit:'pending'}))toast(`已自动处理：主账 ${state.result.main.length} 条，旁证 ${state.result.side.length} 条。`);}
    catch(e){error('读取未完成：请将文件保存为UTF-8编码的CSV后重试。');}
    finally{$('upload-button').disabled=false;$('dropzone').setAttribute('aria-busy','false');}
    $('file-input').value='';
  }
  function download(){
    if(!state.result)return;
    try{
      const sheets=[...L.exportTables(state.result,state.filters,state),...A.sheets(state.result,state.filters,state,state.appendix,L),...L.disclosureTables(state.result,state.filters,state)],bytes=X.create(sheets);
      window.LedgerDownloads.save(bytes,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','碳账试算与附录_'+(state.filters.period||'全期间')+'_'+new Date().toISOString().slice(0,10)+'.xlsx');toast('Excel已生成，包含主线报表、披露附注、标准目录、方法因子与追溯附表。');
    }catch(e){error('导出未完成：'+e.message);}
  }
  $('upload-button').addEventListener('click',()=>$('file-input').click());$('file-input').addEventListener('change',e=>readFile(e.target.files[0]));
  document.querySelector('.brand').addEventListener('click',e=>{e.preventDefault();go('overview');});
  for(const id of ['sample-button','empty-sample'])$(id).addEventListener('click',sample);
  $('export-button').addEventListener('click',download);$('see-issues').addEventListener('click',()=>go('issues'));
  $('reprocess-button').addEventListener('click',()=>{if(state.result&&load(state.csv,{filename:state.filename,nature:state.nature,unit:state.unit,id:state.batchId,appendix:state.appendix}))toast('已重新生成汇总；未累计重复入账。');});
  $('close-detail').addEventListener('click',()=>$('detail-dialog').close());$('detail-dialog').addEventListener('click',e=>{if(e.target===$('detail-dialog'))$('detail-dialog').close();});
  $('close-vehicle').addEventListener('click',()=>$('vehicle-dialog').close());$('vehicle-dialog').addEventListener('click',e=>{if(e.target===$('vehicle-dialog'))$('vehicle-dialog').close();});
  $('ledger-view-mode').addEventListener('change',e=>{state.ledgerMode=e.target.value;state.page=1;renderLedger();});
  document.addEventListener('click',e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.dataset.view)go(b.dataset.view);
    if(b.dataset.rail){state.rail=b.dataset.rail;state.page=1;renderLedger();}
    if(b.dataset.issue){state.issue=b.dataset.issue;renderIssues();}
    if(b.dataset.detail)detail(Number(b.dataset.detail));
    if(b.dataset.vehicle){state.vehicleKey=b.dataset.vehicle;state.vehiclePage=1;renderVehicleDetails();$('vehicle-dialog').showModal();}
    if(b.dataset.vehiclePage){state.vehiclePage+=Number(b.dataset.vehiclePage);renderVehicleDetails();}
    if(b.dataset.page){state.page+=Number(b.dataset.page);renderLedger();}
    if(b.dataset.month){state.filters.period=b.dataset.month;$('period-filter').value=b.dataset.month;state.page=1;render();}
  });
  const updateFilters=()=>{state.filters={period:$('period-filter').value,scope:$('scope-filter').value,search:$('search').value};state.page=1;render();};
  $('period-filter').addEventListener('change',updateFilters);$('scope-filter').addEventListener('change',updateFilters);$('search').addEventListener('input',updateFilters);
  $('reset-filters').addEventListener('click',()=>{$('period-filter').value='';$('scope-filter').value='';$('search').value='';updateFilters();});
  $('nature').addEventListener('change',()=>{state.nature=$('nature').value;save();render();});$('unit').addEventListener('change',()=>{state.unit=$('unit').value;save();render();});
  $('batch-history').addEventListener('change',e=>{const h=state.history.find(h=>h.id===e.target.value);if(h)load(h.csv,h,false);e.target.value='';});
  const drop=$('dropzone');for(const name of ['dragenter','dragover'])drop.addEventListener(name,e=>{e.preventDefault();drop.classList.add('dragging');});for(const name of ['dragleave','drop'])drop.addEventListener(name,e=>{e.preventDefault();drop.classList.remove('dragging');});drop.addEventListener('drop',e=>readFile(e.dataTransfer.files[0]));
  window.addEventListener('dragover',e=>e.preventDefault());window.addEventListener('drop',e=>e.preventDefault());
  state.history=readHistory();renderHistory();if(state.history.length)load(state.history[0].csv,state.history[0],false);else if(window.LEDGER_SAMPLE)sample();else render();
})();
