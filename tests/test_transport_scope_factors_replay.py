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


def test_period_anchor_allocation_uses_full_cycle_distance():
    anchor = inv('anchor', '2026-01-01')
    nxt = inv('next', '2026-01-03')
    nxt.update(quantity=50, invoice_amount=300)
    data = pd.DataFrame([trip('pre', '2026-01-01'),
                         trip('in-period', '2026-01-02'),
                         trip('after', '2026-01-03')])

    result = analyze([anchor, nxt], data, source_mode='simulated',
                     complete_history=True, period_start='2026-01-02', period_end='2026-01-03')

    assert result['run']['scope']['trip_count'] == 2
    # Coverage stays period-only: the pre-period 100 km must not re-enter it.
    assert result['coverage']['total_km'] == 200
    assert len(result['allocations']) == 1
    allocation = result['allocations'][0]
    assert allocation['trip_id'] == 'in-period'
    # Full cycle is 200 km (1/1 + 1/2), so the in-period 100 km trip carries 50 L.
    assert allocation['share'] == pytest.approx(0.5)
    assert allocation['allocated_liters'] == pytest.approx(50)
    assert allocation['l_per_100km'] == pytest.approx(50)


def test_period_anchor_without_pre_period_trips_is_incomplete():
    anchor = inv('anchor', '2026-01-01')
    nxt = inv('next', '2026-01-03')
    nxt.update(quantity=50, invoice_amount=300)
    data = pd.DataFrame([trip('in-period', '2026-01-02'),
                         trip('after', '2026-01-03')])

    result = analyze([anchor, nxt], data, source_mode='simulated',
                     complete_history=True, period_start='2026-01-02', period_end='2026-01-03')

    assert result['allocations'] == []
    assert any(g.get('reason') == 'missing_pre_period_trips' for g in result['gaps'])
    assert result['coverage']['total_km'] == 200


def test_missing_vehicle_type_is_explicit_gap_with_vehicle_factor():
    records = [inv('a', '2026-01-01'), inv('b', '2026-01-03')]
    data = pd.DataFrame([trip('1', '2026-01-01'), trip('2', '2026-01-02')]).drop(columns=['vehicle_type'])
    refs = {'team_theory_factors': [{'factor_id': 'F1', 'vehicle_type': '牵引车', 'energy_type': 'fuel',
                                     'value': 20, 'version': 'v1', 'source': 'team'}]}

    result = analyze(records, data, source_mode='simulated', complete_history=True, references=refs)

    assert result['allocations']
    assert all(a['theory_factor_selection']['status'] == 'unmatched' for a in result['allocations'])
    assert all(a['theory_factor_selection'].get('reason') == 'missing_vehicle_type'
               for a in result['allocations'])
    assert any(g.get('reason') == 'missing_vehicle_type' and g.get('trip_id') == '1'
               for g in result['gaps'])


def test_plate_factor_stays_matched_without_vehicle_type():
    records = [inv('a', '2026-01-01'), inv('b', '2026-01-03')]
    data = pd.DataFrame([trip('1', '2026-01-01'), trip('2', '2026-01-02')]).drop(columns=['vehicle_type'])
    refs = {'theory_l_per_100km': {'京A1': {'value': 20, 'version': 'v1', 'source': 'team'}}}

    result = analyze(records, data, source_mode='simulated', complete_history=True, references=refs)

    assert result['allocations'][0]['theory_factor_selection']['status'] == 'matched'
    assert not any(g.get('reason') == 'missing_vehicle_type' for g in result['gaps'])


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
    data = pd.DataFrame([trip('anchor-pre', '2026-10-30'), trip('period-trip', '2026-11-01')])

    result = analyze(
        [fuel_before, repair, fuel_inside], data, source_mode='simulated',
        complete_history=True, period_start='2026-11-01', period_end='2026-11-03',
    )
    assert result['run']['scope']['invoice_count'] == 2
    assert {m['record_id'] for m in result['matches']} == {'fuel-before', 'fuel-inside'}
    assert len(result['allocations']) == 1
    assert result['allocations'][0]['record_id'] == 'fuel-before'
    # The anchor cycle spans 10-29..11-02 (two 100 km trips), so the in-period
    # 100 km trip gets half of the 100 L invoice instead of all of it.
    assert result['allocations'][0]['share'] == pytest.approx(0.5)
    assert result['allocations'][0]['allocated_liters'] == pytest.approx(50)
