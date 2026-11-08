import json
import pandas as pd
import pytest
from src.transport_evidence import analyze, replay_run, atomic_json


def inv(no, day, plate='京A1'):
    return dict(record_id=no, invoice_no=no, invoice_date=day, seller_name='站', item_name='柴油',
                quantity=100, unit='L', unit_price=6, invoice_amount=600, plate=plate,
                source_image='x.png', source_mode='simulated')


def trip(tid, day, plate='京A1', vehicle_type='牵引车'):
    return dict(trip_id=tid, waybill_no='W'+tid, plate=plate, depart_ts=day+' 08:00',
                arrive_ts=day+' 10:00', trip_km=100, od_site='A-B', vehicle_type=vehicle_type)


def test_shared_scope_applies_to_coverage_and_routes():
    data=pd.DataFrame([trip('1','2026-01-01'), trip('2','2026-01-02'), trip('3','2026-01-02','京A2')])
    result=analyze([inv('a','2025-12-30'), inv('b','2026-01-02'), inv('c','2026-01-03','京A2')],
                   data, source_mode='simulated', period_start='2026-01-02',
                   period_end='2026-01-02', vehicles=['京A1'])
    assert result['coverage']['total_km'] == 100
    assert result['run']['scope']['vehicles'] == ['京A1']
    assert result['run']['scope']['period_start'] == '2026-01-02'
    assert result['run']['scope']['trip_count'] == 1


def test_team_factor_table_unique_ambiguous_and_unmatched():
    records=[inv('a','2026-01-01'), inv('b','2026-01-03')]
    data=pd.DataFrame([trip('1','2026-01-01'),trip('2','2026-01-02')])
    base_factor={'vehicle_type':'牵引车','energy_type':'fuel','value':20,'version':'v1','source':'team'}
    refs={'team_theory_factors':[dict(base_factor,factor_id='F1',valid_from='2026-01-01',valid_to='2026-12-31')]}
    result=analyze(records,data,source_mode='simulated',complete_history=True,references=refs)
    assert result['allocations'][0]['theory_factor_selection']['status']=='matched'
    refs['team_theory_factors'].append(dict(base_factor,factor_id='F2'))
    result=analyze(records,data,source_mode='simulated',complete_history=True,references=refs)
    assert result['allocations'][0]['theory_factor_selection']['status']=='ambiguous'
    refs['team_theory_factors']=[dict(base_factor,factor_id='F3',vehicle_type='厢式货车')]
    result=analyze(records,data,source_mode='simulated',complete_history=True,references=refs)
    assert result['allocations'][0]['theory_factor_selection']['status']=='unmatched'


def test_replay_uses_embedded_inputs_after_original_inputs_change(tmp_path):
    records=[inv('a','2026-01-01'),inv('b','2026-01-03')]
    trips=pd.DataFrame([trip('1','2026-01-01'),trip('2','2026-01-02')])
    result=analyze(records,trips,source_mode='simulated',complete_history=True)
    result['run'].update(input_snapshot={'records':records,'trips':trips.to_dict(orient='records'),
        'trip_columns':list(trips.columns),'invoice_images':{}},references_snapshot={})
    batch=result['run']['batch_id']
    atomic_json(tmp_path/'runs'/'simulated'/(batch+'.json'),result)
    # No external source files are required for replay.
    replay=replay_run(batch,'simulated',tmp_path)
    assert replay['run']['replay_of']==batch
    assert replay['coverage']['total_km']==result['coverage']['total_km']


def test_period_anchor_skips_repair_invoice_and_keeps_last_energy_ticket():
    fuel_before = inv('fuel-before', '2026-10-29')
    repair = inv('repair', '2026-10-30')
    repair.update(item_name='车辆维修费', quantity=1, unit='次', unit_price=100, invoice_amount=113)
    fuel_inside = inv('fuel-inside', '2026-11-02')
    data = pd.DataFrame([trip('period-trip', '2026-11-01')])

    result = analyze(
        [fuel_before, repair, fuel_inside], data, source_mode='simulated',
        complete_history=True, period_start='2026-11-01', period_end='2026-11-03',
    )
    assert result['run']['scope']['invoice_count'] == 2
    assert {m['record_id'] for m in result['matches']} == {'fuel-before', 'fuel-inside'}
    assert len(result['allocations']) == 1
    assert result['allocations'][0]['record_id'] == 'fuel-before'
