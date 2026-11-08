"""Transport evidence storage and deterministic analysis. No estimated invoice fields."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

FIELDS = ['record_id', 'invoice_no', 'invoice_code', 'invoice_date', 'seller_name',
          'item_name', 'quantity', 'unit', 'unit_price', 'invoice_amount', 'tax_amount',
          'plate', 'source_image', 'source_mode', 'note']
HEADERS = ['record_id', '发票号码', '发票代码', '开票日期', '销售方名称', '商品或服务名称',
           '数量', '单位', '单价', '价税合计', '税额', '车牌', '证据索引', '数据性质', '备注']
REQUIRED = [k for k in FIELDS if k not in {'invoice_code', 'tax_amount', 'note', 'record_id'}]
RULE = 'transport-evidence-v2'


def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def plate(value):
    return str(value or '').replace(' ', '').upper()


def timestamp(value):
    result = pd.to_datetime(value, errors='coerce')
    if pd.isna(result):
        return pd.NaT
    return result.tz_localize('Asia/Shanghai') if result.tzinfo is None else result.tz_convert('Asia/Shanghai')


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_store(mode, directory):
    if mode not in {'real', 'simulated'}:
        raise ValueError('数据性质无效')
    path = Path(directory) / ('transport_' + mode + '.json')
    store = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'records': [], 'events': []}
    return consolidate(store)


def assess(record):
    gaps = [k for k in REQUIRED if record.get(k) is None or str(record.get(k)).strip() == '']
    if pd.isna(timestamp(record.get('invoice_date'))):
        gaps.append('invalid_invoice_date')
    for key in ('quantity', 'unit_price', 'invoice_amount'):
        if numeric(record.get(key)) is None:
            gaps.append('invalid_' + key)
    unit = str(record.get('unit') or '').strip().lower()
    name = str(record.get('item_name') or '')
    if unit in {'l', '升'} and any(x in name for x in ('柴油', '汽油')):
        energy = 'fuel'
    elif unit in {'kwh', '千瓦时', '度'} and any(x in name for x in ('电', '充电')) and '服务' not in name:
        energy = 'electricity'
    else:
        energy = 'unknown'
        gaps.append('energy_or_unit')
    if len(record.get('items') or []) > 1 or record.get('multiple_items'):
        gaps.append('multiple_items_review')
    if any(numeric(record.get(k)) is not None and numeric(record[k]) < 0
           for k in ('quantity', 'invoice_amount', 'tax_amount')):
        gaps.append('red_invoice_review')
    a, b, c = (numeric(record.get(k)) for k in ('amount_excluding_tax', 'tax_amount', 'invoice_amount'))
    if all(v is not None for v in (a, b, c)) and abs(a + b - c) > .010001:
        gaps.append('amount_check_failed')
    if record.get('error') or record.get('status') in {'failed', 'offline', 'error'}:
        gaps.append('ocr_failed')
    return energy, sorted(set(gaps))


def register(record, image_bytes, filename, directory):
    from src.finance_workflow import _lock
    if not isinstance(record, dict) or type(record.get('mock')) is not bool:
        raise ValueError('缺少识别来源标记')
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise ValueError('缺少原图')
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
        raise ValueError('不支持的票图格式')
    mode = 'simulated' if record['mock'] else 'real'
    directory = Path(directory)
    digest = hashlib.sha256(image_bytes).hexdigest()
    evidence = Path('evidence') / mode / (digest + suffix)
    with _lock(directory):
        store = read_store(mode, directory)
        raw = dict(record, source_mode=mode, source_image=evidence.as_posix())
        signature = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        existing = [r for r in store['records'] if raw.get('invoice_no') and r.get('invoice_no') == raw['invoice_no']]
        duplicate = any(r.get('signature') == signature or any(v.get('signature') == signature for v in r.get('versions', [])) for r in existing)
        conflict = bool(existing) and not duplicate
        store['events'].append({'at': datetime.now().isoformat(), 'invoice_no': raw.get('invoice_no'),
                                'signature': signature, 'kind': 'duplicate' if duplicate else 'conflict' if conflict else 'register'})
        (directory / evidence).parent.mkdir(parents=True, exist_ok=True)
        (directory / evidence).write_bytes(image_bytes)
        if not duplicate:
            raw.update(record_id='INV-' + uuid.uuid4().hex, signature=signature)
            raw['energy_type'], raw['missing_fields'] = assess(raw)
            if conflict:
                raw['missing_fields'].append('invoice_conflict')
                for r in existing:
                    r['missing_fields'] = sorted(set(r.get('missing_fields', []) + ['invoice_conflict']))
            if conflict:
                existing[0].setdefault('versions', []).append(raw)
            else:
                store['records'].append(raw)
        atomic_json(directory / ('transport_' + mode + '.json'), store)
    return {'conflicts': int(conflict), 'added': int(not duplicate and not conflict), 'duplicates': int(duplicate), 'rejected': [], 'source_mode': mode}


def supplement(mode, record_id, updates, directory, *, expected_digest=None):
    from src.finance_workflow import _lock
    if not isinstance(updates, dict):
        raise ValueError('补充内容必须是JSON对象')
    allowed = set(FIELDS) - {'record_id', 'source_mode', 'source_image'}
    if set(updates) - allowed:
        raise ValueError('补充字段超出允许范围')
    with _lock(Path(directory)):
        store = read_store(mode, directory)
        record = next((r for r in store['records'] if r['record_id'] == record_id), None)
        if record is None:
            raise ValueError('记录不存在')
        if expected_digest is not None and store_digest([record]) != expected_digest:
            raise ValueError('记录已被其他操作修改，请刷新后重试')
        before = dict(record)
        if updates.get('invoice_no') and any(r['record_id'] != record_id and r.get('invoice_no') == updates['invoice_no'] for r in store['records']):
            raise ValueError('票号已存在，请处理冲突记录')
        record.update(updates)
        record['energy_type'], record['missing_fields'] = assess(record)
        if 'invoice_conflict' in before.get('missing_fields', []):
            record['missing_fields'].append('invoice_conflict')
        store['events'].append({'kind': 'supplement', 'record_id': record_id, 'before': before, 'updates': updates})
        atomic_json(Path(directory) / ('transport_' + mode + '.json'), store)


def export_excel(records):
    wb = Workbook()
    ws = wb.active
    ws.title = '发票证据表'
    ws.append(HEADERS)
    records = consolidate({'records': records, 'events': []})['records']
    for record in records:
        record = display_record(record)
        values = [record.get(k) for k in FIELDS]
        for i in (6, 8, 9, 10):
            values[i] = numeric(values[i])
        ws.append(values)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'
    detail = wb.create_sheet('票据明细')
    detail.append(['record_id', '发票号码', '明细序号', '项目名称', '规格型号',
                   '单位', '数量', '单价', '金额', '税率', '税额', '识别置信度'])
    for record in records:
        items = record.get('items') or []
        if not items and record.get('item_name'):
            items = [{
                'item_name': record.get('item_name'),
                'specification': record.get('specification'),
                'unit': record.get('unit'),
                'quantity': record.get('quantity'),
                'unit_price': record.get('unit_price'),
                'amount': record.get('amount_excluding_tax'),
                'tax_rate': record.get('tax_rate'),
                'tax_amount': record.get('tax_amount'),
                'confidence': record.get('confidence'),
            }]
        for index, item in enumerate(items, 1):
            detail.append([
                record['record_id'], record.get('invoice_no'), index,
                item.get('item_name') or item.get('name'),
                item.get('specification'), item.get('unit'),
                numeric(item.get('quantity')), numeric(item.get('unit_price')),
                numeric(item.get('amount')), item.get('tax_rate'),
                numeric(item.get('tax_amount')), item.get('confidence'),
            ])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()



def _normalize_theory_factors(references):
    """Accept team factor tables as rows or legacy plate-keyed mappings."""
    raw = references.get('team_theory_factors')
    if raw is None:
        raw = references.get('theory_factors')
    legacy_plate_map = raw is None
    if legacy_plate_map:
        raw = references.get('theory_l_per_100km', {})
    if isinstance(raw, dict):
        rows = []
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            row = dict(value)
            # Legacy map: key is a plate; newer maps may use factor_id.
            row.setdefault('plate', key)
            rows.append(row)
    elif isinstance(raw, list):
        rows = [dict(x) for x in raw if isinstance(x, dict)]
    else:
        raise ValueError('团队理论因子表必须为对象或记录列表')
    normalized = []
    for i, row in enumerate(rows):
        value = numeric(row.get('value', row.get('theory_l_per_100km', row.get('百公里理论油耗'))))
        if value is None or value <= 0:
            raise ValueError(f'理论因子第{i+1}行缺少有效百公里理论油耗')
        if not (row.get('source') or row.get('来源')) or not (row.get('version') or row.get('版本')):
            raise ValueError(f'理论因子第{i+1}行必须提供 source 和 version')
        vehicle_type = str(row.get('vehicle_type') or row.get('车型') or '').strip() or None
        energy_raw = row.get('energy_type') or row.get('能源类型')
        if not legacy_plate_map and not energy_raw:
            raise ValueError(f'理论因子第{i+1}行缺少能源类型')
        if not legacy_plate_map and not vehicle_type and not row.get('plate') and not row.get('车牌'):
            raise ValueError(f'理论因子第{i+1}行缺少车型或车牌适用范围')
        conditions = row.get('conditions') or row.get('适用条件') or {}
        if isinstance(conditions, str):
            try:
                conditions = json.loads(conditions) if conditions.strip() else {}
            except ValueError as exc:
                raise ValueError(f'理论因子第{i+1}行适用条件须为JSON对象') from exc
        if not isinstance(conditions, dict):
            raise ValueError(f'理论因子第{i+1}行适用条件须为JSON对象')
        normalized.append({
            **row, 'value': value, 'source': row.get('source') or row.get('来源'), 'version': row.get('version') or row.get('版本'),
            'factor_id': str(row.get('factor_id') or row.get('id') or row.get('因子ID') or f'factor-{i+1}'),
            'plate': plate(row.get('plate') or row.get('车牌')) if (row.get('plate') or row.get('车牌')) else None,
            'vehicle_type': vehicle_type,
            'energy_type': ({'燃油': 'fuel', '柴油': 'fuel', '汽油': 'fuel', '电': 'electricity', '电力': 'electricity'}.get(str(energy_raw or 'fuel').strip(), str(energy_raw or 'fuel').strip().lower())),
            'valid_from': str(row.get('valid_from') or row.get('start_date') or row.get('生效日期') or '')[:10] or None,
            'valid_to': str(row.get('valid_to') or row.get('end_date') or row.get('失效日期') or '')[:10] or None,
            'conditions': conditions,
        })
    return normalized


def _vehicle_type_of(vehicle):
    return str(vehicle.get('vehicle_type') or vehicle.get('vehicle_model') or vehicle.get('车型') or '').strip()


def _missing_vehicle_type(vehicle, energy_type, at, factor_rows):
    """True when a vehicle-type factor applies but the trip carries no vehicle_type.

    Plate-only factors stay usable without vehicle_type; only factors that require
    a vehicle type turn the missing column into an explicit gap instead of a silent
    unmatched comparison.
    """
    if _vehicle_type_of(vehicle):
        return False
    day = str(at)[:10]
    for row in factor_rows:
        if not row.get('vehicle_type'):
            continue
        if row.get('plate') and row['plate'] != plate(vehicle.get('plate')):
            continue
        if row.get('energy_type') not in (energy_type, 'all', '*'):
            continue
        if row.get('valid_from') and day < row['valid_from']:
            continue
        if row.get('valid_to') and day > row['valid_to']:
            continue
        return True
    return False


def _select_theory_factor(vehicle, energy_type, at, references):
    rows = _normalize_theory_factors(references)
    vehicle_type = _vehicle_type_of(vehicle)
    day = str(at)[:10]
    candidates = []
    for row in rows:
        if row.get('plate') and row['plate'] != plate(vehicle.get('plate')):
            continue
        if row.get('vehicle_type') and row['vehicle_type'] != vehicle_type:
            continue
        if row.get('energy_type') not in (energy_type, 'all', '*'):
            continue
        if row.get('valid_from') and day < row['valid_from']:
            continue
        if row.get('valid_to') and day > row['valid_to']:
            continue
        # Optional applicability conditions.
        conditions = row.get('conditions') or {}
        if any(str(vehicle.get(k, '')) != str(v) for k, v in conditions.items()):
            continue
        candidates.append(row)
    if len(candidates) == 1:
        return {'status': 'matched', 'factor': candidates[0], 'candidate_factor_ids': [candidates[0]['factor_id']]}
    return {'status': 'unmatched' if not candidates else 'ambiguous',
            'factor': None, 'candidate_factor_ids': [x['factor_id'] for x in candidates]}


def _scope_inputs(records, trips, period_start=None, period_end=None, vehicles=None):
    """Apply one shared reporting scope; keep the last pre-period invoice per vehicle as cycle anchor.

    history_trips is the vehicle-filtered, date-unfiltered history: the cycle
    denominator needs it so pre-period kilometres are not silently dropped from an
    anchor invoice's cycle. scoped_trips stays the period-only view used for
    coverage, routes and matches.
    """
    start = str(period_start)[:10] if period_start else None
    end = str(period_end)[:10] if period_end else None
    selected_vehicles = {plate(x) for x in vehicles or [] if plate(x)}
    if start and end and start > end:
        raise ValueError('统计期间开始日期不能晚于结束日期')
    history_trips = trips.copy()
    if selected_vehicles:
        history_trips = history_trips[history_trips['plate'].map(plate).isin(selected_vehicles)]
    scoped_trips = history_trips.copy()
    dates = pd.to_datetime(scoped_trips['depart_ts'], errors='coerce').dt.strftime('%Y-%m-%d')
    if start:
        scoped_trips = scoped_trips[dates >= start]
        dates = pd.to_datetime(scoped_trips['depart_ts'], errors='coerce').dt.strftime('%Y-%m-%d')
    if end:
        scoped_trips = scoped_trips[dates <= end]
    eligible = [r for r in records if not selected_vehicles or plate(r.get('plate')) in selected_vehicles]
    if start or end:
        by_vehicle = {}
        for r in eligible:
            d = str(r.get('invoice_date') or '')[:10]
            if not d:
                continue
            if not end or d <= end:
                by_vehicle.setdefault(plate(r.get('plate')), []).append((d, r))
        chosen = []
        for vehicle, items in by_vehicle.items():
            # Keep all in-period invoices for review, but only an energy invoice
            # may anchor a fuel/charging cycle. A repair invoice must never
            # replace the previous fuel ticket as the cycle start.
            inside = [r for d, r in items if (not start or d >= start) and (not end or d <= end)]
            before_energy = [
                (d, r) for d, r in items
                if start and d < start and assess(r)[0] in {'fuel', 'electricity'}
            ]
            if before_energy:
                chosen.append(max(before_energy, key=lambda item: item[0])[1])
            chosen.extend(inside)
        eligible = chosen
    scope = {'period_start': start, 'period_end': end,
             'vehicles': sorted(selected_vehicles) if selected_vehicles else None,
             'trip_count': len(scoped_trips), 'invoice_count': len(eligible)}
    return eligible, scoped_trips, history_trips, scope


def analyze(records, trips, *, source_mode, complete_history=False, capacities=None, references=None, period_start=None, period_end=None, vehicles=None):
    """Canonical input: physical trip_id, waybill_no, plate, depart_ts, arrive_ts,
    trip_km, od_site. Reject inconsistent repeated trip rows instead of choosing one.
    Cycles require explicit complete_history; dates follow docs/45 section 5.
    """
    if source_mode not in {'real', 'simulated'} or any(r.get('source_mode') != source_mode for r in records):
        raise ValueError('真实与模拟数据必须隔离')
    if not trips.empty and 'source_mode' in trips and set(trips.source_mode) != {source_mode}:
        raise ValueError('行程数据性质不一致')
    trips = canonical_trips(trips)
    raw_records = list(records)
    raw_trips = trips.copy()
    records, trips, history_trips, scope = _scope_inputs(records, trips, period_start, period_end, vehicles)
    required = {'trip_id', 'waybill_no', 'plate', 'depart_ts', 'arrive_ts', 'trip_km', 'od_site'}
    if required - set(trips.columns):
        raise ValueError('行程缺少字段: ' + ','.join(sorted(required - set(trips.columns))))
    result = {k: [] for k in ('matches', 'allocations', 'routes', 'anomalies', 'gaps', 'waybills')}
    result['run'] = {'batch_id': uuid.uuid4().hex, 'rule': RULE, 'source_mode': source_mode,
                     'complete_history': complete_history, 'scope': scope,
                     'input_sha256': hashlib.sha256((json.dumps(raw_records, sort_keys=True, ensure_ascii=False) + raw_trips.to_json(orient='records') + json.dumps(scope, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()}
    from src.transport_checks import validate_references
    references = validate_references(references or {})
    factor_rows = _normalize_theory_factors(references)
    capacities = capacities or references.get('capacities', {})
    validate_references({'capacities': capacities})
    capacities = {plate(k): float(v) for k, v in capacities.items()}
    all_valid = []
    invalid_plates = set()
    for tid, group in history_trips.groupby('trip_id', dropna=False):
        fields = ['plate', 'depart_ts', 'arrive_ts', 'trip_km', 'od_site']
        row = group.iloc[0].to_dict()
        start, end = timestamp(row['depart_ts']), timestamp(row['arrive_ts'])
        km = numeric(row['trip_km'])
        if pd.isna(tid) or any(group[k].nunique(dropna=False) > 1 for k in fields) or pd.isna(start) or pd.isna(end) or end < start or km is None or km <= 0 or not plate(row['plate']):
            invalid_plates.update(plate(v) for v in group['plate'])
            result['gaps'].append({'trip_id': str(tid), 'reason': 'invalid_or_conflicting_trip'})
            continue
        row.update(trip_id=str(tid), plate=plate(row['plate']), start=start, end=end, trip_km=km,
                   waybills=sorted(set(str(w) for w in group.waybill_no.dropna())))
        all_valid.append(row)
    period_start_day, period_end_day = scope['period_start'], scope['period_end']

    def in_period(trip):
        day = trip['start'].strftime('%Y-%m-%d')
        return bool((not period_start_day or day >= period_start_day) and (not period_end_day or day <= period_end_day))

    valid = [t for t in all_valid if in_period(t)]
    period_start_ts = timestamp(scope['period_start']) if scope['period_start'] else None
    counts = {}
    for r in records:
        if r.get('invoice_no'):
            counts[r['invoice_no']] = counts.get(r['invoice_no'], 0) + 1
    usable = []
    for r in records:
        energy, missing = assess(r)
        rid = r['record_id']
        if counts.get(r.get('invoice_no'), 0) > 1 or 'invoice_conflict' in r.get('missing_fields', []):
            missing.append('invoice_conflict')
            result['anomalies'].append({'record_id': rid, 'rule': 'duplicate_invoice_no', 'invoice_no': r.get('invoice_no')})
        candidates = []
        if not missing:
            day = timestamp(r['invoice_date']).floor('D')
            candidates = [t for t in valid if t['plate'] == plate(r['plate']) and t['start'] < day + pd.Timedelta(days=1) and t['end'] >= day]
        waybills = sorted({w for t in candidates for w in t['waybills']})
        status = ('matched' if len(waybills) == 1 else 'ambiguous' if waybills else 'unmatched') if not missing else None
        result['matches'].append({'record_id': rid, 'invoice_no': r.get('invoice_no'), 'status': status,
                                  'candidate_waybills': waybills, 'candidate_trip_ids': [t['trip_id'] for t in candidates],
                                  'reason': missing or ['calendar_date_overlap'], 'source_image': r.get('source_image')})
        if missing:
            result['gaps'].append({'record_id': rid, 'reason': missing})
        elif energy == 'electricity':
            result['gaps'].append({'record_id': rid, 'reason': 'electricity_pending_review'})
        elif numeric(r.get('quantity')) > 0:
            usable.append(r)
        price = references.get('price_checks', {}).get(rid)
        if price and price.get('source') and price.get('basis_confirmed') and numeric(price.get('expected')) and numeric(price.get('relative_threshold')) is not None:
            actual = numeric(r.get('unit_price'))
            if actual is not None and abs(actual / float(price['expected']) - 1) > float(price['relative_threshold']):
                result['anomalies'].append({'record_id': rid, 'rule': 'price_outlier', 'actual': actual, 'reference': price})
        capacity = (capacities or {}).get(plate(r.get('plate')))
        if capacity and energy == 'fuel' and numeric(r.get('quantity')) is not None and numeric(r['quantity']) > capacity:
            result['anomalies'].append({'record_id': rid, 'rule': 'tank_capacity', 'quantity': numeric(r['quantity']), 'threshold': capacity})
    for vehicle in sorted({plate(r['plate']) for r in usable}):
        invoices = sorted([r for r in usable if plate(r['plate']) == vehicle], key=lambda r: r['invoice_date'])
        for left, right in zip(invoices, invoices[1:]):
            start, end = timestamp(left['invoice_date']), timestamp(right['invoice_date'])
            cycle = left['record_id'] + ':' + right['record_id']
            # The denominator spans the whole invoice-to-invoice cycle, including
            # trips before period_start; only output rows stay period-scoped.
            cycle_trips = [t for t in all_valid if t['plate'] == vehicle and start <= t['start'] < end]
            selected = [t for t in cycle_trips if in_period(t)]
            boundary = any(t['end'] > end for t in cycle_trips)
            repeated_date = sum(r['invoice_date'] == left['invoice_date'] for r in invoices) > 1 or sum(r['invoice_date'] == right['invoice_date'] for r in invoices) > 1
            if not complete_history or vehicle in invalid_plates or repeated_date or end <= start or not selected or boundary:
                result['gaps'].append({'cycle_id': cycle, 'reason': 'incomplete_or_invalid_cycle'})
                continue
            if period_start_ts and start < period_start_ts and not any(start <= t['start'] < period_start_ts for t in cycle_trips):
                # The anchor invoice starts before the reporting period, but no
                # uploaded trip covers the pre-period leg of its cycle, so the
                # invoice quantity cannot be allocated honestly.
                result['gaps'].append({'cycle_id': cycle, 'reason': 'missing_pre_period_trips'})
                continue
            total = sum(t['trip_km'] for t in cycle_trips)
            for t in selected:
                share = t['trip_km'] / total
                liters = numeric(left['quantity']) * share
                result['allocations'].append({'trip_id': t['trip_id'], 'waybills': t['waybills'], 'plate': vehicle,
                    'od_site': t['od_site'], 'cycle_id': cycle, 'record_id': left['record_id'],
                    'end_record_id': right['record_id'], 'source_image': left['source_image'], 'rule': 'docs45-section5',
                    'share': share, 'trip_km': t['trip_km'], 'allocated_liters': liters,
                    'l_per_100km': liters / t['trip_km'] * 100,
                    'allocated_cost': None, 'yuan_per_km': None, 'measurement': 'rule_allocated'})
        if invoices:
            result['gaps'].append({'record_id': invoices[-1]['record_id'], 'reason': 'open_cycle'})
    cost_basis = references.get('cost_basis')
    for item in result['allocations']:
        source = next(r for r in records if r['record_id'] == item['record_id'])
        if cost_basis == 'invoice_gross':
            item['allocated_cost'] = numeric(source['invoice_amount']) * item['share']
            item['yuan_per_km'] = item['allocated_cost'] / item['trip_km']
            item['cost_basis'] = cost_basis
        source_trip = next((t for t in valid if t['trip_id'] == item['trip_id']), {})
        selected = _select_theory_factor(source_trip, 'fuel', source_trip.get('start'), references)
        item['theory_factor_selection'] = selected
        if selected['status'] == 'matched':
            theory = selected['factor']
            item['theory_liters'] = float(theory['value']) * item['trip_km'] / 100
            item['deviation'] = item['allocated_liters'] / item['theory_liters'] - 1
            item['factor'] = theory
        elif _missing_vehicle_type(source_trip, 'fuel', source_trip.get('start'), factor_rows):
            selected['reason'] = 'missing_vehicle_type'
            result['gaps'].append({'record_id': item['record_id'], 'trip_id': item['trip_id'],
                                   'rule': 'theory_comparison', 'reason': 'missing_vehicle_type'})
    allocated = {a['trip_id'] for a in result['allocations']}
    linked = {tid for m in result['matches'] if m['status'] == 'matched' for tid in m['candidate_trip_ids']}
    total = sum(t['trip_km'] for t in valid)
    result['coverage'] = {'total_km': total, 'association_km': sum(t['trip_km'] for t in valid if t['trip_id'] in linked),
                          'allocation_km': sum(t['trip_km'] for t in valid if t['trip_id'] in allocated)}
    for key in ('association', 'allocation'):
        result['coverage'][key + '_rate'] = result['coverage'][key + '_km'] / total if total else None
    for t in valid:
        for w in t['waybills']:
            ids = [m['record_id'] for m in result['matches'] if w in m['candidate_waybills'] and m['status'] == 'matched']
            ambiguous = any(w in m['candidate_waybills'] and m['status'] == 'ambiguous' for m in result['matches'])
            result['waybills'].append({'waybill_no': w, 'trip_id': t['trip_id'], 'record_ids': ids,
                                       'status': 'matched' if ids else 'ambiguous' if ambiguous else 'unmatched'})
        if t['trip_id'] not in allocated:
            result['gaps'].append({'trip_id': t['trip_id'], 'reason': 'no_valid_cycle_cover'})
    from src.transport_checks import finish_report
    finish_report(result, records, valid, references, capacities or {}, complete_history)
    result['run']['references'] = references
    return result


def canonical_trips(frame):
    """Map wide_data using carbon_model physical-trip keys; retain all waybills."""
    frame = frame.copy()
    if 'trip_id' in frame:
        return frame
    required = {'plate', 'dist_km', 'depart_ts', 'arrive_ts', 'dep_site', 'arr_site'}
    if required - set(frame):
        return frame
    keys = pd.DataFrame(index=frame.index)
    keys['plate'] = frame.plate.astype('string').str.strip()
    keys['distance'] = pd.to_numeric(frame.dist_km, errors='coerce').round(2).astype('string')
    for name in ('depart_ts', 'arrive_ts'):
        keys[name] = pd.to_datetime(frame[name], errors='coerce').astype('string')
    for name in ('dep_site', 'arr_site'):
        keys[name] = frame[name].astype('string').str.strip()
    frame['trip_id'] = keys.fillna('<missing>').apply(
        lambda row: hashlib.sha256(json.dumps(row.tolist(), ensure_ascii=False).encode()).hexdigest(), axis=1)
    frame['trip_km'] = pd.to_numeric(frame.dist_km, errors='coerce')
    return frame


def consolidate(store):
    """Upgrade v1 conflicts to one master row, preserving original records as versions."""
    import copy
    store = copy.deepcopy(store)
    unique, masters = [], {}
    for record in store['records']:
        number = record.get('invoice_no')
        if number and number in masters:
            main = masters[number]
            main.setdefault('versions', []).append(record)
            main['missing_fields'] = sorted(set(main.get('missing_fields', []) + ['invoice_conflict']))
        else:
            unique.append(record)
            if number:
                masters[number] = record
    store['records'] = unique
    store['schema_version'] = 2
    return store


def display_record(record):
    record = dict(record)
    unit = str(record.get('unit') or '').lower()
    if unit in {'l', '升'}:
        record['unit'] = 'L'
    elif unit in {'kwh', '千瓦时', '度'}:
        record['unit'] = 'kWh'
    if record.get('multiple_items') or len(record.get('items') or []) > 1:
        for key in ('quantity', 'unit', 'unit_price'):
            record[key] = None
        record['note'] = (record.get('note') or '') + '；多明细票，数量单位单价见明细，待核'
    if 'invoice_conflict' in record.get('missing_fields', []):
        record['note'] = (record.get('note') or '') + '；同票号冲突待核'
    return record


def store_digest(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()



def _snapshot_invoice_images(records, directory):
    """Embed source invoice images so a historical batch remains independently auditable."""
    import base64
    root = Path(directory)
    images = {}
    for record in records:
        rel = record.get('source_image')
        if not rel or rel in images:
            continue
        path = root / rel
        if path.is_file():
            images[rel] = base64.b64encode(path.read_bytes()).decode('ascii')
    return images


def save_run(mode, trips, directory, *, references=None, complete_history=False, period_start=None, period_end=None, vehicles=None):
    from src.finance_workflow import _lock
    records = read_store(mode, directory)['records']
    result = analyze(records, trips, source_mode=mode, references=references, complete_history=complete_history, period_start=period_start, period_end=period_end, vehicles=vehicles)
    result['run'].update(records_digest=store_digest(records), created_at=datetime.now().isoformat(),
                         input_snapshot={'records': records, 'trips': json.loads(trips.to_json(orient='records', date_format='iso')),
                                          'trip_columns': list(trips.columns),
                                          'invoice_images': _snapshot_invoice_images(records, directory)},
                         references_snapshot=references or {})
    with _lock(Path(directory)):
        if store_digest(read_store(mode, directory)['records']) != store_digest(records):
            raise ValueError('证据已更新，请重新计算')
        atomic_json(Path(directory) / 'runs' / mode / (result['run']['batch_id'] + '.json'), result)
    return result


def list_runs(mode, directory):
    records = read_store(mode, directory)['records']
    digest = store_digest(records)
    results = []
    for path in (Path(directory) / 'runs' / mode).glob('*.json'):
        run = json.loads(path.read_text(encoding='utf-8'))
        run['run']['stale'] = run['run'].get('records_digest') != digest
        results.append(run)
    return sorted(results, key=lambda r: r['run']['created_at'], reverse=True)



def replay_run(batch_id, mode, directory):
    """Recompute a historical batch solely from its immutable embedded inputs."""
    path = Path(directory) / 'runs' / mode / (str(batch_id) + '.json')
    if not path.is_file():
        raise ValueError('历史批次不存在')
    old = json.loads(path.read_text(encoding='utf-8'))
    snapshot = old.get('run', {}).get('input_snapshot')
    if not snapshot or 'records' not in snapshot or 'trips' not in snapshot:
        raise ValueError('该历史批次没有完整输入快照，无法复算')
    trips = pd.DataFrame(snapshot['trips'], columns=snapshot.get('trip_columns'))
    scope = old.get('run', {}).get('scope', {})
    result = analyze(snapshot['records'], trips, source_mode=mode,
                     complete_history=old['run'].get('complete_history', False),
                     references=old['run'].get('references_snapshot', old['run'].get('references', {})),
                     period_start=scope.get('period_start'), period_end=scope.get('period_end'),
                     vehicles=scope.get('vehicles'))
    result['run'].update(created_at=datetime.now().isoformat(), replay_of=batch_id,
                         records_digest=store_digest(snapshot['records']),
                         input_snapshot=snapshot,
                         references_snapshot=old['run'].get('references_snapshot', old['run'].get('references', {})))
    atomic_json(Path(directory) / 'runs' / mode / (result['run']['batch_id'] + '.json'), result)
    return result
