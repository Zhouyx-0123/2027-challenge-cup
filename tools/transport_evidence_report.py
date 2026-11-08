"""Run transport matching on canonical physical-trip input; save one atomic report."""
import argparse
import json
from pathlib import Path
import pandas as pd
from src.transport_evidence import analyze, atomic_json, store_digest, consolidate, _snapshot_invoice_images


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--invoices', type=Path, required=True)
    p.add_argument('--trips', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-mode', choices=['real', 'simulated'], required=True)
    p.add_argument('--complete-history', action='store_true', help='调用方确认输入包含完整周期行程')
    p.add_argument('--references', type=Path, help='已核实的参考配置JSON（支持团队理论因子表）')
    p.add_argument('--period-start', help='统计开始日期 YYYY-MM-DD')
    p.add_argument('--period-end', help='统计结束日期 YYYY-MM-DD')
    p.add_argument('--vehicles', help='逗号分隔车牌范围')
    p.add_argument('--image-dir', type=Path, help='原始发票图片目录，用于批次快照')
    a = p.parse_args()
    if a.output.resolve() in {a.invoices.resolve(), a.trips.resolve()}:
        p.error('输出路径必须与输入不同')
    data = json.loads(a.invoices.read_text(encoding='utf-8'))
    records = consolidate({'records': data['records'] if isinstance(data, dict) else data, 'events': []})['records']
    trips = pd.read_parquet(a.trips) if a.trips.suffix == '.parquet' else pd.read_csv(a.trips, dtype={'waybill_no': str, 'trip_id': str})
    refs = json.loads(a.references.read_text(encoding='utf-8')) if a.references else {}
    result = analyze(records, trips, source_mode=a.source_mode, complete_history=a.complete_history, references=refs, period_start=a.period_start, period_end=a.period_end, vehicles=[x.strip() for x in a.vehicles.split(',') if x.strip()] if a.vehicles else None)
    from datetime import datetime
    result['run'].update(records_digest=store_digest(records), created_at=datetime.now().isoformat(),
                         input_snapshot={'records': records, 'trips': json.loads(trips.to_json(orient='records', date_format='iso')), 'trip_columns': list(trips.columns), 'invoice_images': _snapshot_invoice_images(records, a.image_dir or Path.cwd())},
                         references_snapshot=refs)
    atomic_json(a.output.parent / 'runs' / a.source_mode / (result['run']['batch_id'] + '.json'), result)
    atomic_json(a.output, result)


if __name__ == '__main__':
    main()
