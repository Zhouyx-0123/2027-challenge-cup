"""Finance workbench: all forms use the same validated v3 service as the CLI."""
from pathlib import Path
import json
import pandas as pd
import streamlit as st
from src.finance_workflow import finance_dir
from tools import finance_register_v3 as service

def render():
    st.subheader('财务登记与核验')
    st.caption('票面信息照录。费用所属期、抵扣和成本由人工确认。操作人目前为自报身份，不代表登录认证。')
    actor=st.text_input('操作人（必填）',key='finance_actor')
    mode_label=st.selectbox('账簿数据性质',['真实','模拟'],key='finance_mode')
    mode='real' if mode_label=='真实' else 'simulated'
    path=finance_dir()/('finance_master_v3_'+mode+'.xlsx')
    legacy=finance_dir()/('finance_master_'+mode+'.xlsx')
    migration_needed=legacy.exists() and not path.exists()
    if migration_needed:st.warning('发现旧版登记账簿，请先迁移，避免形成两个账簿。')
    raw=st.session_state.get('finance_invoice_raw');image=st.session_state.get('finance_invoice_image')
    if raw and image:
        source_mode='simulated' if raw.get('mock') is True else 'real'
        st.write({k:raw.get(k) for k in ['item_name','invoice_no','invoice_date','invoice_amount','tax_amount','tax_rate']})
        if raw.get('mock') is True:st.warning('当前识别结果为模拟数据，请选择模拟账簿。')
        legacy=finance_dir()/('finance_master_'+mode+'.xlsx')
        migration_needed=legacy.exists() and not path.exists()
        if migration_needed:st.warning('发现旧版登记账簿，请先在旧表迁移页导入，避免形成两个账簿。')
        if st.button('记录到财务主表',key='finance_register_save',disabled=not actor.strip() or migration_needed):
            try:
                if type(raw.get('mock')) is not bool:raise ValueError('识别结果缺少来源标记')
                if source_mode!=mode:raise ValueError('请选择与识别来源一致的账簿')
                report=service.add(path,raw,mode,actor,image=image[0],filename=image[1])
                if report['rejected']:st.warning('已保存到异常待处理：'+report['rejected'][0]['reason'])
                elif report['duplicates']:st.info('重复票，未重复登记。')
                else:st.success('已登记并记录操作人和时间。')
            except (ValueError,OSError) as exc:st.error(str(exc))
    tabs=st.tabs(['已登记与汇总','异常处理／人工登记','旧表迁移'])
    with tabs[2]:
        st.write('支持原28/33列主表。迁移生成v3文件和原文件备份，不覆盖已有v3账簿。历史录入人、时间未知时不补造。')
        upload=st.file_uploader('选择旧财务主表',type=['xlsx'],key='finance_legacy')
        if st.button('迁移旧表',disabled=upload is None or not actor.strip()):
            try:st.success(str(service.migrate(upload.getvalue(),path,mode,actor)));st.rerun()
            except (ValueError,OSError,KeyError) as exc:st.error(str(exc))
    with tabs[0]:
        try:wb=service.load(path);rows=service.records(wb)
        except (ValueError,OSError) as exc:st.error(str(exc));return
        basis_label=st.selectbox('汇总日期口径',['开票月份','费用所属期'])
        basis='invoice_month' if basis_label=='开票月份' else 'expense_period'
        month=st.text_input('汇总月份（YYYY-MM，留空为全部）')
        try:
            if month:service.period(month)
            result=service.summary(path,month or None,basis)
            c=st.columns(3);c[0].metric('发票张数',result['invoice_count']);c[1].metric('价税合计',result['invoice_amount']);c[2].metric('待核验张数',result['pending_verification'])
            st.write({'不含税金额合计':result['amount_excluding_tax'],'票面税额合计':result['tax_amount'],'待判断抵扣资格的票面税额':result['pending_deduction_face_tax'],'已确认可抵扣税额':result['confirmed_deductible_tax'],'待分类张数':result['pending_classification']})
            st.caption(f"全账簿未确认所属期：{result['unassigned_period_count']}张；待处理异常：{result['open_exceptions']}条；当前范围税额缺失：{result['missing_tax_count']}张。缺失金额未纳入对应金额合计。分组按明细统计，张数按发票统计。")
            st.caption(f"全账簿累计登记动作：{result['registration_events']}；重复跳过：{result['duplicate_events']}；人工不予登记：{result['rejected_exception_count']}。用于导入核对，不代表银行或总账对账。")
            st.dataframe(pd.DataFrame([{'费用类别':k,'价税合计':v} for k,v in result['category_totals'].items()]),hide_index=True)
        except ValueError as exc:st.error(str(exc))
        keyword=st.text_input('检索票号／品名／销售方／车牌／运单')
        category=st.selectbox('类别筛选',['全部']+service.CATEGORIES)
        def in_month(r):
            v=r[basis];v=v.strftime('%Y-%m') if hasattr(v,'strftime') else str(v or '')
            return not month or v==month
        filtered=[r for r in rows if in_month(r) and (category=='全部' or r['expense_category']==category) and (not keyword or keyword in ' '.join(str(r.get(k) or '') for k in ['invoice_no','item_name','seller_name','plate','waybill_no']))]
        st.dataframe(pd.DataFrame([{h:r.get(k) for h,k in zip(service.HEADERS,service.FIELDS)} for r in filtered]),hide_index=True)
        if path.exists():
            st.download_button('下载财务主表（全部记录与明细、日志）',path.read_bytes(),file_name=path.name)
            st.download_button('下载下游接口数据（JSON）',service.json_dump(service.export_bundle(path)),file_name='finance_master_v3_'+mode+'.json')
        if filtered:
            rid=st.selectbox('选择记录', [r['record_id'] for r in filtered],format_func=lambda x:next(r['invoice_no']+' / '+r['item_name'] for r in filtered if r['record_id']==x))
            r=next(x for x in rows if x['record_id']==rid);rev=r['revision']
            st.write('记录版本：',rev)
            ev=Path(r['evidence_ref']) if r.get('evidence_ref') else None
            if ev and ev.is_file():
                st.download_button('下载登记时原票',ev.read_bytes(),file_name=ev.name,key='evidence_'+rid)
            lines=[x for x in wb['单据明细'].iter_rows(min_row=2,values_only=True) if x[0]==rid]
            st.dataframe(pd.DataFrame(lines,columns=service.LINE_HEADERS),hide_index=True)
            with st.form('basic_'+rid):
                ep=st.text_input('费用所属期（留空为待确认）',value=r.get('expense_period') or '')
                values={}
                for key,label in [('plate','车牌号'),('waybill_no','运单号'),('trip_id','趟或段ID'),('activity_id','活动记录ID'),('run_id','核算批次ID'),('note','备注')]:values[key]=st.text_input(label,value=str(r.get(key) or ''))
                why=st.text_input('修改原因',key='basic_reason_'+rid)
                if st.form_submit_button('保存期间与关联'):
                    try:service.update(path,rid,dict(values,expense_period=ep),actor,why,rev);st.rerun()
                    except ValueError as exc:st.error(str(exc))
            with st.form('classes_'+rid):
                cats={}
                choices=[x for x in service.CATEGORIES if x!='混合费用']
                for line in lines:cats[line[1]]=st.selectbox(line[2]+'：费用类别',choices,index=choices.index(line[3]) if line[3] in choices else choices.index('待分类'),key=line[1])
                why=st.text_input('改类原因',key='class_reason_'+rid)
                if st.form_submit_button('保存明细类别'):
                    try:service.update_lines(path,rid,cats,actor,why,rev);st.rerun()
                    except ValueError as exc:st.error(str(exc))
            with st.expander('审核与抵扣人工回填'):
                with st.form('review_'+rid):
                    statuses=['待核验','一致','偏差可解释','异常待复核'];vs=st.selectbox('双验判定',statuses,index=statuses.index(r['verification_status']) if r['verification_status'] in statuses else 0)
                    posting=st.selectbox('是否允许入账',['待确认','允许','不允许'],index=1 if r['posting_allowed'] is True else 2 if r['posting_allowed'] is False else 0)
                    why=st.text_input('审核依据／原因')
                    if st.form_submit_button('保存审核'):
                        try:service.update(path,rid,{'verification_status':vs,'posting_allowed':{'待确认':'待确认','允许':True,'不允许':False}[posting]},actor,why,rev);st.rerun()
                        except ValueError as exc:st.error(str(exc))
                with st.form('deduct_'+rid):
                    choices=['待确认','可抵扣','不可抵扣','部分抵扣'];ds=st.selectbox('抵扣状态',choices,index=choices.index(r['deduction_status']) if r['deduction_status'] in choices else 0)
                    amount=st.text_input('人工确认抵扣额（未知留空）',value=str(r.get('deductible_input_tax') if r.get('deductible_input_tax') is not None else ''))
                    why=st.text_input('抵扣判断依据',value=r.get('deduction_basis') or '')
                    if st.form_submit_button('保存抵扣判断'):
                        try:service.update(path,rid,{'deduction_status':ds,'deductible_input_tax':amount,'deduction_basis':why},actor,why or '恢复待确认',rev);st.rerun()
                        except ValueError as exc:st.error(str(exc))
            with st.expander('下游成本与跨趟分摊回填'):
                st.caption('只接收人工决定，不自动制定成本或分摊规则。已有分摊时须先清空，再改确认成本。')
                with st.form('cost_'+rid):
                    cost=st.text_input('人工确认成本',value=str(r['confirmed_cost'] if r['confirmed_cost'] is not None else ''))
                    why=st.text_input('成本确认依据')
                    if st.form_submit_button('保存确认成本'):
                        try:service.update(path,rid,{'confirmed_cost':cost},actor,why,rev);st.rerun()
                        except ValueError as exc:st.error(str(exc))
                alloc=[{'line_id':x[1],'trip_id':x[2],'amount':str(x[3])} for x in wb['分摊明细'].iter_rows(min_row=2,values_only=True) if x[0]==rid]
                edited=st.data_editor(pd.DataFrame(alloc,columns=['line_id','trip_id','amount']),num_rows='dynamic',key='allocation_'+rid)
                why=st.text_input('分摊依据／修改原因',key='allocation_reason_'+rid)
                if st.button('保存分摊明细（空表表示清空）'):
                    try:service.allocate(path,rid,edited.to_dict('records'),actor,why,rev);st.rerun()
                    except ValueError as exc:st.error(str(exc))
            with st.expander('登记及修改历史'):
                st.dataframe(pd.DataFrame([x for x in wb['登记操作记录'].iter_rows(min_row=2,values_only=True) if x[0]==rid],columns=service.AUDIT_HEADERS),hide_index=True)
    with tabs[1]:
        queue=[x for x in wb['异常待处理'].iter_rows(min_row=2,values_only=True) if x[5]=='待处理']
        st.dataframe(pd.DataFrame(queue,columns=service.QUEUE_HEADERS),hide_index=True)
        selected=st.selectbox('处理对象',['新建人工登记']+[x[0] for x in queue])
        q=next((x for x in queue if x[0]==selected),None)
        rawq=json.loads(q[2]) if q else {}
        if q:
            st.info('原异常原因：'+q[4])
            if q[3] and Path(q[3]).is_file():st.download_button('下载异常原票',Path(q[3]).read_bytes(),file_name=Path(q[3]).name)
            discard_reason=st.text_input('不予登记的原因（仅不登记时填写）')
            if st.button('将该异常标记为不予登记'):
                try:service.close_exception(path,q[0],actor,discard_reason);st.rerun()
                except ValueError as exc:st.error(str(exc))
        evidence=st.file_uploader('补充票图或PDF（新建登记必填）',type=['png','jpg','jpeg','pdf'],key='manual_evidence')
        with st.form('manual_'+selected):
            no=st.text_input('发票号码',value=str(rawq.get('invoice_no') or ''))
            code=st.text_input('发票代码',value=str(rawq.get('invoice_code') or ''))
            date=st.text_input('开票日期 YYYY-MM-DD',value=str(rawq.get('invoice_date') or ''))
            seller=st.text_input('销售方',value=str(rawq.get('seller_name') or ''))
            itype=st.text_input('发票类型',value=str(rawq.get('invoice_type') or ''))
            gross=st.text_input('票面价税合计',value=str(rawq.get('invoice_amount') or ''))
            original=st.text_input('红字票对应原发票号码（普通票留空）',value=str(rawq.get('original_invoice_no') or ''))
            ep=st.text_input('人工确认费用所属期（YYYY-MM，可留空）',key='manual_period_'+selected)
            cols=['item_name','expense_category','amount_excluding_tax','tax_amount','invoice_amount','tax_rate']
            seed=rawq.get('items') or ([rawq] if rawq.get('item_name') else [])
            frame=pd.DataFrame([{k:str(x.get(k)) if x.get(k) is not None else '' for k in cols} for x in seed],columns=cols)
            details=st.data_editor(frame,num_rows='dynamic',key='manual_lines_'+selected,column_config={'item_name':'品名','expense_category':st.column_config.SelectboxColumn('费用类别',options=[c for c in service.CATEGORIES if c!='混合费用']),'amount_excluding_tax':'不含税金额','tax_amount':'票面税额','invoice_amount':'价税合计','tax_rate':'税率（如13%）'})
            why=st.text_input('人工处理依据')
            confirm=st.checkbox('已核对原票、明细金额及红字票关联信息')
            if st.form_submit_button('确认并登记',disabled=migration_needed):
                try:
                    if not confirm:raise ValueError('请先核对原票并勾选确认')
                    if not evidence and not (q and q[3]):raise ValueError('请上传原票证据')
                    if q and q[1]!=mode:raise ValueError('异常数据性质不匹配')
                    data=dict(invoice_no=no,invoice_code=code,invoice_date=date,seller_name=seller,invoice_type=itype,invoice_amount=gross,original_invoice_no=original,expense_period=ep,items=details.fillna('').to_dict('records'),evidence_ref=q[3] if q else None)
                    if not data['items']:raise ValueError('请填写至少一条明细')
                    service.add(path,data,mode,actor,image=evidence.getvalue() if evidence else None,filename=evidence.name if evidence else '',manual=True,reason=why,queue_id=q[0] if q else None);st.rerun()
                except (ValueError,OSError) as exc:st.error(str(exc))
