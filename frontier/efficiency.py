"""效率边界与真实参照院区。

采用投入导向、可变规模报酬（VRS/BCC）的数据包络分析。未完成服务与有效投诉
属于不可随意按比例放大的坏产出，按投入侧指标处理（零值安全，不做倒数变换）；
满意程度等缺测指标不进入模型，而不是按零代入。

边界由真实院区的凸组合构成：lambda 为正的院区就是可借鉴的真实参照，
lambda 组合出的目标点明确标注为统计参照（虚拟混合点），不会被描述成
一个同时做好所有指标的真实院区。
"""

import numpy as np
from scipy.optimize import linprog

from .benchmark import build_panel

TOL = 1e-7
PEER_TOL = 1e-6
EFFICIENT_TOL = 1e-6
DISPLAY_DIGITS = 6


def _number(value):
    """把 numpy 标量转成原生 JSON 类型，并隐藏求解器的数值噪声。"""
    value = float(value)
    if abs(value) < TOL:
        return 0.0
    return round(value, DISPLAY_DIGITS)


def _metric_columns(panel):
    """投入侧列 = 资源投入 + 坏产出；产出侧列 = 好产出。"""
    entries = panel['entries']
    input_side = list(panel['input_resources']) + list(panel['undesirable_outputs'])
    outputs = list(panel['desirable_outputs'])

    def input_vector(entry):
        values = [entry['inputs'].get(name, 0.0) for name in panel['input_resources']]
        values += [entry[name] for name in panel['undesirable_outputs']]
        return values

    x_matrix = np.array([input_vector(entry) for entry in entries], dtype=float)
    y_matrix = np.array([[entry[name] for name in outputs] for entry in entries], dtype=float)
    return input_side, outputs, x_matrix, y_matrix


def _solve_stage_one(x_matrix, y_matrix, index):
    n, m = x_matrix.shape
    r = y_matrix.shape[1]
    # 变量：theta, lambda(n), 投入侧松弛(m), 产出松弛(r)
    c = np.zeros(1 + n + m + r)
    c[0] = 1.0
    a_ub, b_ub = [], []
    for k in range(m):
        row = np.zeros(1 + n + m + r)
        row[1:1 + n] = x_matrix[:, k]
        row[1 + n + k] = 1.0
        row[0] = -x_matrix[index, k]
        a_ub.append(row)
        b_ub.append(0.0)
    for k in range(r):
        row = np.zeros(1 + n + m + r)
        row[1:1 + n] = -y_matrix[:, k]
        row[1 + n + m + k] = 1.0
        a_ub.append(row)
        b_ub.append(-y_matrix[index, k])
    equality = np.zeros((1, 1 + n + m + r))
    equality[0, 1:1 + n] = 1.0
    result = linprog(
        c, A_ub=np.array(a_ub), b_ub=np.array(b_ub),
        A_eq=equality, b_eq=np.array([1.0]),
        bounds=[(0, None)] * (1 + n + m + r), method='highs',
    )
    if not result.success:
        raise RuntimeError('效率边界求解失败: ' + result.message)
    return max(0.0, min(1.0, result.x[0])), result.x


def _solve_stage_two(x_matrix, y_matrix, index, theta):
    n, m = x_matrix.shape
    r = y_matrix.shape[1]
    # 变量：lambda(n), 投入侧松弛(m), 产出松弛(r)，最大化归一化松弛之和
    dim = n + m + r
    c = np.zeros(dim)
    for k in range(m):
        denom = x_matrix[index, k] if x_matrix[index, k] > 0 else 1.0
        c[n + k] = -1.0 / denom
    for k in range(r):
        denom = y_matrix[index, k] if y_matrix[index, k] > 0 else 1.0
        c[n + m + k] = -1.0 / denom
    a_ub, b_ub = [], []
    for k in range(m):
        row = np.zeros(dim)
        row[:n] = x_matrix[:, k]
        row[n + k] = 1.0
        a_ub.append(row)
        b_ub.append(theta * x_matrix[index, k])
    for k in range(r):
        row = np.zeros(dim)
        row[:n] = -y_matrix[:, k]
        row[n + m + k] = 1.0
        a_ub.append(row)
        b_ub.append(-y_matrix[index, k])
    equality = np.zeros((1, dim))
    equality[0, :n] = 1.0
    result = linprog(
        c, A_ub=np.array(a_ub), b_ub=np.array(b_ub),
        A_eq=equality, b_eq=np.array([1.0]),
        bounds=[(0, None)] * dim, method='highs',
    )
    if not result.success:
        raise RuntimeError('松弛变量求解失败: ' + result.message)
    weights = result.x[:n]
    input_slacks = result.x[n:n + m]
    output_slacks = result.x[n + m:]
    return weights, input_slacks, output_slacks


def _super_efficiency(x_matrix, y_matrix, index):
    """超效率：把自身移出参照集。VRS 下可能无可行解，此时返回 None。"""
    keep = [j for j in range(x_matrix.shape[0]) if j != index]
    if not keep:
        return None
    x_ref = x_matrix[keep]
    y_ref = y_matrix[keep]
    n, m = x_ref.shape
    r = y_ref.shape[1]
    dim = 1 + n + m + r
    c = np.zeros(dim)
    c[0] = 1.0
    a_ub, b_ub = [], []
    for k in range(m):
        row = np.zeros(dim)
        row[1:1 + n] = x_ref[:, k]
        row[1 + n + k] = 1.0
        row[0] = -x_matrix[index, k]
        a_ub.append(row)
        b_ub.append(0.0)
    for k in range(r):
        row = np.zeros(dim)
        row[1:1 + n] = -y_ref[:, k]
        row[1 + n + m + k] = 1.0
        a_ub.append(row)
        b_ub.append(-y_matrix[index, k])
    equality = np.zeros((1, dim))
    equality[0, 1:1 + n] = 1.0
    result = linprog(
        c, A_ub=np.array(a_ub), b_ub=np.array(b_ub),
        A_eq=equality, b_eq=np.array([1.0]),
        bounds=[(0, None)] * dim, method='highs',
    )
    if not result.success:
        return None
    return _number(result.x[0])


def _peer_facts(panel, input_side, outputs, entries, index, weights):
    """用真实院区的实际指标说明可借鉴的服务能力，不做虚构混合。"""
    observed = entries[index]
    facts = []
    for j, weight in enumerate(weights):
        if weight <= PEER_TOL:
            continue
        peer = entries[j]
        strengths = []
        for metric in outputs:
            if peer[metric] is not None and observed[metric] is not None and peer[metric] >= observed[metric] + TOL:
                strengths.append({'metric': metric, 'peer_value': _number(peer[metric]),
                                  'observed_value': _number(observed[metric]), 'direction': '更高的好产出'})
        for metric in panel['input_resources']:
            if peer['inputs'].get(metric, 0.0) <= observed['inputs'].get(metric, 0.0) - TOL:
                strengths.append({'metric': metric, 'peer_value': _number(peer['inputs'].get(metric, 0.0)),
                                  'observed_value': _number(observed['inputs'].get(metric, 0.0)), 'direction': '更低的资源占用'})
        for metric in panel['undesirable_outputs']:
            if peer[metric] <= observed[metric] - TOL:
                strengths.append({'metric': metric, 'peer_value': _number(peer[metric]),
                                  'observed_value': _number(observed[metric]), 'direction': '更少的坏产出'})
        facts.append({
            'site': peer['site'],
            'lambda_weight': _number(weight),
            'observed_metrics': {
                'inputs': {key: _number(value) for key, value in peer['inputs'].items()},
                **{name: _number(peer[name]) for name in outputs + list(panel['undesirable_outputs'])},
            },
            'demonstrated_strengths': strengths,
        })
    return facts


def _evaluate(panel, x_matrix, y_matrix, input_side, outputs, index):
    entries = panel['entries']
    theta, _ = _solve_stage_one(x_matrix, y_matrix, index)
    weights, input_slacks, output_slacks = _solve_stage_two(x_matrix, y_matrix, index, theta)
    n = len(entries)
    efficient = theta >= 1 - EFFICIENT_TOL and not np.any(input_slacks > PEER_TOL) and not np.any(output_slacks > PEER_TOL)

    observed_input = x_matrix[index]
    observed_output = y_matrix[index]
    target_input = theta * observed_input - input_slacks
    target_output = observed_output + output_slacks

    input_gaps, output_gaps = [], []
    for k, metric in enumerate(input_side):
        observed_value = _number(observed_input[k])
        target_value = _number(target_input[k])
        relative = _number((observed_input[k] - target_input[k]) / observed_input[k]) if observed_input[k] > 0 else None
        input_gaps.append({
            'metric': metric,
            'observed': observed_value,
            'statistical_target': target_value,
            'gap': _number(observed_input[k] - target_input[k]),
            'relative_gap': relative,
            'interpretation': '可减少的资源或坏产出' if metric in panel['input_resources'] else '可减少的坏产出（不可按比例放大）',
        })
    for k, metric in enumerate(outputs):
        observed_value = _number(observed_output[k])
        target_value = _number(target_output[k])
        relative = _number((target_output[k] - observed_output[k]) / observed_output[k]) if observed_output[k] > 0 else None
        output_gaps.append({
            'metric': metric,
            'observed': observed_value,
            'statistical_target': target_value,
            'gap': _number(target_output[k] - observed_output[k]),
            'relative_gap': relative,
            'interpretation': '可提升的好产出',
        })

    peers = _peer_facts(panel, input_side, outputs, entries, index, weights)
    result = {
        'site': entries[index]['site'],
        'efficiency_score': _number(theta),
        'is_efficient': bool(efficient),
        'super_efficiency': _super_efficiency(x_matrix, y_matrix, index) if efficient else None,
        'peers': peers,
        'statistical_reference': {
            'kind': 'virtual_convex_target',
            'note': '由真实参照院区按 lambda 凸组合得到的统计参照，不是真实存在的混合院区，不代表所有指标能同时无条件达成',
            'lambda_weights': {entries[j]['site']: _number(weights[j]) for j in range(n) if weights[j] > PEER_TOL},
            'input_side_target': {input_side[k]: _number(target_input[k]) for k in range(len(input_side))},
            'output_target': {outputs[k]: _number(target_output[k]) for k in range(len(outputs))},
        },
        'input_side_gaps': input_gaps,
        'output_gaps': output_gaps,
    }
    if efficient:
        result['frontier_note'] = '位于效率边界，本期统计参照为自身；改进空间须看可达组合分析中的项目冲突'
    return result


def _diagnostics(panel, results, x_matrix):
    n = len(panel['entries'])
    efficient_count = sum(1 for item in results if item['is_efficient'])
    columns = x_matrix.shape[1] + y_count(panel)
    extremes = [item['site'] for item in results
                if item['is_efficient'] and item['super_efficiency'] is not None and item['super_efficiency'] >= 1.5]
    notes = []
    if n < 2:
        notes.append('可比院区不足两个，无法构造边界，仅返回口径面板')
    if n <= columns * 2:
        notes.append(f'样本量 {n} 相对指标数 {columns} 偏小（经验门槛约为指标数的两倍），区分度与参照稳定性有限')
    if efficient_count / n >= 0.5 if n else False:
        notes.append('超过半数院区落在边界上，小样本下边界区分度不足，结论需结合留一法稳定性阅读')
    zero_variance = []
    for k in range(x_matrix.shape[1]):
        if np.allclose(x_matrix[:, k], x_matrix[0, k]):
            zero_variance.append(panel['input_resources'][k] if k < len(panel['input_resources'])
                                 else panel['undesirable_outputs'][k - len(panel['input_resources'])])
    if zero_variance:
        notes.append('以下指标在组内没有差异，不产生区分度: ' + ','.join(zero_variance))
    if extremes:
        notes.append('极端参照院区（超效率明显高于 1）: ' + ','.join(extremes) + '，其权重对样本变化敏感')
    return {
        'sample_size': n,
        'metric_count': columns,
        'efficient_count': efficient_count,
        'extreme_peer_sites': extremes,
        'small_sample': n <= columns * 2,
        'notes': notes,
    }


def y_count(panel):
    return len(panel['desirable_outputs'])


def group_frontier(store, site, months=None, include_satisfaction=True):
    panel = build_panel(store, site, months=months, include_satisfaction=include_satisfaction)
    response = {
        'site': site,
        'caliber': panel['caliber'],
        'months': panel['months'],
        'service_scope': panel['service_scope'],
        'care_group': panel['care_group'],
        'members': panel['members'],
        'excluded_outside_scope': panel['excluded_outside_scope'],
        'excluded_within_scope': panel['excluded_within_scope'],
        'input_resources': panel['input_resources'],
        'desirable_outputs': panel['desirable_outputs'],
        'undesirable_outputs': panel['undesirable_outputs'],
        'panel': panel['entries'],
    }
    if len(panel['entries']) < 2:
        response['results'] = []
        response['diagnostics'] = {
            'sample_size': len(panel['entries']),
            'metric_count': len(panel['input_resources']) + len(panel['undesirable_outputs']) + len(panel['desirable_outputs']),
            'efficient_count': 0,
            'extreme_peer_sites': [],
            'small_sample': True,
            'notes': ['可比院区不足两个，无法构造边界，仅返回口径面板'],
        }
        return response
    input_side, outputs, x_matrix, y_matrix = _metric_columns(panel)
    results = [_evaluate(panel, x_matrix, y_matrix, input_side, outputs, index)
               for index in range(len(panel['entries']))]
    response['results'] = results
    response['diagnostics'] = _diagnostics(panel, results, x_matrix)
    return response


def project_panel(panel, deltas_by_site):
    """按候选项目的资源增减与覆盖增量推演面板，供改进后再评估使用。"""
    entries = []
    for entry in panel['entries']:
        updated = dict(entry)
        updated['inputs'] = dict(entry['inputs'])
        delta = deltas_by_site.get(entry['site'], {})
        for resource, amount in delta.get('resource_need', {}).items():
            updated['inputs'][resource] = updated['inputs'].get(resource, 0.0) + amount
        updated['covered'] = entry['covered'] + delta.get('coverage_gain', 0)
        entries.append(updated)
    projected = dict(panel)
    projected['entries'] = entries
    projected['members'] = [entry['site'] for entry in entries]
    return projected


def evaluate_projection(panel, target_site):
    """在推演后的面板上只评估目标院区，返回它与真实参照之间的剩余差距。"""
    input_side, outputs, x_matrix, y_matrix = _metric_columns(panel)
    index = next(i for i, entry in enumerate(panel['entries']) if entry['site'] == target_site)
    return _evaluate(panel, x_matrix, y_matrix, input_side, outputs, index)
