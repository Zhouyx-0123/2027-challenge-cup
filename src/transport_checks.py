"""Reference checks, explicit check states and route coverage for transport evidence."""
import math
from statistics import median
import pandas as pd


def validate_references(refs):
    from src.transport_evidence import numeric
    if not isinstance(refs, dict):
        raise ValueError('参考配置必须为对象')
    if refs.get('cost_basis') not in {None, '', 'invoice_gross'}:
        raise ValueError('成本口径应为空或 invoice_gross')
    for key in ('capacities', 'theory_l_per_100km', 'price_checks', 'time_checks', 'location_checks'):
        if not isinstance(refs.get(key, {}), dict):
            raise ValueError(key + ' 必须为对象')
    for value in refs.get('capacities', {}).values():
        if numeric(value) is None or numeric(value) <= 0:
            raise ValueError('油箱容量须为正数')
    for key in ('theory_l_per_100km', 'team_theory_factors', 'theory_factors'):
        raw = refs.get(key)
        if raw is None:
            continue
        rows = list(raw.values()) if isinstance(raw, dict) else raw if isinstance(raw, list) else None
        if rows is None:
            raise ValueError(key + ' 必须为对象或记录列表')
        for value in rows:
            if not isinstance(value, dict) or numeric(value.get('value', value.get('theory_l_per_100km', value.get('百公里理论油耗')))) is None or numeric(value.get('value', value.get('theory_l_per_100km', value.get('百公里理论油耗')))) <= 0 or not (value.get('source') or value.get('来源')) or not (value.get('version') or value.get('版本')):
                raise ValueError('理论因子须有正数 value、source 和 version')
            if key in {'team_theory_factors', 'theory_factors'}:
                if not (value.get('energy_type') or value.get('能源类型')):
                    raise ValueError('团队理论因子必须明确能源类型')
                if not (value.get('vehicle_type') or value.get('车型') or value.get('plate') or value.get('车牌')):
                    raise ValueError('团队理论因子必须明确车型或车牌适用范围')
    for value in refs.get('price_checks', {}).values():
        if not isinstance(value, dict) or numeric(value.get('expected')) is None or numeric(value['expected']) <= 0 or numeric(value.get('relative_threshold')) is None or numeric(value['relative_threshold']) < 0:
            raise ValueError('油价须有正数 expected 和非负 relative_threshold')
    for key in ('time_checks', 'location_checks'):
        if any(not isinstance(v, dict) for v in refs.get(key, {}).values()):
            raise ValueError(key + ' 每项必须为对象')
    if any(refs.get(k) is not None for k in ('team_theory_factors', 'theory_factors', 'theory_l_per_100km')):
        from src.transport_evidence import _normalize_theory_factors
        _normalize_theory_factors(refs)
    return refs


def distance_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(h)))


def coordinates(record):
    from src.transport_evidence import numeric
    lat, lon = numeric(record.get('lat')), numeric(record.get('lon'))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return lat, lon


def finish_report(result, records, trips, refs, capacities, complete_history):
    from src.transport_evidence import numeric, plate, timestamp
    result['checks'] = []

    def check(r, rule, ready, anomaly=False, reason='', **evidence):
        entry = dict(record_id=r['record_id'], rule=rule, status='anomaly' if ready and anomaly else 'checked' if ready else 'unavailable',
                     reason=reason, source_image=r.get('source_image'), **evidence)
        result['checks'].append(entry)
        if not ready:
            result['gaps'].append(dict(record_id=r['record_id'], rule=rule, reason=reason))
        elif anomaly:
            result['anomalies'].append(entry)

    # The same predicates control execution and unavailable flags.
    result['anomalies'] = [a for a in result['anomalies'] if a['rule'] not in {'tank_capacity', 'price_outlier'}]
    for r in records:
        rid, vehicle = r['record_id'], plate(r.get('plate'))
        price = refs.get('price_checks', {}).get(rid, {})
        actual = numeric(r.get('unit_price'))
        ready = bool(price.get('source') and price.get('basis_confirmed') is True and actual is not None)
        deviation = abs(actual / float(price['expected']) - 1) if ready else None
        check(r, 'price_outlier', ready, ready and deviation > float(price['relative_threshold']),
              reason='同油品地区日期与税费口径比较' if ready else 'missing_or_unconfirmed_price_reference', actual=actual, reference=price, deviation=deviation)
        qty = numeric(r.get('quantity'))
        fuel = str(r.get('unit', '')).lower() in {'l', '升'} and any(x in str(r.get('item_name')) for x in ('柴油','汽油'))
        capacity = numeric(capacities.get(vehicle))
        ready = fuel and qty is not None and qty >= 0 and capacity is not None
        check(r, 'tank_capacity', ready, ready and qty > capacity, reason='容量比较' if ready else 'missing_or_inapplicable_capacity', quantity=qty, threshold=capacity)
        allocations = [a for a in result['allocations'] if a['record_id'] == rid]
        from src.transport_evidence import _select_theory_factor
        trip = next((t for a in allocations for t in trips if t['trip_id'] == a['trip_id']), {})
        selection = _select_theory_factor(trip, 'fuel', trip.get('start'), refs) if trip else {'status': 'unmatched', 'candidate_factor_ids': []}
        ready = bool(selection['status'] == 'matched' and allocations)
        check(r, 'theory_comparison', ready, reason='同趟粒度比较' if ready else 'theory_factor_' + selection['status'],
              reference=selection, factor_candidate_ids=selection['candidate_factor_ids'])
        check(r, 'cost_calculation', bool(refs.get('cost_basis') == 'invoice_gross' and allocations),
              reason='含税总额周期分配' if refs.get('cost_basis') == 'invoice_gross' and allocations else 'missing_cost_basis_or_valid_allocation')
        timing = refs.get('time_checks', {}).get(rid, {})
        when = timestamp(timing.get('replenished_at'))
        start, end = timestamp(timing.get('coverage_start')), timestamp(timing.get('coverage_end'))
        comparable_trips = ([t for t in trips if t['plate'] == vehicle and t['end'] >= start and t['start'] <= end]
                            if not pd.isna(start) and not pd.isna(end) else [])
        ready = bool(timing.get('source') and timing.get('history_complete') is True and not pd.isna(when) and not pd.isna(start) and not pd.isna(end) and start <= when <= end)
        overlaps = [t['trip_id'] for t in comparable_trips if t['start'] <= when <= t['end']] if ready else []
        if not ready:
            check(r, 'time_contradiction', False, reason='missing_precise_time_or_complete_coverage', reference=timing, trip_ids=[])
        elif not comparable_trips:
            # No trip can be compared with this replenishment: report a lead, not a contradiction.
            check(r, 'time_contradiction', False, reason='no_trip_to_compare', reference=timing, trip_ids=[])
        else:
            check(r, 'time_contradiction', True, anomaly=bool(overlaps),
                  reason='补能时刻落在行程内，与站内补能场景矛盾' if overlaps else '补能时刻位于行程间隙，无时间冲突',
                  reference=timing, trip_ids=overlaps)
        location = refs.get('location_checks', {}).get(rid, {})
        invoice_point = coordinates(location)
        limit, window = numeric(location.get('max_distance_km')), numeric(location.get('max_time_delta_minutes'))
        observed_at = timestamp(location.get('replenished_at'))
        points = []
        observations = location.get('observations', [])
        if not isinstance(observations, list):
            raise ValueError('observations 必须为列表')
        for item in observations:
            if not isinstance(item, dict):
                raise ValueError('位置观测必须为对象')
            point, at = coordinates(item), timestamp(item.get('observed_at'))
            if point and item.get('source') and plate(item.get('plate')) == vehicle and not pd.isna(at) and not pd.isna(observed_at) and window is not None and abs((at-observed_at).total_seconds()) <= window*60:
                points.append((item, point, abs((at-observed_at).total_seconds())))
        ready = bool(invoice_point and location.get('source') and points and limit is not None and limit >= 0 and window is not None and window >= 0)
        nearest = min(points, key=lambda p: p[2]) if ready else None
        distance = distance_km(invoice_point, nearest[1]) if ready else None
        check(r, 'region_contradiction', ready, ready and distance > limit,
              reason='同车近时点位置距离比较' if ready else 'missing_location_or_vehicle_observation', distance_km=distance, threshold_km=limit,
              reference=location, observation=nearest[0] if ready else None)
    linked = {tid for m in result['matches'] if m['status'] == 'matched' for tid in m['candidate_trip_ids']}
    allocations = {a['trip_id']: a for a in result['allocations']}
    result['routes'] = []
    for route in sorted({str(t['od_site']) for t in trips}):
        route_trips = [t for t in trips if str(t['od_site']) == route]
        sample = [allocations[t['trip_id']] for t in route_trips if t['trip_id'] in allocations]
        total = sum(t['trip_km'] for t in route_trips)
        covered = sum(a['trip_km'] for a in sample)
        associated = sum(t['trip_km'] for t in route_trips if t['trip_id'] in linked)
        costs = [a['yuan_per_km'] for a in sample if a['yuan_per_km'] is not None]
        result['routes'].append(dict(od_site=route, energy_type='fuel', total_trips=len(route_trips), sample_trips=len(sample),
            sample_km=covered, total_km=total, covered_km=covered, coverage_rate=covered/total if total else None,
            association_km=associated, association_rate=associated/total if total else None,
            median_l_per_100km=median(a['l_per_100km'] for a in sample) if sample else None,
            median_yuan_per_km=median(costs) if costs else None, cost_sample_trips=len(costs),
            rule='docs45-section5', cost_basis=refs.get('cost_basis'),
            record_ids=sorted({a['record_id'] for a in sample}), cycle_ids=sorted({a['cycle_id'] for a in sample})))
