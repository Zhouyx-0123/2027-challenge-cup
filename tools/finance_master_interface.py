"""Local JSON interfaces for the 33-column register; no network or tax decisions.

export_master(path, mode) -> JSON-compatible envelope
apply_updates(path, envelope) -> atomic, audited, idempotent update
"""
from __future__ import annotations
import argparse
import json
import os
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
try:
    from .invoice_to_master import HEADERS, number, text
except ImportError:
    from invoice_to_master import HEADERS, number, text

SCHEMA='finance_master_v2'
FIELDS=['record_id','activity_id','source_mode','invoice_month','invoice_date','invoice_code','invoice_no','seller_name','item_name','expense_category','quantity','unit','unit_price','fuel_liters','amount_excluding_tax','tax_amount','invoice_amount','confirmed_cost','plate','waybill_no','trip_id','run_id','verification_status','posting_allowed','reviewer','reviewed_at','evidence_ref','note','invoice_type','tax_rate','deduction_status','deductible_input_tax','deduction_basis']
COL={name:i+1 for i,name in enumerate(FIELDS)}
NUMERIC={'quantity','unit_price','fuel_liters','amount_excluding_tax','tax_amount','invoice_amount','confirmed_cost','deductible_input_tax'}
GROUPS={
    'association':{'activity_id','plate','waybill_no','trip_id','run_id'},
    'verification':{'verification_status','posting_allowed','reviewer','reviewed_at'},
    'deduction':{'deduction_status','deductible_input_tax','deduction_basis'},
    'cost':{'confirmed_cost'},
    'classification':{'expense_category'},
}
CATEGORIES={'燃料','电力','路桥','维修','碳配额购买','履约','其他','待分类'}

def serial(v,field):
    if v is None or v=='': return None
    if field=='source_mode':
        if v not in {'模拟','真实'}: raise ValueError('数据性质无效')
        return {'模拟':'simulated','真实':'real'}[v]
    if field=='posting_allowed':
        if v not in {'是','否','待确认'}: raise ValueError('入账许可无效')
        return {'是':True,'否':False,'待确认':None}[v]
    if isinstance(v,(datetime,date)):
        return v.strftime('%Y-%m') if field=='invoice_month' else v.isoformat(timespec='seconds') if field=='reviewed_at' and isinstance(v,datetime) else v.strftime('%Y-%m-%d')
    if field in NUMERIC or (field=='tax_rate' and isinstance(v,(int,float,Decimal))):return str(number(v))
    return str(v)

def load_register(path,mode):
    from openpyxl import load_workbook
    if mode not in {'real','simulated'}:raise ValueError('source_mode须为real或simulated')
    wb=load_workbook(path)
    if '财务主表' not in wb.sheetnames:raise ValueError('缺少财务主表')
    ws=wb['财务主表']
    if ws.max_column>33 or [ws.cell(1,i).value for i in range(1,34)]!=HEADERS:raise ValueError('主表须使用33列v2表头')
    records={}
    for row in ws.iter_rows(min_row=2,max_col=33):
        if all(c.value is None for c in row):continue
        if any(c.data_type=='f' for c in row):raise ValueError('接口仅接收已落定的记录值，不接受主表公式')
        item={f:serial(c.value,f) for f,c in zip(FIELDS,row)}
        rid=item['record_id']
        if not rid or rid in records:raise ValueError('记录ID缺失或重复')
        if item['source_mode']!=mode:raise ValueError('真实与模拟记录不得混用')
        records[rid]=(row[0].row,item)
    return wb,ws,records

def export_master(path,mode):
    _,_,records=load_register(Path(path),mode)
    return {'schema_version':SCHEMA,'source_mode':mode,'records':[r for _,r in records.values()]}

def timestamp(v):
    if not isinstance(v,str):raise ValueError('reviewed_at须为含时区的ISO时间')
    try:dt=datetime.fromisoformat(v.replace('Z','+00:00'))
    except ValueError:raise ValueError('reviewed_at格式无效')
    if dt.tzinfo is None:raise ValueError('reviewed_at必须带时区')
    return dt.isoformat(timespec='seconds')

def canonical(field,v):
    if field=='posting_allowed':
        if v is not None and type(v) is not bool:raise ValueError('posting_allowed须为true、false或null')
        return v
    if field in NUMERIC:
        n=number(v)
        if n is not None and (n<0 or n!=n.quantize(Decimal('.01'))):raise ValueError('金额须非负且至多两位小数')
        return None if n is None else str(n)
    if v is None:return None
    if not isinstance(v,str) or not v.strip():raise ValueError('文本字段须为非空字符串或null')
    return timestamp(v) if field=='reviewed_at' else v.strip()

def equal(field,a,b):
    if field in NUMERIC:return number(a)==number(b)
    return a==b

def apply_updates(path,envelope,*,dry_run=False):
    """All-or-nothing per batch. expected must include exactly the updated fields.

    CLI/file caller authenticates the producer; this is not a public HTTP service.
    source identifies the module for audit, not proof of authorization.
    """
    if not isinstance(envelope,dict) or envelope.get('schema_version')!=SCHEMA:raise ValueError('schema_version不匹配')
    mode=envelope.get('source_mode');kind=envelope.get('kind')
    if kind not in GROUPS:raise ValueError('未知接口kind')
    audit={k:text(envelope.get(k)) for k in ['source','reviewer','reason']}
    if not all(audit.values()):raise ValueError('source、reviewer、reason必须填写')
    at=timestamp(envelope.get('reviewed_at'))
    updates=envelope.get('updates')
    if not isinstance(updates,list) or not updates:raise ValueError('updates须为非空列表')
    path=Path(path);wb,ws,records=load_register(path,mode)
    plans=[];seen=set();unchanged=0
    for u in updates:
        if not isinstance(u,dict):raise ValueError('更新项须为对象')
        rid=u.get('record_id')
        if not rid or rid in seen or rid not in records:raise ValueError('记录ID不存在或批内重复')
        seen.add(rid);ri,old=records[rid]
        vals=u.get('values');expected=u.get('expected')
        if not isinstance(vals,dict) or not vals or not set(vals)<=GROUPS[kind]:raise ValueError('不允许修改该接口以外的字段')
        if not isinstance(expected,dict) or set(expected)!=set(vals):raise ValueError('expected须完整覆盖此次values中的字段')
        new={f:canonical(f,v) for f,v in vals.items()}
        exp={f:canonical(f,v) for f,v in expected.items()}
        if all(equal(f,old[f],v) for f,v in new.items()):unchanged+=1;continue
        if any(not equal(f,old[f],v) for f,v in exp.items()):raise ValueError('旧值已变化，拒绝覆盖；重新导出后核对')
        merged={**old,**new}
        if kind=='classification' and merged['expense_category'] not in CATEGORIES:raise ValueError('费用类别无效')
        if kind=='verification':
            if set(new)!=GROUPS[kind]:raise ValueError('双验回填必须完整包含四个字段')
            if merged['verification_status'] not in {'待核验','一致','偏差可解释','异常待复核'}:raise ValueError('双验状态无效')
            if merged['reviewer']!=audit['reviewer'] or merged['reviewed_at']!=at:raise ValueError('审核人与时间须和批次一致')
            if merged['posting_allowed'] is True and merged['verification_status'] not in {'一致','偏差可解释'}:raise ValueError('未通过双验不得标允许入账')
        if kind=='deduction':
            if set(new)!=GROUPS[kind]:raise ValueError('抵扣回填必须完整包含三个字段')
            status=merged['deduction_status'];v=number(merged['deductible_input_tax']);tax=number(old['tax_amount'])
            if status=='待确认':
                if v is not None:raise ValueError('待确认时抵扣金额须为空')
            elif status in {'可全额抵扣','可部分抵扣','不可抵扣'}:
                if v is None or not merged['deduction_basis']:raise ValueError('已判断需提供金额与依据')
                if status=='不可抵扣' and v!=0:raise ValueError('不可抵扣金额须为0')
                if status!='不可抵扣' and (tax is None or v>tax):raise ValueError('缺少票面税额或抵扣金额超过税额')
                if status=='可全额抵扣' and v!=tax:raise ValueError('全额抵扣金额应与票面税额一致')
                if status=='可部分抵扣' and not 0<v<tax:raise ValueError('部分抵扣金额须在0与税额之间')
            else:raise ValueError('抵扣状态无效')
        plans.append((ri,rid,{f:old[f] for f in new},new))
    report={'updated':len(plans),'unchanged':unchanged,'dry_run':dry_run}
    if dry_run or not plans:return report
    from openpyxl.styles import Font,PatternFill
    log=wb['接口回填记录'] if '接口回填记录' in wb.sheetnames else wb.create_sheet('接口回填记录')
    if log.max_row==1 and log['A1'].value is None:
        for ci,v in enumerate(['记录ID','接口','来源','审核人','审核时间','原因','原值JSON','新值JSON'],1):log.cell(1,ci,v)
    for ri,rid,old,new in plans:
        for f,v in new.items():
            c=ws.cell(ri,COL[f])
            if f=='posting_allowed':v='待确认' if v is None else '是' if v else '否'
            elif f in NUMERIC:v=None if v is None else float(Decimal(v))
            c.value=v
            if isinstance(v,str):c.data_type='s'
        # Values are appended as literal strings, never Excel formulas.
        row=[rid,kind,audit['source'],audit['reviewer'],at,audit['reason'],json.dumps(old,ensure_ascii=False),json.dumps(new,ensure_ascii=False)]
        for ci,v in enumerate(row,1):
            c=log.cell(log.max_row+1 if ci==1 else log.max_row,ci,v);c.data_type='s';c.font=Font(name='Calibri',size=11);c.fill=PatternFill()
    fd,tmp=tempfile.mkstemp(suffix='.xlsx',dir=path.parent);os.close(fd)
    try:wb.save(tmp);os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    ex=sub.add_parser('export');ex.add_argument('--master',type=Path,required=True);ex.add_argument('--output',type=Path,required=True);ex.add_argument('--source-mode',choices=['real','simulated'],required=True)
    ap=sub.add_parser('apply');ap.add_argument('--master',type=Path,required=True);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    try:
        if a.command=='export':
            result=export_master(a.master,a.source_mode);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'exported':len(result['records'])}));return 0
        result=apply_updates(a.master,json.loads(a.input.read_text(encoding='utf-8-sig')),dry_run=a.dry_run);print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,OSError) as e:print(json.dumps({'error':str(e)},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
