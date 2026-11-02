import io
from pathlib import Path
import pytest
from openpyxl import Workbook,load_workbook
from tools import finance_register_v3 as f

BASE=dict(invoice_no='TEST-001',invoice_date='2026-10-31',item_name='充电服务费',seller_name='测试公司',amount_excluding_tax='100',tax_amount='13',invoice_amount='113',tax_rate='13%')
def add(path,**kw):return f.add(path,dict(BASE,**kw),'simulated','测试员',image=b'test',filename='test.png')

@pytest.mark.parametrize('name,cat',[('运输服务费','运输'),('仓储服务费','仓储'),('车辆保险费','保险'),('租赁费','租赁'),('年检费','年检'),('办公用品','办公'),('充电服务费','电力'),('车辆维修费','维修'),('柴油','燃料'),('通行费','路桥'),('运输及仓储服务','待分类')])
def test_categories(name,cat):assert f.classify(name)==cat

def test_edit_audit_summary_and_concurrency(tmp_path):
    p=tmp_path/'v3.xlsx';rid=add(p)['record_id'];r=f.list_records(p)[0]
    assert r['created_by']=='测试员' and r['created_at'] and r['expense_period'] is None
    assert add(p)['duplicates']==1
    f.update(p,rid,dict(expense_period='2026-09',expense_category='其他',plate='测试车牌'), '财务','人工核对',1)
    assert f.summary(p,'2026-09','expense_period')['invoice_count']==1
    assert f.summary(p,'2026-10','invoice_month')['invoice_amount']=='113'
    assert f.summary(p)['category_totals']=={'其他':'113'}
    with pytest.raises(ValueError,match='他人修改'):f.update(p,rid,{'note':'覆盖'},'人','旧版本',1)
    assert f.list_records(p)[0]['revision']==2
    assert load_workbook(p)['登记操作记录'].max_row==4

def test_multiline_queue_resolve_duplicate_and_red(tmp_path):
    p=tmp_path/'v3.xlsx';raw=dict(BASE,items=[dict(BASE,invoice_amount='56.5',amount_excluding_tax='50',tax_amount='6.5'),dict(BASE,item_name='车辆维修费',invoice_amount='56.5',amount_excluding_tax='50',tax_amount='6.5')])
    report=f.add(p,raw,'simulated','测试',image=b'raw',filename='raw.png');qid=report['rejected'][0]['queue_id']
    q=list(f.load(p)['异常待处理'].values)[1];assert Path(q[3]).read_bytes()==b'raw'
    assert f.summary(p)['invoice_count']==0
    rid=f.add(p,raw,'simulated','财务',manual=True,reason='逐条核对',queue_id=qid)['record_id']
    assert f.summary(p)['invoice_count']==1 and f.summary(p)['invoice_amount']=='113'
    assert f.summary(p)['category_totals']=={'电力':'56.5','维修':'56.5'}
    assert f.summary(p)['open_exceptions']==0
    assert f.add(p,raw,'simulated','财务',manual=True)['duplicates']==1
    red=dict(BASE,invoice_no='RED-1',invoice_amount='-113',amount_excluding_tax='-100',tax_amount='-13')
    with pytest.raises(ValueError,match='原发票'):f.add(p,red,'simulated','财务',manual=True)
    red['original_invoice_no']=BASE['invoice_no'];f.add(p,red,'simulated','财务',manual=True)
    assert f.summary(p)['invoice_count']==2 and f.summary(p)['invoice_amount']=='0'
    lines=[x for x in f.load(p)['单据明细'].iter_rows(min_row=2,values_only=True) if x[0]==rid]
    f.update_lines(p,rid,{x[1]:'运输' for x in lines},'财务','确认',1)
    assert f.list_records(p)[0]['expense_category']=='运输'

def test_review_deduction_allocation(tmp_path):
    p=tmp_path/'v3.xlsx';rid=add(p)['record_id']
    with pytest.raises(ValueError):f.update(p,rid,{'posting_allowed':True},'财务','核对',1)
    f.update(p,rid,{'verification_status':'一致','posting_allowed':True},'财务','核对',1)
    with pytest.raises(ValueError):f.update(p,rid,{'deduction_status':'可抵扣','deductible_input_tax':'14','deduction_basis':'测试'},'财务','核对',2)
    f.update(p,rid,{'deduction_status':'不可抵扣','deductible_input_tax':'0','deduction_basis':'人工测试决定','confirmed_cost':'113'},'财务','核对',2)
    lid=list(f.load(p)['单据明细'].values)[1][1]
    with pytest.raises(ValueError):f.allocate(p,rid,[dict(line_id=lid,trip_id='A',amount='100')],'财务','依据',3)
    f.allocate(p,rid,[dict(line_id=lid,trip_id='A',amount='50'),dict(line_id=lid,trip_id='B',amount='63')],'财务','人工分配',3)
    with pytest.raises(ValueError,match='已有分摊'):f.update(p,rid,{'confirmed_cost':'100'},'财务','改',4)
    assert f.summary(p)['confirmed_deductible_tax']=='0'

@pytest.mark.parametrize('count',[28,33])
def test_migrate_preserves_period_source_and_values(tmp_path,count):
    wb=Workbook();ws=wb.active;ws.title='财务主表';heads=f.V2_HEADERS[:count];heads[3]='归属月份';ws.append(heads)
    from datetime import datetime
    row=[None]*count;row[0]='OLD';row[2]='模拟';row[3]=datetime(2026,9,1);row[4]=datetime(2026,10,31);row[6]='OLDNO';row[8]='办公用品';row[9]='办公';row[14]=100;row[15]=13;row[16]=113
    ws.append(row);src=tmp_path/'old.xlsx';wb.save(src);before=src.read_bytes();dest=tmp_path/'v3.xlsx'
    assert f.migrate(src,dest,'simulated','迁移员')['migrated']==1
    r=f.list_records(dest)[0];assert r['expense_period']=='2026-09' and r['invoice_month'].month==10
    assert r['record_id']=='OLD' and r['created_at'] is None and r['invoice_amount']==113
    assert src.read_bytes()==before and dest.with_suffix('.source.xlsx').read_bytes()==before
    with pytest.raises(ValueError):f.migrate(src,dest,'simulated','迁移员')

def test_atomic_reject_and_literal_text(tmp_path):
    p=tmp_path/'v3.xlsx';rid=add(p)['record_id'];before=p.read_bytes()
    with pytest.raises(ValueError):f.update(p,rid,{'note':'x'},'','缺少身份',1)
    assert p.read_bytes()==before
    f.update(p,rid,{'note':'=HYPERLINK("bad")'},'测试','字面值',1)
    assert f.load(p)['财务主表'].cell(2,28).data_type=='s'
    assert add(p,invoice_amount='114')['rejected']

def test_v3_panel_without_ocr_and_manual_review(monkeypatch,tmp_path):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('FINANCE_DATA_DIR',str(tmp_path))
    p=tmp_path/'finance_master_v3_real.xlsx';f.add(p,BASE,'real','测试',image=b'original',filename='x.png')
    def app():
        from finance_panel import render
        render()
    at=AppTest.from_function(app,default_timeout=30).run()
    assert not at.exception
    at.text_input(key='finance_actor').set_value('审核员').run()
    at.text_input(key='basic_reason_'+f.list_records(p)[0]['record_id']).set_value('修正所属期')
    next(x for x in at.text_input if x.label.startswith('费用所属期')).set_value('2026-09')
    next(x for x in at.button if x.label=='保存期间与关联').click().run()
    assert not at.exception
    assert f.list_records(p)[0]['expense_period']=='2026-09'

def test_export_exception_close_and_tax_unknown(tmp_path):
    p=tmp_path/'v3.xlsx';raw=dict(BASE,invoice_no='BAD',invoice_amount='114')
    q=f.add(p,raw,'simulated','登记员',image=b'x',filename='x.png')['rejected'][0]['queue_id']
    f.close_exception(p,q,'审核员','票面无效，不登记')
    assert f.summary(p)['rejected_exception_count']==1
    with pytest.raises(ValueError):f.close_exception(p,q,'审核员','重复')
    add(p,tax_amount=None,amount_excluding_tax=None)
    result=f.summary(p);assert result['missing_tax_count']==1
    bundle=f.export_bundle(p);assert bundle['schema_version']=='finance_master_v3'
    assert bundle['lines'][0]['line_id'].startswith('INV-')
