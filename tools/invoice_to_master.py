"""OCR JSON/CSV -> approved 33-column Excel register. No OCR or posting decisions."""
from __future__ import annotations
import argparse, csv, json, os, re, tempfile, uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

HEADERS = ['记录ID','活动记录ID','数据性质','开票月份','开票日期','发票代码','发票号码','销售方名称','商品或服务名称','费用类别','数量','单位','单价（元/单位）','柴油量（L）','不含税金额（元）','发票税额（元）','价税合计（元）','确认成本（元）','车牌号','运单号','趟或段ID','核算批次ID','双验判定','是否允许入账','审核人','审核时间','证据索引','备注','发票类型','税率/征收率','抵扣状态','可抵扣进项税额（元）','抵扣判断依据']
MAP = {'invoice_date':4,'invoice_code':5,'invoice_no':6,'seller_name':7,'item_name':8,'quantity':10,'unit':11,'unit_price':12,'fuel_liters':13,'amount_excluding_tax':14,'tax_amount':15,'invoice_amount':16,'plate':18,'invoice_type':28,'tax_rate':29}
NUMBERS = {'quantity','unit_price','fuel_liters','amount_excluding_tax','tax_amount','invoice_amount'}
def text(v):
    return '' if v is None else str(v).strip()
def number(v):
    if v is None or text(v)=='': return None
    if isinstance(v,bool): raise ValueError('布尔值不是金额或数量')
    try: n=Decimal(text(v).replace(',','').replace('¥','').replace('￥',''))
    except InvalidOperation: raise ValueError('数字格式无效')
    if not n.is_finite() or abs(n)>Decimal('999999999999.99'): raise ValueError('数字必须有限')
    return n
def rate(v):
    if v is None or text(v)=='': return None
    if text(v) in {'免税','不征税','***'}: return text(v)
    n=number(text(v)[:-1])/100 if text(v).endswith('%') else number(v)
    if not 0<=n<=1: raise ValueError('税率须为0到1的小数或带百分号文本；13%填0.13')
    return n
def normalize(raw):
    if not isinstance(raw,dict): raise ValueError('每条输入须为字典')
    if raw.get('multiple_items') is True or len(raw.get('items') or [])>1:
        raise ValueError('多项目发票需先拆分并核对，不自动合并')
    if text(raw.get('status')).lower() in {'failed','offline','error'}:
        raise ValueError('OCR未成功，不能导入')
    if raw.get('error'): raise ValueError('OCR返回错误')
    out={k:(rate(raw.get(k)) if k=='tax_rate' else number(raw.get(k)) if k in NUMBERS else text(raw.get(k))) for k in MAP}
    if not out['invoice_no']: raise ValueError('缺少发票号码，暂不导入')
    try: datetime.strptime(out['invoice_date'],'%Y-%m-%d')
    except ValueError: raise ValueError('开票日期缺失或无效，需YYYY-MM-DD')
    if out['invoice_amount'] is None: raise ValueError('缺少价税合计')
    if not out['item_name']: raise ValueError('缺少商品名称，需复核')
    if any(out[k] is not None and out[k]<0 for k in NUMBERS):
        raise ValueError('负数或红字单据需单独处理，本版不自动导入')
    a,b,c=[out[k] for k in ('amount_excluding_tax','tax_amount','invoice_amount')]
    if a is not None and b is not None and abs(a+b-c)>Decimal('0.01'):
        raise ValueError('不含税金额+税额与价税合计不符')
    return out
def category(name):
    groups=[('燃料',('柴油','汽油','天然气')),('电力',('电费','购电','电力','充电费')),('路桥',('通行费','过路费','过桥费','路桥费')),('维修',('维修','修理','保养')),('碳配额购买',('碳排放配额','碳配额'))]
    hits=[label for label,words in groups if any(w in name for w in words)]
    return hits[0] if len(hits)==1 else '待分类'
def key(r): return r['invoice_code'],r['invoice_no']
def fingerprint(r): return tuple(r[k] for k in MAP)
def map_record(r,mode,evidence,note=''):
    row=[None]*33
    row[0]='INV-'+uuid.uuid4().hex
    row[2]='模拟' if mode=='simulated' else '真实'
    dt=datetime.strptime(r['invoice_date'],'%Y-%m-%d')
    row[3]=dt.replace(day=1); row[4]=dt
    for k,i in MAP.items():
        if k!='invoice_date': row[i]=r[k] if r[k]!='' else None
    row[9]=category(r['item_name']);row[22]='待核验';row[23]='待确认'
    row[26]=evidence;row[27]=note or None;row[30]='待确认'
    return row
def prepare(records,mode,source,existing=None):
    """Reject conflicting groups entirely, skip exact duplicates; no invented values."""
    seen=dict(existing or {}); groups={}; report={'added':0,'duplicates':0,'rejected':[]}; rows=[]
    for i,raw in enumerate(records,1):
        try:
            if not isinstance(raw,dict): raise ValueError('每条输入须为字典')
            if mode not in {'real','simulated'}: raise ValueError('source_mode无效')
            if mode=='real' and (text(raw.get('mock')).lower() in {'true','1','yes'} or text(raw.get('source_mode')).lower()=='simulated'):
                raise ValueError('模拟数据不能标记为真实')
            r=normalize(raw); groups.setdefault(key(r),[]).append((i,r,raw))
        except (ValueError,TypeError) as e: report['rejected'].append({'input_row':i,'reason':str(e)})
    for k,items in groups.items():
        sigs={fingerprint(r) for _,r,_ in items}
        if len(sigs)>1 or (k in seen and seen[k] not in sigs):
            report['rejected'].extend({'input_row':i,'reason':'同票号数据冲突，整组待复核'} for i,_,_ in items);continue
        if k in seen: report['duplicates']+=len(items);continue
        i,r,raw=items[0]
        evidence=text(raw.get('source_image')) or f'{source}#record={i}'
        note=text(raw.get('note'))
        if category(r['item_name'])=='待分类': note=(note+'；类别不明确，待人工分类').strip('；')
        if category(r['item_name'])=='路桥': note=(note+'；路桥费待校准').strip('；')
        rows.append(map_record(r,mode,evidence,note))
        report['duplicates']+=len(items)-1;seen[k]=fingerprint(r)
    report['added']=len(rows)
    return rows,report
def read_records(path):
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
    with path.open(encoding='utf-8-sig') as f:obj=json.load(f,parse_float=Decimal)
    if isinstance(obj,list):return obj
    if isinstance(obj,dict) and isinstance(obj.get('records'),list):return obj['records']
    if isinstance(obj,dict) and 'invoice_no' in obj:return [obj]
    raise ValueError('输入应为OCR字典、字典列表或records列表；不是invoice_report.json')
def write_master(path,rows,source_mode, *, dry_run=False):
    from openpyxl import Workbook,load_workbook
    from openpyxl.styles import Font,PatternFill,Border
    from openpyxl.worksheet.table import TableStyleInfo
    if path.exists():
        wb=load_workbook(path)
        if '财务主表' not in wb.sheetnames:raise ValueError('缺少财务主表工作表')
        ws=wb['财务主表']
        headers=[ws.cell(1,i).value for i in range(1,34)]
        expected=HEADERS.copy()
        if headers[3]=='归属月份': headers[3]='开票月份'
        if headers!=expected or ws.max_column>33:raise ValueError('表头与33列模板不一致，停止写入；旧版须先迁移')
    else:
        wb=Workbook();ws=wb.active;ws.title='财务主表';ws.append(HEADERS)
    ws.cell(1,4,'开票月份')
    existing={}
    record_ids=set()
    for vals in ws.iter_rows(min_row=2,values_only=True):
        if not any(v is not None for v in vals):continue
        if not vals[0] or vals[0] in record_ids: raise ValueError('已有记录ID缺失或重复')
        record_ids.add(vals[0])
        if not vals[6]:raise ValueError('已有记录缺少票号，停止写入以避免重复')
        if vals[2] != ('模拟' if source_mode=='simulated' else '真实'):
            raise ValueError('真实与模拟记录请分别保存到不同工作簿')
        raw={k:vals[i] for k,i in MAP.items()}
        if isinstance(raw['invoice_date'],datetime):raw['invoice_date']=raw['invoice_date'].strftime('%Y-%m-%d')
        norm=normalize(raw);k=key(norm)
        if k in existing:raise ValueError('现有主表有重复票号，需先人工处理')
        existing[k]=fingerprint(norm)
    mapped,report=prepare(rows,source_mode,str(path),existing)
    if dry_run: return report
    last=max([1]+[i for i in range(2,ws.max_row+1) if any(ws.cell(i,c).value is not None for c in range(1,34))])
    for ri,row in enumerate(mapped,last+1):
        for ci,value in enumerate(row,1):
            cell=ws.cell(ri,ci)
            cell.value=float(value) if isinstance(value,Decimal) else value
            if isinstance(value,str):cell.data_type='s'  # literal text; never execute OCR as formulas
            cell.font=Font(name='Calibri',size=11);cell.fill=PatternFill();cell.border=Border()
            if ci in (1,2,6,7,19,20,21,22,27):cell.number_format='@'
            elif ci==4:cell.number_format='yyyy-mm'
            elif ci==5:cell.number_format='yyyy-mm-dd'
            elif ci in (13,15,16,17,18,32):cell.number_format='0.00'
            elif ci==30:cell.number_format='0.##%'
    for table in ws.tables.values():
        table.ref=f'A1:AG{max(ws.max_row,last+len(mapped))}'
        table.tableStyleInfo=TableStyleInfo(name=None,showRowStripes=False,showColumnStripes=False)
        if len(table.tableColumns)>=4:table.tableColumns[3].name='开票月份'
    from openpyxl.worksheet.datavalidation import DataValidation
    limit=max(101,ws.max_row)
    # Existing validation ranges extend to newly appended rows.
    for dv in ws.data_validations.dataValidation:
        for rg in list(dv.ranges.ranges):
            if rg.min_row==2 and rg.max_row>=100:
                rg.max_row=max(rg.max_row,limit)
    ws.freeze_panes='A2'
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(suffix='.xlsx',dir=path.parent);os.close(fd)
    try:wb.save(tmp);os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return report
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--source-mode',choices=['simulated','real'],required=True);p.add_argument('--dry-run',action='store_true');p.add_argument('--report',type=Path)
    a=p.parse_args();records=read_records(a.input)
    for i,r in enumerate(records,1):
        if not isinstance(r,dict):p.error('每条记录必须是字典')
        r.setdefault('source_image',str(a.input.resolve())+f'#record={i}')
    report=write_master(a.output,records,a.source_mode,dry_run=a.dry_run)
    if a.report:
        a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 2 if report['rejected'] else 0
if __name__=='__main__':raise SystemExit(main())
