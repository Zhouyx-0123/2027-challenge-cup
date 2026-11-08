import io
import pandas as pd
import pytest
from openpyxl import load_workbook
from src.transport_evidence import analyze, register, read_store, export_excel, supplement


def invoice(no='001', date='2026-01-01', **extra):
    return dict(record_id=no, invoice_no=no, invoice_date=date, seller_name='站', item_name='柴油',
                quantity=100, unit='L', unit_price=6, invoice_amount=600, plate='京A1',
                source_image='a.png', source_mode='simulated', **extra)


def trips():
    return pd.DataFrame([dict(trip_id='t1',waybill_no='w1',plate='京A1',depart_ts='2026-01-01 08:00',
                             arrive_ts='2026-01-01 10:00',trip_km=100,od_site='A|B'),
                         dict(trip_id='t2',waybill_no='w2',plate='京A1',depart_ts='2026-01-02 08:00',
                             arrive_ts='2026-01-02 10:00',trip_km=300,od_site='A|B')])


def test_cycles_conserve_and_dedupe_physical_distance():
    data=pd.concat([trips(),trips().iloc[:1]])
    r=analyze([invoice(),invoice('002','2026-01-03')],data,source_mode='simulated',complete_history=True)
    assert sum(x['allocated_liters'] for x in r['allocations']) == pytest.approx(100)
    assert r['coverage']['total_km'] == 400
    assert r['coverage']['allocation_rate'] == 1
    assert r['allocations'][0]['allocated_cost'] is None


def test_three_states_and_missing():
    data=trips();data.loc[1,['depart_ts','arrive_ts']]=['2026-01-01 12:00','2026-01-01 14:00']
    records=[invoice(),invoice('002','2026-01-05'),invoice('003','2026-01-06')]
    records[2]['plate']=''
    r=analyze(records,data,source_mode='simulated')
    assert [x['status'] for x in r['matches']] == ['ambiguous','unmatched',None]
    r=analyze([invoice()],trips(),source_mode='simulated')
    assert r['matches'][0]['status']=='matched'
    assert not r['allocations']


def test_conflict_blocks_both_and_mode():
    r=analyze([invoice(),invoice()],trips(),source_mode='simulated',complete_history=True)
    assert all(m['status'] is None for m in r['matches'])
    with pytest.raises(ValueError):
        analyze([invoice()],trips(),source_mode='real')


def test_registration_preserves_incomplete_multiline_and_audit(tmp_path):
    raw={'mock':True,'invoice_no':'0001','items':[{'name':'柴油'},{'name':'服务'}]}
    assert register(raw,b'image','a.png',tmp_path)['added']==1
    assert register(raw,b'image','a.png',tmp_path)['duplicates']==1
    store=read_store('simulated',tmp_path)
    rid=store['records'][0]['record_id']
    supplement('simulated',rid,{'plate':'京A1'},tmp_path)
    assert read_store('simulated',tmp_path)['records'][0]['plate']=='京A1'
    wb=load_workbook(io.BytesIO(export_excel(store['records'])))
    assert wb['发票证据表'].max_column==15
    assert wb['票据明细'].max_row==3
    assert wb['发票证据表'].cell(2,2).value=='0001'


def test_conflicting_trip_excluded_and_cost_factor():
    data=trips();other=data.iloc[:1].copy();other['trip_km']=200
    r=analyze([invoice()],pd.concat([data,other]),source_mode='simulated')
    assert r['coverage']['total_km']==300
    r=analyze([invoice(),invoice('002','2026-01-03')],trips(),source_mode='simulated',complete_history=True,
              references={'cost_basis':'invoice_gross','theory_l_per_100km':{'京A1':{'value':20,'version':'v1','source':'test'}}})
    assert sum(a['allocated_cost'] for a in r['allocations'])==pytest.approx(600)
    assert r['allocations'][0]['deviation']==pytest.approx(.25)


def test_v2_conflicts_one_master_and_multiline_display(tmp_path):
    raw=dict(invoice(),mock=True)
    register(raw,b'image','a.png',tmp_path)
    changed=dict(raw,quantity=200)
    register(changed,b'image2','b.png',tmp_path)
    store=read_store('simulated',tmp_path)
    assert len(store['records'])==1
    assert len(store['records'][0]['versions'])==1
    assert register(changed,b'image2','b.png',tmp_path)['duplicates']==1
    result=analyze(store['records'],trips(),source_mode='simulated')
    assert result['matches'][0]['status'] is None
    record=dict(invoice(),items=[{'quantity':2},{'quantity':3}])
    wb=load_workbook(io.BytesIO(export_excel([record])))
    assert wb.worksheets[0].cell(2,7).value is None
    assert wb.worksheets[0].cell(2,9).value is None


def test_v2_checks_and_route_coverage():
    refs={'cost_basis':'invoice_gross','capacities':{'京A1':500},
          'theory_l_per_100km':{'京A1':{'value':20,'version':'v1','source':'fixture'}},
          'price_checks':{'001':{'expected':6,'relative_threshold':.2,'basis_confirmed':True,'source':'fixture'}},
          'time_checks':{'001':{'replenished_at':'2026-01-01 20:00','coverage_start':'2026-01-01','coverage_end':'2026-01-03','history_complete':True,'source':'fixture'}},
          'location_checks':{'001':{'replenished_at':'2026-01-01 20:00','lat':40,'lon':116,'source':'fixture','max_distance_km':5,'max_time_delta_minutes':5,
                                   'observations':[{'plate':'京A1','observed_at':'2026-01-01 20:01','lat':41,'lon':117,'source':'GPS'}]}}}
    r=analyze([invoice(),invoice('002','2026-01-03')],trips(),source_mode='simulated',complete_history=True,references=refs)
    checks={c['rule']:c for c in r['checks'] if c['record_id']=='001'}
    assert checks['time_contradiction']['status']=='anomaly'
    assert checks['region_contradiction']['status']=='anomaly'
    assert checks['price_outlier']['status']=='checked'
    assert checks['theory_comparison']['status']=='checked'
    assert r['routes'][0]['coverage_rate']==1
    assert r['routes'][0]['median_yuan_per_km']==pytest.approx(1.5)
    assert not any(g.get('reason')=='missing_price_reference' for g in r['gaps'])


def test_v2_persistent_batches_stale_after_edit(tmp_path):
    from src.transport_evidence import save_run,list_runs
    register(dict(invoice(),mock=True),b'image','a.png',tmp_path)
    run=save_run('simulated',trips(),tmp_path)
    assert list_runs('simulated',tmp_path)[0]['run']['stale'] is False
    record=read_store('simulated',tmp_path)['records'][0]
    supplement('simulated',record['record_id'],{'note':'updated'},tmp_path)
    assert list_runs('simulated',tmp_path)[0]['run']['stale'] is True
    assert list_runs('simulated',tmp_path)[0]['run']['batch_id']==run['run']['batch_id']


def test_v2_page_saved_batch_and_edit_form(monkeypatch,tmp_path):
    import sys
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from src.transport_evidence import save_run
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
    monkeypatch.setenv('FINANCE_DATA_DIR',str(tmp_path))
    register(dict(invoice(),mock=True),b'image','a.png',tmp_path)
    save_run('simulated',trips(),tmp_path)
    def app():
        from main_views import _render_transport_records
        _render_transport_records()
    at=AppTest.from_function(app,default_timeout=30).run()
    assert not at.exception
    assert any(s.label=='查看分析批次' for s in at.selectbox)
    rid=read_store('simulated',tmp_path)['records'][0]['record_id']
    at.text_input(key=rid+'_plate').set_value('京A2')
    next(b for b in at.button if b.label=='保存补充信息').click().run()
    assert not at.exception
    assert read_store('simulated',tmp_path)['records'][0]['plate']=='京A2'
    assert any('过期' in w.value or '已更新' in w.value for w in at.warning)
