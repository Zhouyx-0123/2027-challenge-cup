"""Full local integration with original OCR kernel/CLI, Excel, UI and callbacks.

The model boundary is mocked. No external requests or paid OCR calls.
"""
import json
import sys
from pathlib import Path
import pytest
from openpyxl import load_workbook
from src import invoice
from src import finance_workflow as workflow
from tools import invoice_ocr
from tools.invoice_to_master import read_records, write_master
from tools.finance_master_interface import export_master,apply_updates,SCHEMA

FIXTURE={'invoice_no':'DEMO-E2E-001','invoice_code':'','invoice_date':'2026-10-31',
         'seller_name':'模拟维修服务商','item_name':'*劳务*车辆维修费','quantity':1,
         'invoice_type':'电子发票（普通发票）','tax_rate':'13%',
         'invoice_amount':1334,'amount_excluding_tax':1180.53,'tax_amount':153.47}
IMAGE=b'\xff\xd8\xff\xe0'+b'0'*32

def fake_ocr(monkeypatch):
    monkeypatch.setattr(invoice,'MOCK_MODE',True)
    monkeypatch.setattr(invoice,'MOCK_RESULT',dict(FIXTURE))
    # Empty key exercises the kernel's existing explicit mock response path.
    return invoice.extract_invoice_fields(IMAGE,'','repair.jpg')

def test_model_boundary_to_csv_master_export_callbacks(monkeypatch,tmp_path):
    parsed=fake_ocr(monkeypatch)
    assert parsed['mock'] is True and parsed['tax_rate']=='13%'
    monkeypatch.setattr(invoice_ocr,'load_api_key',lambda:'test-only')
    monkeypatch.setattr(invoice_ocr.kernel,'extract_invoice_fields',lambda *a,**kw:parsed)
    images=tmp_path/'images';images.mkdir();(images/'repair.jpg').write_bytes(IMAGE)
    csv_path=tmp_path/'ocr.csv'
    invoice_ocr.run(images,csv_path,tmp_path/'ocr_report.json')
    records=read_records(csv_path)
    assert records[0]['tax_rate']=='13%' and records[0]['mock']=='True'
    master=tmp_path/'register.xlsx'
    assert write_master(master,records,'simulated')['added']==1
    assert write_master(master,records,'simulated')['duplicates']==1
    row=export_master(master,'simulated')['records'][0]
    assert row['tax_amount']=='153.47' and row['tax_rate']=='0.13'
    assert row['expense_category']=='维修' and row['fuel_liters'] is None
    for kind,values in [
        ('association',{'activity_id':'ACT-DEMO','waybill_no':'WB-DEMO','trip_id':'TRIP-DEMO'}),
        ('verification',{'verification_status':'一致','posting_allowed':True,'reviewer':'模拟审核人','reviewed_at':'2026-10-31T15:00:00+08:00'}),
        ('deduction',{'deduction_status':'不可抵扣','deductible_input_tax':'0.00','deduction_basis':'模拟回填；仅验证接口，不作税务判断'}),
        ('cost',{'confirmed_cost':'1334.00'})]:
        payload=dict(schema_version=SCHEMA,source_mode='simulated',kind=kind,source='mock_downstream',reviewer='模拟审核人',reviewed_at='2026-10-31T15:00:00+08:00',reason='模拟接口测试',updates=[{'record_id':row['record_id'],'expected':{f:row[f] for f in values},'values':values}])
        assert apply_updates(master,payload)['updated']==1
        row=export_master(master,'simulated')['records'][0]
    assert row['confirmed_cost']=='1334' and row['posting_allowed'] is True
    assert load_workbook(master)['接口回填记录'].max_row==5

def test_workflow_evidence_and_clean_template(monkeypatch,tmp_path):
    raw=fake_ocr(monkeypatch);report=workflow.register_invoice(raw,IMAGE,'repair.jpg',directory=tmp_path)
    assert report['added']==1
    rows=export_master(Path(report['master_path']),'simulated')['records']
    assert len(rows)==1 and rows[0]['invoice_no']==FIXTURE['invoice_no']
    assert Path(rows[0]['evidence_ref']).read_bytes()==IMAGE
    assert workflow.register_invoice(raw,IMAGE,'repair.jpg',directory=tmp_path)['duplicates']==1
    assert rows[0]['fuel_liters'] is None

def test_page_save_button_and_download(monkeypatch,tmp_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
    import main_views
    from streamlit.testing.v1 import AppTest
    raw=fake_ocr(monkeypatch)
    monkeypatch.setenv('FINANCE_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(main_views.live_services,'extract_invoice',lambda *a:dict(raw))
    class Upload:
        name='repair.jpg'
        def getvalue(self):return IMAGE
    def app(upload):
        import streamlit as st
        from demo_state import init_state
        from main_views import _run_invoice,_render_finance_register
        init_state()
        if not st.session_state.get('finance_invoice_raw'):_run_invoice(upload())
        _render_finance_register()
    at=AppTest.from_function(app,args=(Upload,),default_timeout=30).run()
    assert not at.exception
    assert at.session_state['current_invoice_carbon']==0
    assert at.session_state['current_invoice_fuel'] is None
    at.text_input(key='finance_actor').set_value('模拟录入员')
    at.selectbox(key='finance_mode').select('模拟').run()
    at.button(key='finance_register_save').click().run()
    assert not at.exception and len(at.success)==1
    from tools.finance_register_v3 import list_records
    path=tmp_path/'finance_master_v3_simulated.xlsx'
    assert len(list_records(path))==1
    at.button(key='finance_register_save').click().run()
    assert not at.exception and len(at.info)==1
    assert len(list_records(path))==1

def test_source_flag_required(tmp_path):
    with pytest.raises(ValueError,match='来源标记'):workflow.register_invoice(FIXTURE,IMAGE,'repair.jpg',directory=tmp_path)
