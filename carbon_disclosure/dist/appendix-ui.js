(function(){
  'use strict';
  window.createAppendixUI=function(state,save,toast,error){
    const A=window.LedgerAppendix,L=window.CarbonLedger,$=id=>document.getElementById(id);
    const escape=s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    const external=(url,text)=>A.link(url)?`<a href="${escape(A.link(url))}" target="_blank" rel="noopener noreferrer">${escape(text||'查看来源')} ↗</a>`:'';
    let tab='notes',page=1,editing=null;
    const labelInput=(key,name,value,prefix='')=>`<label>${escape(name)}<textarea name="${prefix+key}" rows="${['inputs','formula','boundary','limitations'].includes(key)?3:2}" placeholder="没有资料时可留空，之后补充">${escape(value)}</textarea></label>`;
    function render(){
      if(!state.result||!state.appendix)return;
      const data=state.appendix,c=A.completeness(state.result,data);
      $('appendix-status').innerHTML=`<span>本批接收记录 <b>${c.facts}</b> 条</span><span>四项基本追溯资料已登记 <b>${c.recorded}</b> 条</span><span>方法/因子 <b>${c.factors}</b> 项</span><span class="appendix-pending">已登记不代表已核验</span>`;
      for(const t of ['notes','references','factors','trace'])$('appendix-'+t).hidden=t!==tab;
      document.querySelectorAll('[data-appendix-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.appendixTab===tab));
      $('appendix-note-fields').innerHTML=A.NOTE_FIELDS.map(([k,n])=>labelInput(k,n,data.notes[k],'note-')).join('');
      $('appendix-reference-list').innerHTML=A.REFERENCES.map(r=>`<article class="reference-item"><div><span class="eyebrow">${escape(r.version)}</span><h3>${escape(r.name)}</h3><div class="reference-section">${escape(r.section)}</div><p>${escape(r.use)}</p><small>${escape(r.status)}</small></div><div>${external(r.url,'官方原文')}</div></article>`).join('');
      $('appendix-factor-list').innerHTML=Object.values(data.factors).map((f,i)=>`<article class="factor-card"><div class="factor-card-head"><span class="tag">${escape(f.factor_version)}</span><button class="text-button" data-factor-edit="${i}">补充 / 修改 →</button></div><h3>${escape(f.label||f.factor_id)}</h3><p class="factor-id">${escape(f.factor_id)}</p><div class="factor-value">${escape(f.value||'数值待提供')}<small>${escape(f.unit||'模型或单位待说明')}</small></div><p>${escape(f.source_note||'来源说明待补')}</p><div class="factor-authority"><b>正式出处</b><span>${escape(f.authoritative_source||'待计算侧提供')}</span>${external(f.source_url,'查看原文')}</div><small class="appendix-pending">${A.factorGaps(f).length?'仍有待补资料':'资料已登记，未经核验'}</small></article>`).join('')||'<p class="empty-table">本批没有可关联的方法与因子</p>';
      renderTrace();
    }
    function renderTrace(){
      const facts=L.filter([...state.result.main,...state.result.side],state.filters),size=12,pages=Math.max(1,Math.ceil(facts.length/size));page=Math.min(page,pages);const start=(page-1)*size;
      $('appendix-trace-body').innerHTML=facts.slice(start,start+size).map(f=>{const gaps=A.recordGaps(state.appendix.records[f.fact_id]);return`<tr><td>${escape(f.fact_id)}<small>${f.period} · ${f.kind==='main'?'主账':'旁证'}</small></td><td>${escape(f.activity_type)}<small>${escape(f.emission_kg)} kg · 范围${f.scope}</small></td><td>${escape(f.evidence_ref||'未提供')}</td><td><span class="tag ${gaps.length?'side':''}">${gaps.length?'待补资料':'已登记 · 未核验'}</span><small>${escape(gaps.join('、'))}</small></td><td><button class="detail-button" data-trace-edit="${f.line}">补充说明 →</button></td></tr>`;}).join('')||'<tr><td colspan="5" class="empty-table">当前筛选下没有接收记录</td></tr>';
      $('appendix-pagination').innerHTML=`<span>${facts.length?`${start+1}–${Math.min(start+size,facts.length)}`:'0'} / ${facts.length} 条</span><div><span>${page} / ${pages}</span><button data-appendix-page="-1" ${page<=1?'disabled':''}>上一页</button><button data-appendix-page="1" ${page>=pages?'disabled':''}>下一页</button></div>`;
    }
    function edit(type,target){
      if(!state.result)return;
      let fields,data,heading;
      if(type==='factor'){
        data=Object.values(state.appendix.factors)[target];if(!data)return;
        editing={type,key:A.key(data)};fields=A.FACTOR_FIELDS;heading=data.factor_id+' / '+data.factor_version;
        $('appendix-dialog-title').textContent='方法与因子资料';
      }else{
        const fact=[...state.result.main,...state.result.side].find(f=>f.line===target);if(!fact)return;
        editing={type:'trace',id:fact.fact_id};data=state.appendix.records[fact.fact_id];fields=A.TRACE_FIELDS;heading=fact.fact_id+' · 上游排放 '+fact.emission_kg+' kg（只读）';
        $('appendix-dialog-title').textContent='补充逐条计算与证据';
      }
      $('appendix-dialog-fields').innerHTML=`<div class="detail-status">${escape(heading)}</div><p class="appendix-editor-help">填写上游实际提供的内容；缺少材料可以留空。保存仅登记资料，不修改排放量，也不代表完成审核。链接可不填，证据说明可写文件名称或记录编号。</p><div class="appendix-form-grid modal-fields">${fields.map(([k,n])=>labelInput(k,n,data[k])).join('')}</div>`;
      $('appendix-dialog-error').textContent='';$('appendix-dialog').showModal();
    }
    function traceDetails(f){
      if(!['main','side'].includes(f.kind))return'<p class="detail-appendix">此记录尚未进入有效接收集合，先处理原始数据异常。</p>';
      const record=state.appendix.records[f.fact_id]||{},factor=state.appendix.factors[A.key(f)],gaps=A.recordGaps(record);
      return`<div class="detail-appendix"><div class="appendix-detail-head"><button class="text-button" data-trace-edit="${f.line}">补充 / 修改</button></div><p>${gaps.length?'待补：'+escape(gaps.join('、')):'资料已登记，未经业务核验。'}</p>${A.TRACE_FIELDS.filter(([k])=>k!=='evidence_url').map(([k,n])=>`<p><b>${escape(n)}：</b>${escape(record[k]||'待补充')}</p>`).join('')}${external(record.evidence_url,'查看已登记证据链接')}<hr><p><b>方法/因子：</b>${escape(factor?.label||f.factor_id)} · ${escape(factor?.value||'数值待补')} ${escape(factor?.unit||'')}</p><p><b>参考说明：</b>${escape(factor?.reference_formula||'待补充')}（不是逐条实际计算快照）</p><p><b>正式出处：</b>${escape(factor?.authoritative_source||'待提供')}</p><p>本页登记信息不触发排放重算。</p></div>`;
    }
    function downloadText(content,name){window.LedgerDownloads.save(content,'application/json;charset=utf-8',name);}
    $('appendix-notes-form').addEventListener('input',e=>{if(e.target.name?.startsWith('note-')){state.appendix.notes[e.target.name.slice(5)]=e.target.value;$('appendix-save-status').textContent='有未保存修改';}});
    $('appendix-notes-form').addEventListener('submit',e=>{e.preventDefault();const stored=save();$('appendix-save-status').textContent=stored?'已保存在当前浏览器':'仅当前会话，请备份';toast(stored?'本期附注已保存，会随当前批次和Excel导出。':'当前浏览器未能持久保存，请使用“备份附录”留存。');});
    $('appendix-edit-form').addEventListener('submit',e=>{
      e.preventDefault();if(!editing)return;const fields=editing.type==='factor'?A.FACTOR_FIELDS:A.TRACE_FIELDS;const data={};
      for(const [k]of fields)data[k]=e.target.elements.namedItem(k).value.trim();
      for(const k of ['source_url','evidence_url'])if(data[k]&&!A.link(data[k])){$('appendix-dialog-error').textContent='链接请填写不含账号密码的http或https地址，或暂时留空。';return;}
      if(editing.type==='factor')Object.assign(state.appendix.factors[editing.key],data);else state.appendix.records[editing.id]=data;
      const stored=save();$('appendix-dialog').close();if($('detail-dialog').open)$('detail-dialog').close();render();toast(stored?'附录资料已登记，主账排放量保持不变。':'资料已登记在当前会话，请使用“备份附录”留存。');
    });
    $('close-appendix').addEventListener('click',()=>$('appendix-dialog').close());
    document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
      if(b.dataset.appendixTab){tab=b.dataset.appendixTab;page=1;render();}
      if(b.dataset.factorEdit!==undefined)edit('factor',Number(b.dataset.factorEdit));
      if(b.dataset.traceEdit!==undefined)edit('trace',Number(b.dataset.traceEdit));
      if(b.dataset.appendixPage){page+=Number(b.dataset.appendixPage);renderTrace();}
    });
    $('appendix-backup').addEventListener('click',()=>{if(state.result){downloadText(A.backup(state.csv,state.appendix),'碳账附录备份_'+state.batchId+'.json');toast('附录备份已生成，包含本批原始CSV与附录资料，请妥善保留。');}});
    $('appendix-restore').addEventListener('click',()=>$('appendix-file').click());
    $('appendix-file').addEventListener('change',async e=>{const f=e.target.files[0];if(!f)return;try{const next=A.restore(await f.text(),state.csv,state.result,state);state.appendix=next;save();render();toast('附录已恢复；原始排放与汇总规则保持不变。');}catch(ex){error('恢复未完成：'+ex.message);}e.target.value='';});
    return{render,traceDetails};
  };
})();
