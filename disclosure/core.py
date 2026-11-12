"""Web adapter: validate input, reuse original pure rules, isolate export jobs."""
from pathlib import Path
from io import BytesIO
import json
import math
import shutil
import subprocess
import sys
import tempfile

import pandas as pd
import yaml
from .engine import scenario, rules

ROOT = Path(__file__).parent
EMISSION = '绝对碳排(kgCO₂e)'
DISTANCE = '行驶里程(km)'
QUALITY = '数据质量标识'

def defaults():
    return yaml.safe_load((ROOT / 'engine/config.yaml').read_text(encoding='utf-8'))

def demo():
    fixture = json.loads((ROOT / 'fixtures/demo.json').read_text(encoding='utf-8'))
    return pd.DataFrame(fixture['rows'], columns=fixture['columns'])

def validate(data):
    required = list(demo().columns)
    missing = set(required) - set(data.columns)
    if missing:
        raise ValueError('缺少列：' + '、'.join(sorted(missing)))
    frame = data[required].copy().reset_index(drop=True)
    if frame.empty:
        raise ValueError('请至少录入一条数据。')
    if len(frame) > 10000:
        raise ValueError('演示模式最多支持 10,000 条记录。')
    for column in (EMISSION, DISTANCE, '消耗量(L/kWh)'):
        values = pd.to_numeric(frame[column], errors='coerce')
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError(f'{column} 必须为有限且非负的数字，请检查空值、负数和文本。')
        frame[column] = values
    tiers, notes = [], []
    for index, flag in enumerate(frame[QUALITY]):
        try:
            tier = rules.judge_tier(flag)
        except ValueError as exc:
            if '绿框' in str(flag) and '黄框' in str(flag):
                raise ValueError(f'原始行 {index + 2}：{exc}') from exc
            tier = rules.TIER_YELLOW
            notes.append(f'原始行 {index + 2}：未知或空白质量标识，保守降为黄框。')
        tiers.append(tier)
    frame['原始行号'] = range(2, len(frame) + 2)
    frame['数据层级'] = tiers
    return frame, notes

def calculate(data, config):
    frame, notes = validate(data)
    for name in ('baseline_intensity', 'gap_carbon_price', 'shadow_carbon_price_central'):
        value = float(config[name])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(name + ' 必须为有限正数')
    factor = float(config['scenario_control_factor'])
    band = float(config['uncertainty_range'])
    if not math.isfinite(band) or not 0 <= band <= 1:
        raise ValueError('黄框带宽须在 0 至 100% 之间')
    green = frame[frame['数据层级'] == '绿框']
    yellow = frame[frame['数据层级'] == '黄框']
    e, d = green[EMISSION].sum(), green[DISTANCE].sum()
    a = scenario.allowance(d, config['baseline_intensity'], factor)
    result = scenario.balance(e, a, config['gap_carbon_price'])
    sg = rules.calc_shadow_cost(e, config['shadow_carbon_price_central'])
    sy = rules.calc_shadow_cost(yellow[EMISSION].sum(), config['shadow_carbon_price_central'])
    lo, hi = rules.calc_uncertainty_range(sy, band)
    scenarios = []
    for title, intensity in [('主情景', config['baseline_intensity']), ('比较情景 A', config['scenario_intensity_mid']), ('比较情景 B', config['scenario_intensity_low']), ('压力情景', config['scenario_intensity_stress'])]:
        values = scenario.balance(e, scenario.allowance(d, intensity, factor), config['gap_carbon_price'])
        scenarios.append({'情景': title, '假设强度': intensity, '情景额度': values['allowance'], '净缺口': values['gap'], '情景金额': values['cost']})
    return dict(result, frame=frame, notes=notes, green=green, yellow=yellow, emission=e, distance=d,
                shadow_green=sg, shadow_yellow=sy, shadow=sg+sy, low=lo, high=hi, scenarios=scenarios)

def input_bytes(data):
    buffer = BytesIO()
    data[list(demo().columns)].to_excel(buffer, index=False, sheet_name='原始数据')
    return buffer.getvalue()

def export(data, config):
    """Run unmodified engine per request; no shared output files or module globals."""
    calculate(data, config)
    with tempfile.TemporaryDirectory(prefix='carbon-disclosure-') as folder:
        target = Path(folder)
        for name in ('main.py', 'scenario.py', 'rules.py', 'charts.py'):
            shutil.copyfile(ROOT / 'engine' / name, target / name)
        (target / 'config.yaml').write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
        (target / 'input_data.xlsx').write_bytes(input_bytes(data))
        proc = subprocess.run([sys.executable, str(target / 'main.py')], cwd=target,
                              capture_output=True, timeout=120, encoding='utf-8', errors='replace',
                              env={**__import__('os').environ, 'PYTHONIOENCODING': 'utf-8'})
        if proc.returncode:
            raise RuntimeError('原始底稿引擎校验未通过：' + proc.stderr[-2500:])
        return {p.name: p.read_bytes() for p in (target / 'output').iterdir() if p.suffix in ('.xlsx', '.docx', '.png')}
