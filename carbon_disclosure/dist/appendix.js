/* Disclosure metadata only. Never computes or modifies emission facts. */
(function(root,factory){const api=factory(typeof module==='object'&&module.exports?require('./disclosure.js'):root.LedgerDisclosure);if(typeof module==='object'&&module.exports)module.exports=api;else root.LedgerAppendix=api;})(typeof globalThis!=='undefined'?globalThis:this,function(D){
  'use strict';
  const NOTE_FIELDS=[['entity','报告主体'],['boundary','组织与业务边界'],['standard_choice','本项目选定的参考文件及版本'],['data_source','数据来源与性质说明'],['scope1','范围一覆盖说明'],['scope2','范围二覆盖说明'],['scope3','范围三覆盖说明'],['limitations','缺失、估算与未覆盖事项']];
  const TRACE_FIELDS=[['inputs','上游实际输入及单位'],['formula','上游实际公式 / 模型说明'],['input_source','输入数据来源'],['rounding','分摊、精度与舍入说明'],['evidence_note','证据文件或记录说明'],['evidence_url','证据查询链接（可选）']];
  const FACTOR_FIELDS=[['label','方法或因子名称'],['value','因子数值（模型可留空）'],['unit','因子单位'],['boundary','适用边界与气体覆盖'],['reference_formula','参考方法说明（不是逐条计算快照）'],['source_note','项目资料或上游来源说明'],['authoritative_source','正式因子 / 模型依据及页码'],['source_url','正式来源链接（可选）']];
  const REFERENCES=D.REFERENCES;
  const PRESETS={
    'F-DIESEL-TTW':{label:'柴油燃烧 TTW',value:'2.63',unit:'kgCO₂e/L（测试说明原单位）',boundary:'项目测试说明：自有车辆柴油燃烧，范围一；与用户口述CO₂单位仍需统一。',reference_formula:'项目参考：柴油耗用量（L）×柴油TTW因子。实际输入与精度需上游逐条提供。'},
    'F-DIESEL-WTT':{label:'柴油上游 WTT',value:'0.53',unit:'kgCO₂e/L（测试说明原单位）',boundary:'项目测试说明：上游燃料，范围三；不并入范围一。',reference_formula:'项目参考：适用柴油活动量（L）×上游燃料因子。活动量归属由上游说明。'},
    'F-GRID-WTW':{label:'外购电力（项目编号）',value:'0.5568',unit:'kgCO₂e/kWh（测试说明原单位）',boundary:'项目将外购电力记为范围二。编号含WTW，准确因子边界与来源需计算侧确认。',reference_formula:'项目参考：外购电量（kWh）×电力因子。地区、年度及实际方法需上游说明。'},
    'F-VSP':{label:'VSP模型',value:'',unit:'',boundary:'项目旁证模型轨，不计入主账总量；模型适用边界待提供。',reference_formula:'实际模型公式、参数与输入待上游提供，不能统一替换为升数×2.63。'}
  };
  const own=(obj,key)=>obj&&Object.prototype.hasOwnProperty.call(obj,key)?obj[key]:undefined;
  const string=value=>typeof value==='string'?value:'';
  const key=f=>JSON.stringify([f.factor_id,f.factor_version]);
  function link(value){try{const u=new URL(String(value||''));return['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch{return'';}}
  function create(result,meta={},previous){
    const data={schema:1,notes:{},factors:Object.create(null),records:Object.create(null)};
    const defaults={entity:'',boundary:'',standard_choice:D.ADOPTION,data_source:'接收上游已经计算的排放事实，数据性质以当前批次标签为准。因子目录、计算记录与原始证据分别补充。',scope1:'使用输入scope=1的有效主账；实际组织边界与活动覆盖待补充。',scope2:'使用输入scope=2的有效主账；外购能源核算方法与适用因子待计算侧说明。',scope3:'仅覆盖本批已提供的上游或下游活动类别，不表示全部价值链排放已完成统计。',limitations:'本期为自动试算，未完成人工业务审核与三方油量核验。缺少资料的记录保持待补；附录填写不改变上游排放数值。'};
    for(const [field]of NOTE_FIELDS){const prior=own(previous?.notes,field);data.notes[field]=field==='standard_choice'&&!(typeof prior==='string'&&prior.trim())?D.ADOPTION:typeof prior==='string'?prior:defaults[field];}
    for(const f of [...result.main,...result.side]){
      const k=key(f),stored=own(previous?.factors,k);
      if(!own(data.factors,k)){
        const isTest=meta.nature==='模拟'&&((f.factor_version==='calc-factors-v1'&&f.factor_id!=='F-VSP')||(f.factor_id==='F-VSP'&&f.factor_version==='vsp-model-v1'));
        const preset=isTest?own(PRESETS,f.factor_id):null;
        const factor={factor_id:f.factor_id,factor_version:f.factor_version};
        for(const [field]of FACTOR_FIELDS)factor[field]=stored&&typeof stored[field]==='string'?stored[field]:preset?.[field]||'';
        if(!stored)factor.source_note=preset?'负责人测试集README第二节及排放事实表；仅为项目模拟指定口径，正式出处待补。':'已接收因子或模型编号、版本；数值、适用边界与出处待上游提供。';
        data.factors[k]=factor;
      }
      const record={};for(const [field]of TRACE_FIELDS)record[field]=string(own(own(previous?.records,f.fact_id),field));data.records[f.fact_id]=record;
    }
    return data;
  }
  function recordGaps(record={}){return[['inputs','实际输入及单位'],['formula','实际计算说明'],['input_source','输入来源'],['evidence_note','原始证据说明']].filter(([k])=>!string(record[k]).trim()).map(([,name])=>name);}
  function factorGaps(factor={}){const gaps=[];if(!factor.label?.trim())gaps.push('名称');if(!factor.boundary?.trim())gaps.push('适用边界');if(['F-DIESEL-TTW','F-DIESEL-WTT','F-GRID-WTW'].includes(factor.factor_id)){if(!factor.value?.trim())gaps.push('因子数值');if(!factor.unit?.trim())gaps.push('单位');}if(!factor.authoritative_source?.trim())gaps.push('正式出处');return gaps;}
  function completeness(result,data){
    const facts=[...result.main,...result.side];return{facts:facts.length,recorded:facts.filter(f=>!recordGaps(own(data.records,f.fact_id)).length).length,factors:Object.values(data.factors).length,factorRecorded:Object.values(data.factors).filter(f=>!factorGaps(f).length).length};
  }
  function sheets(result,filters,meta,data,L){
    const main=L.filter(result.main,filters),side=L.filter(result.side,filters),facts=[...main,...side];
    const factors=[...new Set(facts.map(key))].map(k=>data.factors[k]);
    const scopeNames={'1':'范围一','2':'范围二','3':'范围三（已提供类别）'};
    const factorCell=(factor,field)=>{const value=factor[field];if(field==='value'&&value?.trim()){try{return{number:L.plain(L.decimal(value))};}catch{return value;}}return value||'待补充';};
    const gaps=[];
    for(const [k,name]of NOTE_FIELDS)if(!data.notes[k]?.trim())gaps.push(['本期附注',name,'待填写','报告主体或数据提供方']);
    for(const f of factors){const g=factorGaps(f);if(g.length)gaps.push(['方法与因子',f.factor_id+' / '+f.factor_version,g.join('、'),'碳计算侧']);}
    for(const f of facts){const g=recordGaps(data.records[f.fact_id]);if(g.length)gaps.push(['逐条追溯',f.fact_id,g.join('、'),'碳计算侧 / 原始证据提供方']);}
    return[
      {name:'07_披露附注_草稿',widths:[32,115],headerRow:1,rows:[['披露附注','自动试算附录草稿；填写资料不代表已核验'],...NOTE_FIELDS.map(([k,name])=>[name,data.notes[k]||'待补充']),['报告期（当前筛选）',[...new Set(main.map(f=>f.period))].sort().join('、')||'无主账记录'],['记录来源',meta.filename||''],['排放单位',meta.unit==='co2e'?'kgCO₂e':meta.unit==='co2'?'kgCO₂':'kg，气体口径待确认'],['分类与期间','使用上游scope和period，核对规则后归集；不改用开票日期。'],['主账与旁证','同项legal优先，无legal时采用OBD实测；VSP及重复来源仅作旁证，不相加。'],['计算职责','上游计算原始排放；碳账保留结果并展示实际依据，不重新乘因子。'],['材料状态','资料已填写仍不等于已核验。计算与证据缺口见待补清单。'],['本项目参考文件',D.ADOPTION],...D.footnotes(meta,data).map(n=>['表下注'+n.id+' '+n.title,n.text]),['独立说明册',D.TITLE+'；系统中可阅读或下载PDF。参考组合版本：'+D.VERSION]]},
      {name:'08_标准依据目录',widths:[25,58,22,40,100,90,65],headerRow:1,rows:[['目录编号','标准名称','版本','相关章节','参考用途','官方来源','采用状态'],...REFERENCES.map(r=>[r.id,r.name,r.version,r.section,r.use,r.url,r.status])]},
      {name:'09_方法与因子',widths:[27,26,35,22,40,90,100,100,90,90,30],headerRow:1,rows:[['因子编号','版本',...FACTOR_FIELDS.map(([,name])=>name),'资料状态'],...factors.map(f=>[f.factor_id,f.factor_version,...FACTOR_FIELDS.map(([k])=>factorCell(f,k)),factorGaps(f).length?'有待补资料':'已登记，未经核验'])]},
      {name:'10_逐条追溯附表',widths:[36,14,16,17,30,25,30,26,38,...TRACE_FIELDS.map(()=>80),35],headerRow:1,rows:[['fact_id','记录角色','期间','范围','车辆/对象','上游排放量（kg）','因子编号','因子版本','原始证据索引',...TRACE_FIELDS.map(([,name])=>name),'资料状态'],...facts.map(f=>[f.fact_id,f.kind==='main'?'主账':'旁证',f.period,scopeNames[f.scope],f.plate||f.vehicle_id,{number:L.plain(f.amount)},f.factor_id,f.factor_version,f.evidence_ref,...TRACE_FIELDS.map(([k])=>data.records[f.fact_id]?.[k]||''),recordGaps(data.records[f.fact_id]).length?'待补：'+recordGaps(data.records[f.fact_id]).join('、'):'已登记，未经核验'])]},
      {name:'11_附录待补资料',widths:[24,52,100,44],headerRow:1,rows:[['类别','关联对象','待补内容','建议提供方'],...gaps,...(!gaps.length?[['材料状态','当前筛选','资料已登记；尚未进行业务真实性及原始证据核验。','业务审核方']]:[])]}
    ];
  }
  function restore(text,csv,result,meta){
    let parsed;try{parsed=JSON.parse(text);}catch{throw new Error('附录备份不是有效JSON文件');}
    if(parsed.schema!==1||parsed.source_csv!==csv||parsed.appendix?.schema!==1)throw new Error('备份与当前排放批次不匹配，请先载入相应CSV');
    return create(result,meta,parsed.appendix);
  }
  function backup(csv,appendix){return JSON.stringify({schema:1,source_csv:csv,appendix},null,2);}
  return{NOTE_FIELDS,TRACE_FIELDS,FACTOR_FIELDS,REFERENCES,key,link,create,recordGaps,factorGaps,completeness,sheets,restore,backup};
});
