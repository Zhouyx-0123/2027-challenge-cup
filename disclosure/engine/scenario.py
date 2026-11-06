"""参照北京移动设施配额结构的教学情景；不是正式配额核定器。"""
from math import isfinite

POLICY_URL = 'https://sthjj.beijing.gov.cn/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/zcfb/2024bzcwj/743948637/index.html'
POLICY_ATTACHMENT = POLICY_URL.rsplit('/', 1)[0] + '/2026031913231691765.docx'
POLICY_SOURCE = '京环发〔2026〕7号附件3《北京市重点碳排放单位配额核定方案》第3.3节'
MODEL_NOTE = (
    '参照北京配额规则的情景演示：仅借用移动设施“里程×强度×控排系数”的结构。'
    '无企业三年历史数据，强度为假设值，非历史统计结果；99%为所引政策的控排系数。'
    '样例为10天任务快照，不年化；仅对绿框模拟分组计算，不代表企业完整履约边界。'
    '未模拟固定设施、年度配额富缺20%熔断、新增单位首年及配额交易/抵销等机制。'
    '输入为kgCO₂e，政策为企业CO₂配额口径，两者须进一步核对，不能直接用于法定履约。'
    '结果非主管部门核定配额，情景金额非购买义务、会计费用或负债；不主张最优模型。'
)

def allowance(distance_km, assumed_intensity, control_factor=0.99):
    """返回kg情景额度，输入均须有限，里程非负，强度正，系数在(0,1]。"""
    d, i, f = map(float, (distance_km, assumed_intensity, control_factor))
    if not all(isfinite(x) for x in (d, i, f)) or d < 0 or i <= 0 or not 0 < f <= 1:
        raise ValueError('情景参数必须有限：里程≥0、假设强度>0、控排系数在(0,1]')
    return d * i * f

def balance(emission_kg, allowance_kg, price):
    """输入同一模拟边界的汇总值，先求净差额再取正，不累加逐条正差额。"""
    e, a, p = map(float, (emission_kg, allowance_kg, price))
    if not all(isfinite(x) for x in (e, a, p)) or min(e, a) < 0 or p <= 0:
        raise ValueError('情景汇总值必须有限且非负，碳价必须为正')
    delta = e - a
    gap = max(0.0, delta)
    return {'allowance': a, 'delta': delta, 'gap': gap, 'cost': gap / 1000 * p}
