"""Finance workbook v3: invoice headers, lines, exception queue and audit trail.

No automatic deductibility, accounting-period or allocation decisions.
All mutations are serialized and atomically replace the workbook.
"""
from __future__ import annotations
import argparse
from copy import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import uuid
from openpyxl import Workbook, load_workbook
from tools.invoice_to_master import HEADERS as V2_HEADERS, MAP, number, rate, text
from tools.finance_master_interface import FIELDS as V2_FIELDS

EXTRA = ['费用所属期','所属期确认状态','录入人','录入时间','更新人','更新时间','原发票号码','版本号']
FIELDS = V2_FIELDS + ['expense_period','period_status','created_by','created_at','updated_by','updated_at','original_invoice_no','revision']
HEADERS = V2_HEADERS + EXTRA
CATEGORIES = ['燃料','电力','路桥','维修','运输','仓储','保险','租赁','年检','办公','碳配额购买','履约','其他','待分类','混合费用']
LINE_HEADERS = ['记录ID','明细ID','品名','费用类别','不含税金额','税额','价税合计','税率/征收率']
AUDIT_HEADERS = ['记录ID','动作','操作人','时间','原因','修改前','修改后']
QUEUE_HEADERS = ['异常ID','数据性质','原始识别JSON','证据索引','原因','状态','处理后记录ID','录入人','录入时间']
ALLOC_HEADERS = ['记录ID','明细ID','趟或段ID','分摊金额','操作人','时间','依据']
RULES = [('电力',['充电','电费','购电','电力']),('燃料',['柴油','汽油','天然气']),('路桥',['通行','过路','过桥','路桥']),('维修',['维修','修理','保养']),('运输',['运输','运费']),('仓储',['仓储','仓库']),('保险',['保险']),('租赁',['租赁','租金']),('年检',['年检','车辆检测']),('办公',['办公用品','文具']),('碳配额购买',['碳排放配额','碳配额'])]

def classify(name):
    hits=[label for label,words in RULES if any(word in text(name) for word in words)]
    return hits[0] if len(hits)==1 else '待分类'

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def json_dump(value): return json.dumps(value,ensure_ascii=False,default=str,sort_keys=True)
def money(value):
    n=number(value)
    if n is None: return None
    if n != n.quantize(Decimal('.01')): raise ValueError('金额最多两位小数')
    return n

def literal(ws, values):
    ws.append([float(v) if isinstance(v,Decimal) else v for v in values])
    for cell in ws[ws.max_row]:
        if isinstance(cell.value,str): cell.data_type='s'

def sheets(wb):
    for title,headers in [('财务主表',HEADERS),('单据明细',LINE_HEADERS),('登记操作记录',AUDIT_HEADERS),('异常待处理',QUEUE_HEADERS),('分摊明细',ALLOC_HEADERS)]:
        if title not in wb:
            ws=wb.create_sheet(title); literal(ws,headers)
    return wb

def empty_book():
    wb=Workbook(); wb.remove(wb.active); return sheets(wb)

def load(path):
    if not Path(path).exists(): return empty_book()
    wb=load_workbook(path)
    if '财务主表' not in wb or [c.value for c in wb['财务主表'][1]] != HEADERS:
        raise ValueError('请先通过“旧表迁移”导入28/33列工作簿，不覆盖旧表')
    return sheets(wb)

@contextmanager
def transaction(path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    lock=path.with_suffix(path.suffix+'.lock')
    try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError: raise ValueError('工作簿正在写入，请稍后重试；异常退出遗留锁需管理员确认后移除')
    temp=None
    try:
        wb=load(path); yield wb
        handle,temp=tempfile.mkstemp(suffix='.xlsx',dir=path.parent); os.close(handle)
        wb.save(temp); os.replace(temp,path)
    finally:
        if temp and Path(temp).exists(): Path(temp).unlink()
        os.close(fd); lock.unlink(missing_ok=True)

def records(wb):
    return [dict(zip(FIELDS,row)) for row in wb['财务主表'].iter_rows(min_row=2,values_only=True) if any(v is not None for v in row)]

def list_records(path): return records(load(path))
def row_at(wb,rid):
    for i,row in enumerate(wb['财务主表'].iter_rows(min_row=2,values_only=True),2):
        if row[0]==rid:return i,dict(zip(FIELDS,row))
    raise ValueError('记录不存在')

def audit(wb,rid,action,actor,reason,before,after):
    if not text(actor) or not text(reason):raise ValueError('操作人和原因必须填写')
    literal(wb['登记操作记录'],[rid,action,actor,now(),reason,json_dump(before),json_dump(after)])

def period(value):
    if not text(value):return None
    datetime.strptime(text(value),'%Y-%m')
    if len(text(value))!=7:raise ValueError('所属期须为YYYY-MM')
    return text(value)

def normal(raw, allow_special=False):
    if not isinstance(raw,dict):raise ValueError('发票数据须为对象')
    if text(raw.get('status')).lower() in {'failed','offline','error'} or raw.get('error'):raise ValueError('识别失败，请人工核对修正')
    no=text(raw.get('invoice_no')); date=text(raw.get('invoice_date'))
    if not no:raise ValueError('缺少发票号码')
    dt=datetime.strptime(date,'%Y-%m-%d')
    total=money(raw.get('invoice_amount'))
    if total is None:raise ValueError('缺少价税合计')
    items=raw.get('items') or [raw]
    if not isinstance(items,list) or not all(isinstance(x,dict) for x in items):raise ValueError('明细须为对象列表')
    if (len(items)>1 or raw.get('multiple_items') or total<0) and not allow_special:
        raise ValueError('多明细/红字票需在异常处理区人工确认')
    if raw.get('multiple_items') and len(items)<2:raise ValueError('多明细票需要填写至少两条明细')
    lines=[]
    for item in items:
        name=text(item.get('item_name'))
        if not name:raise ValueError('缺少明细品名')
        net,tax,gross=[money(item.get(k)) for k in ['amount_excluding_tax','tax_amount','invoice_amount']]
        if gross is None and net is not None and tax is not None:gross=net+tax
        if gross is None:raise ValueError('每条明细须有价税合计')
        if net is not None and tax is not None and abs(net+tax-gross)>Decimal('.01'):raise ValueError('明细金额与税额勾稽不符')
        if (total<0 and any(x is not None and x>0 for x in (net,tax,gross))) or (total>=0 and any(x is not None and x<0 for x in (net,tax,gross))):raise ValueError('明细金额正负号与票面总额不一致')
        cat=text(item.get('expense_category')) or classify(name)
        if cat not in CATEGORIES or cat=='混合费用':raise ValueError('明细费用类别无效')
        lines.append(dict(item_name=name,expense_category=cat,amount_excluding_tax=net,tax_amount=tax,invoice_amount=gross,tax_rate=rate(item.get('tax_rate'))))
    if abs(sum(x['invoice_amount'] for x in lines)-total)>Decimal('.01'):raise ValueError('明细合计与发票价税合计不符')
    out={k:None for k in FIELDS}
    for k in V2_FIELDS:
        if k in raw and k not in {'record_id','source_mode','confirmed_cost','verification_status','posting_allowed','reviewer','reviewed_at','deduction_status','deductible_input_tax','deduction_basis'}:out[k]=raw[k]
    for k in ['quantity','unit_price','fuel_liters']:out[k]=number(raw.get(k))
    out.update(invoice_no=no,invoice_code=text(raw.get('invoice_code')),invoice_date=dt,invoice_month=dt.replace(day=1),invoice_amount=total,
        item_name='；'.join(x['item_name'] for x in lines),expense_category=lines[0]['expense_category'] if len({x['expense_category'] for x in lines})==1 else '混合费用',tax_rate=lines[0]['tax_rate'] if len(lines)==1 else '多税率/见明细')
    for k in ['amount_excluding_tax','tax_amount']:
        vals=[x[k] for x in lines]; value=sum(vals) if all(x is not None for x in vals) else None
        stated=money(raw.get(k))
        if stated is not None and value is not None and abs(stated-value)>Decimal('.01'):raise ValueError('明细合计与票面'+k+'不符')
        out[k]=stated if stated is not None else value
    if out['amount_excluding_tax'] is not None and out['tax_amount'] is not None and abs(out['amount_excluding_tax']+out['tax_amount']-total)>Decimal('.01'):raise ValueError('票面金额勾稽不符')
    original=text(raw.get('original_invoice_no'))
    if total<0 and not original:raise ValueError('红字票需填写原发票号码供人工核验')
    out.update(expense_period=period(raw.get('expense_period')),period_status='已确认' if raw.get('expense_period') else '待确认',original_invoice_no=original or None,verification_status='待核验',posting_allowed='待确认',deduction_status='待确认',revision=1)
    return out,lines

def evidence_save(path,image,filename):
    if not image:return None
    digest=hashlib.sha256(image).hexdigest(); suffix=Path(filename).suffix.lower()
    if suffix not in {'.jpg','.jpeg','.png','.webp','.gif','.pdf'}:suffix='.bin'
    target=Path(path).parent/'evidence'/(digest+suffix);target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():target.write_bytes(image)
    return str(target.resolve())

def add(path,raw,mode,actor,*,image=None,filename='',manual=False,reason='发票登记',queue_id=None):
    if mode not in {'real','simulated'}:raise ValueError('数据性质无效')
    if not text(actor):raise ValueError('请填写录入人（自报身份）')
    if mode=='real' and raw.get('mock') is True:raise ValueError('模拟数据不能登记为真实')
    evidence=evidence_save(path,image,filename) or raw.get('evidence_ref') or raw.get('source_image')
    with transaction(path) as wb:
        try:
            data,lines=normal(raw,manual)
            if any(r['source_mode']!=('真实' if mode=='real' else '模拟') for r in records(wb)):raise ValueError('真实与模拟必须分工作簿')
            for old in records(wb):
                if text(old['invoice_code'])==data['invoice_code'] and old['invoice_no']==data['invoice_no']:
                    old_lines=[dict(zip(['item_name','expense_category','amount_excluding_tax','tax_amount','invoice_amount','tax_rate'],r[2:])) for r in wb['单据明细'].iter_rows(min_row=2,values_only=True) if r[0]==old['record_id']]
                    # Canonical decimals avoid Excel float/int representation differences.
                    def canonical(xs):
                        return [(x['item_name'],)+tuple(str(number(x[k]).normalize()) if number(x[k]) is not None else None for k in ['amount_excluding_tax','tax_amount','invoice_amount'])+(str(rate(x['tax_rate']).normalize()) if isinstance(rate(x['tax_rate']),Decimal) else text(x['tax_rate']),) for x in xs]
                    if canonical(old_lines)==canonical(lines) and old['invoice_date']==data['invoice_date'] and text(old['seller_name'])==text(data['seller_name']):
                        audit(wb,old['record_id'],'重复跳过',actor,reason,{}, {})
                        if queue_id:resolve(wb,queue_id,old['record_id'])
                        return {'added':0,'duplicates':1,'rejected':[],'record_id':old['record_id']}
                    raise ValueError('同票号数据冲突，请查看已登记记录；不能覆盖原票')
        except (ValueError,TypeError) as exc:
            if manual:raise
            qid='Q-'+uuid.uuid4().hex
            literal(wb['异常待处理'],[qid,mode,json_dump(raw),evidence,str(exc),'待处理',None,actor,now()])
            audit(wb,qid,'转待处理',actor,str(exc),{},raw)
            return {'added':0,'duplicates':0,'rejected':[{'reason':str(exc),'queue_id':qid}]}
        rid='INV-'+uuid.uuid4().hex;at=now()
        data.update(record_id=rid,source_mode='真实' if mode=='real' else '模拟',created_by=actor,created_at=at,updated_by=actor,updated_at=at,evidence_ref=evidence)
        literal(wb['财务主表'],[data[k] for k in FIELDS])
        for i,line in enumerate(lines,1):literal(wb['单据明细'],[rid,f'{rid}-{i}',*[line[k] for k in ['item_name','expense_category','amount_excluding_tax','tax_amount','invoice_amount','tax_rate']]])
        audit(wb,rid,'登记',actor,reason,{},data)
        if queue_id:resolve(wb,queue_id,rid)
        return {'added':1,'duplicates':0,'rejected':[],'record_id':rid}

def resolve(wb,qid,rid):
    ws=wb['异常待处理']
    for i,row in enumerate(ws.iter_rows(min_row=2,values_only=True),2):
        if row[0]==qid:
            if row[5]!='待处理':raise ValueError('异常单已处理，请刷新')
            ws.cell(i,6,'已处理');ws.cell(i,7,rid);return
    raise ValueError('异常单不存在')

EDITABLE={'expense_category','expense_period','plate','waybill_no','trip_id','activity_id','run_id','note','verification_status','posting_allowed','deduction_status','deductible_input_tax','deduction_basis','confirmed_cost'}
def update(path,rid,values,actor,reason,expected_revision):
    if not values or set(values)-EDITABLE:raise ValueError('包含不允许修改的字段')
    with transaction(path) as wb:
        i,old=row_at(wb,rid)
        if old['revision']!=expected_revision:raise ValueError('记录已被他人修改，请刷新后重试')
        new=dict(old,**values)
        if new['expense_category'] not in CATEGORIES:raise ValueError('类别无效')
        if 'expense_category' in values:
            detail_rows=[r for r in wb['单据明细'].iter_rows(min_row=2) if r[0].value==rid]
            if len(detail_rows)>1:raise ValueError('多明细票请逐条修改明细类别')
            if detail_rows:detail_rows[0][3].value=new['expense_category']
        if 'expense_period' in values:
            new['expense_period']=period(values['expense_period']);new['period_status']='已确认' if new['expense_period'] else '待确认'
        if {'verification_status','posting_allowed'} & set(values):
            if new['verification_status'] not in {'待核验','一致','偏差可解释','异常待复核'}:raise ValueError('审核状态无效')
            if new['posting_allowed'] not in {True,False,'待确认'}:raise ValueError('入账许可无效')
            if new['posting_allowed'] is True and new['verification_status'] not in {'一致','偏差可解释'}:raise ValueError('未通过核验不能允许入账')
            new.update(reviewer=actor,reviewed_at=now())
        if {'deduction_status','deductible_input_tax','deduction_basis'} & set(values):
            if new['deduction_status'] not in {'待确认','可抵扣','不可抵扣','部分抵扣'}:raise ValueError('抵扣状态无效')
            amount=money(new['deductible_input_tax']);tax=money(old['tax_amount'])
            if new['deduction_status']=='待确认' and amount is not None:raise ValueError('待确认时抵扣额应留空')
            if new['deduction_status']!='待确认':
                if not text(new['deduction_basis']) or amount is None:raise ValueError('抵扣判断需填写金额和依据')
                if new['deduction_status']=='不可抵扣' and amount!=0:raise ValueError('不可抵扣金额须为0')
                if amount!=0 and (tax is None or not min(Decimal(0),tax)<=amount<=max(Decimal(0),tax)):raise ValueError('抵扣金额超出票面税额范围')
            new['deductible_input_tax']=amount
        if 'confirmed_cost' in values:
            new['confirmed_cost']=money(values['confirmed_cost'])
            if any(r[0]==rid for r in wb['分摊明细'].iter_rows(min_row=2,values_only=True)):raise ValueError('已有分摊，请先清空分摊后再修改确认成本')
        new.update(updated_by=actor,updated_at=now(),revision=old['revision']+1)
        for col,k in enumerate(FIELDS,1):
            val=new[k];cell=wb['财务主表'].cell(i,col,float(val) if isinstance(val,Decimal) else val)
            if val is None:cell.value=None
            elif isinstance(val,str):cell.data_type='s'
        audit(wb,rid,'更新',actor,reason,old,new)

def update_lines(path,rid,categories,actor,reason,expected_revision):
    with transaction(path) as wb:
        i,old=row_at(wb,rid)
        if old['revision']!=expected_revision:raise ValueError('记录已变化，请刷新')
        rows=[r for r in wb['单据明细'].iter_rows(min_row=2) if r[0].value==rid]
        if set(categories)!={r[1].value for r in rows}:raise ValueError('请提交该票全部明细类别')
        if any(x not in CATEGORIES or x=='混合费用' for x in categories.values()):raise ValueError('类别无效')
        before={r[1].value:r[3].value for r in rows}
        for r in rows:r[3].value=categories[r[1].value]
        cats=set(categories.values());ws=wb['财务主表'];ws.cell(i,10,next(iter(cats)) if len(cats)==1 else '混合费用')
        for key,val in [('updated_by',actor),('updated_at',now()),('revision',old['revision']+1)]:
            c=ws.cell(i,FIELDS.index(key)+1,val)
            if isinstance(val,str):c.data_type='s'
        audit(wb,rid,'明细改类',actor,reason,before,categories)

def allocate(path,rid,rows,actor,reason,expected_revision):
    with transaction(path) as wb:
        i,old=row_at(wb,rid)
        if old['revision']!=expected_revision:raise ValueError('记录已变化，请刷新')
        ids={r[1] for r in wb['单据明细'].iter_rows(min_row=2,values_only=True) if r[0]==rid}
        parsed=[];keys=set()
        for r in rows:
            lid=text(r.get('line_id'));trip=text(r.get('trip_id'));amount=money(r.get('amount'))
            if lid not in ids or not trip or amount is None:raise ValueError('分摊需要有效明细ID、趟次和金额')
            if (lid,trip) in keys:raise ValueError('同明细同趟次不得重复')
            keys.add((lid,trip));parsed.append([rid,lid,trip,amount,actor,now(),reason])
        cost=money(old['confirmed_cost'])
        if parsed and (cost is None or sum(r[3] for r in parsed)!=cost):raise ValueError('分摊总额必须等于人工确认成本')
        if parsed and any((cost>=0 and r[3]<0) or (cost<0 and r[3]>0) for r in parsed):raise ValueError('分摊金额正负号与确认成本不一致')
        ws=wb['分摊明细'];before=[]
        for n in range(ws.max_row,1,-1):
            if ws.cell(n,1).value==rid:before.append([c.value for c in ws[n]]);ws.delete_rows(n)
        for r in parsed:literal(ws,r)
        wb['财务主表'].cell(i,FIELDS.index('revision')+1,old['revision']+1)
        c=wb['财务主表'].cell(i,FIELDS.index('updated_by')+1,actor);c.data_type='s'
        wb['财务主表'].cell(i,FIELDS.index('updated_at')+1,now())
        audit(wb,rid,'分摊回填',actor,reason,before,parsed)

def summary(path,month=None,basis='invoice_month'):
    if basis not in {'invoice_month','expense_period'}:raise ValueError('期间口径无效')
    wb=load(path);allrows=records(wb)
    def month_of(r):
        v=r[basis];return v.strftime('%Y-%m') if isinstance(v,datetime) else text(v)
    rs=[r for r in allrows if not month or month_of(r)==month];ids={r['record_id'] for r in rs}
    def total(key,subset=rs):return str(sum((number(r.get(key)) or Decimal(0) for r in subset),Decimal(0)))
    cats={}
    for line in wb['单据明细'].iter_rows(min_row=2,values_only=True):
        if line[0] in ids:cats[line[3]]=cats.get(line[3],Decimal(0))+(number(line[6]) or Decimal(0))
    return dict(registration_events=sum(r[1]=='登记' for r in wb['登记操作记录'].iter_rows(min_row=2,values_only=True)),duplicate_events=sum(r[1]=='重复跳过' for r in wb['登记操作记录'].iter_rows(min_row=2,values_only=True)),rejected_exception_count=sum(r[5]=='人工不予登记' for r in wb['异常待处理'].iter_rows(min_row=2,values_only=True)),invoice_count=len(rs),amount_excluding_tax=total('amount_excluding_tax'),tax_amount=total('tax_amount'),invoice_amount=total('invoice_amount'),pending_verification=sum(r['verification_status'] not in {'一致','偏差可解释'} for r in rs),pending_classification=sum(r['expense_category']=='待分类' or any(x[0]==r['record_id'] and x[3]=='待分类' for x in wb['单据明细'].iter_rows(min_row=2,values_only=True)) for r in rs),pending_deduction_face_tax=total('tax_amount',[r for r in rs if r['deduction_status']=='待确认']),confirmed_deductible_tax=total('deductible_input_tax',[r for r in rs if r['deduction_status']!='待确认']),missing_tax_count=sum(r['tax_amount'] is None for r in rs),unassigned_period_count=sum(not r['expense_period'] for r in allrows),category_totals={k:str(v) for k,v in cats.items()},open_exceptions=sum(r[5]=='待处理' for r in wb['异常待处理'].iter_rows(min_row=2,values_only=True)))

def migrate(source,target,mode,actor):
    if Path(target).exists():raise ValueError('迁移目标已存在；请选择新文件，禁止覆盖')
    if mode not in {'real','simulated'} or not text(actor):raise ValueError('请填写数据性质与迁移操作人')
    source_bytes=Path(source).read_bytes() if not isinstance(source,bytes) else source
    oldwb=load_workbook(io.BytesIO(source_bytes));ws=oldwb['财务主表'] if '财务主表' in oldwb else oldwb.active
    headers=[c.value for c in ws[1]]
    if len(headers) not in {28,33}:raise ValueError('只支持28或33列旧表；请确认主表位置')
    expected=V2_HEADERS[:len(headers)]
    if headers[3]=='归属月份':expected[3]='归属月份'
    if headers[15]=='税额（元）':expected[15]='税额（元）'
    if headers!=expected:raise ValueError('旧表字段不匹配，停止迁移并保留原文件；请核对表头')
    count=0;seen=set();invoice_keys=set()
    with transaction(target) as wb:
        # Keep the entire original workbook as a sibling backup, including formatting.
        for source_row,vals in enumerate(ws.iter_rows(min_row=2,values_only=True),2):
            if not any(x is not None for x in vals):continue
            if any(isinstance(x,str) and x.startswith('=') for x in vals):raise ValueError('旧表含公式，需人工核对后转换为值')
            d=dict(zip(V2_FIELDS,vals));rid=text(d.get('record_id')) or 'INV-'+uuid.uuid4().hex
            if rid in seen:raise ValueError('旧表记录ID重复')
            seen.add(rid)
            invoice_key=(text(d.get('invoice_code')),text(d.get('invoice_no')))
            if not invoice_key[1] or invoice_key in invoice_keys:raise ValueError('旧表发票号码缺失或同票重复，需先核对')
            invoice_keys.add(invoice_key)
            if d.get('source_mode') not in {None,'','真实' if mode=='real' else '模拟'}:raise ValueError('旧表数据性质与选择不一致')
            d['source_mode']='真实' if mode=='real' else '模拟'
            invoice_date=d.get('invoice_date')
            if isinstance(invoice_date,str):invoice_date=datetime.strptime(invoice_date,'%Y-%m-%d')
            if not isinstance(invoice_date,datetime):raise ValueError('旧表开票日期缺失或无效')
            oldperiod=d.get('invoice_month')
            expense_period=(oldperiod.strftime('%Y-%m') if isinstance(oldperiod,datetime) else period(oldperiod)) if headers[3]=='归属月份' and oldperiod else None
            d.update(record_id=rid,invoice_date=invoice_date,invoice_month=invoice_date.replace(day=1),expense_period=expense_period,period_status='已确认' if expense_period else '待确认',created_by='历史记录：录入人未知',created_at=None,updated_by=actor,updated_at=now(),revision=1)
            d['posting_allowed']={'是':True,'否':False}.get(d.get('posting_allowed'),d.get('posting_allowed') or '待确认')
            d['deduction_status']=d.get('deduction_status') or '待确认'
            d['verification_status']=d.get('verification_status') or '待核验'
            d['expense_category']=d.get('expense_category') or '待分类'
            literal(wb['财务主表'],[d.get(k) for k in FIELDS])
            for col in range(1,len(headers)+1):
                oldcell=ws.cell(source_row,col);newcell=wb['财务主表'].cell(count+2,col)
                for attr in ('font','fill','border','alignment','protection'):
                    setattr(newcell,attr,copy(getattr(oldcell,attr)))
                newcell.number_format=oldcell.number_format
            for col,dim in ws.column_dimensions.items():
                wb['财务主表'].column_dimensions[col].width=dim.width
                wb['财务主表'].column_dimensions[col].hidden=dim.hidden
            literal(wb['单据明细'],[rid,rid+'-1',d.get('item_name'),d['expense_category'],d.get('amount_excluding_tax'),d.get('tax_amount'),d.get('invoice_amount'),d.get('tax_rate')])
            audit(wb,rid,'旧表迁移',actor,'保留历史值；原录入身份和时间未知',dict(zip(headers,vals)),d);count+=1
    backup=Path(target).with_suffix('.source.xlsx');backup.write_bytes(source_bytes)
    return {'migrated':count,'source_backup':str(backup),'target':str(target)}

def close_exception(path,qid,actor,reason):
    with transaction(path) as wb:
        ws=wb['异常待处理']
        for i,row in enumerate(ws.iter_rows(min_row=2,values_only=True),2):
            if row[0]==qid:
                if row[5]!='待处理':raise ValueError('异常单已处理')
                ws.cell(i,6,'人工不予登记')
                audit(wb,qid,'异常不登记',actor,reason,{'状态':'待处理'},{'状态':'人工不予登记'})
                return
        raise ValueError('异常单不存在')

def export_bundle(path):
    wb=load(path)
    return {'schema_version':'finance_master_v3','records':records(wb),
            'lines':[dict(zip(['record_id','line_id','item_name','expense_category','amount_excluding_tax','tax_amount','invoice_amount','tax_rate'],r)) for r in wb['单据明细'].iter_rows(min_row=2,values_only=True)],
            'allocations':[dict(zip(['record_id','line_id','trip_id','amount','actor','at','reason'],r)) for r in wb['分摊明细'].iter_rows(min_row=2,values_only=True)]}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['export','summary','migrate','update']);p.add_argument('workbook');p.add_argument('--source');p.add_argument('--mode',choices=['real','simulated'],default='real');p.add_argument('--actor');p.add_argument('--payload');p.add_argument('--month');p.add_argument('--basis',default='invoice_month');a=p.parse_args()
    if a.action=='export':result=export_bundle(a.workbook)
    elif a.action=='summary':result=summary(a.workbook,a.month,a.basis)
    elif a.action=='migrate':result=migrate(a.source,a.workbook,a.mode,a.actor)
    else:
        q=json.loads(Path(a.payload).read_text());update(a.workbook,q['record_id'],q['values'],a.actor,q['reason'],q['expected_revision']);result={'updated':1}
    print(json_dump(result))
if __name__=='__main__':main()
