"""参照稳定性分析。

小样本与极端院区会让效率边界随样本增减而变化。这里通过留一法（每次移出一个
参照院区重算边界）量化参照与效率分的波动，并比较输入更正前后的结论差异。
稳定性只用于解释结论可靠程度，不会自动改写已经冻结的改进目标。
"""

from .benchmark import build_panel
from .efficiency import PEER_TOL, _metric_columns, _evaluate


def _score_site(panel, target_site):
    input_side, outputs, x_matrix, y_matrix = _metric_columns(panel)
    index = next(i for i, entry in enumerate(panel['entries']) if entry['site'] == target_site)
    return _evaluate(panel, x_matrix, y_matrix, input_side, outputs, index)


def leave_one_out(store, site, months=None, include_satisfaction=True):
    panel = build_panel(store, site, months=months, include_satisfaction=include_satisfaction)
    members = list(panel['members'])
    if len(members) < 2:
        return {
            'site': site,
            'months': panel['months'],
            'caliber': panel['caliber'],
            'baseline': None,
            'runs': [],
            'stability_notes': ['可比院区不足两个，无法进行留一法稳定性分析'],
        }
    baseline = _score_site(panel, site)
    baseline_peers = set(baseline['statistical_reference']['lambda_weights'])
    runs = []
    for removed in members:
        if removed == site:
            continue
        reduced = dict(panel)
        reduced['entries'] = [entry for entry in panel['entries'] if entry['site'] != removed]
        reduced['members'] = [entry['site'] for entry in reduced['entries']]
        if len(reduced['entries']) < 2:
            runs.append({'removed_site': removed, 'feasible': False,
                         'note': '移出后仅剩目标院区，无法构造边界'})
            continue
        result = _score_site(reduced, site)
        peers = set(result['statistical_reference']['lambda_weights'])
        runs.append({
            'removed_site': removed,
            'feasible': True,
            'efficiency_score': result['efficiency_score'],
            'is_efficient': result['is_efficient'],
            'peer_sites': sorted(peers),
            'peer_set_changed': peers != baseline_peers,
            'lost_peer_support': sorted(baseline_peers - peers),
            'new_peer_support': sorted(peers - baseline_peers),
        })
    feasible = [run for run in runs if run['feasible']]
    scores = [run['efficiency_score'] for run in feasible]
    peer_changes = sum(1 for run in feasible if run['peer_set_changed'])
    notes = []
    if feasible:
        spread = max(scores) - min(scores)
        if spread >= 0.1:
            notes.append(f'留一法效率分波动 {spread:.3f}，参照结论对样本变化敏感')
        else:
            notes.append(f'留一法效率分波动 {spread:.3f}，效率分相对稳定')
        if peer_changes:
            notes.append(f'{peer_changes}/{len(feasible)} 次留一计算改变了参照院区集合')
        indispensable = sorted(baseline_peers - set().union(*[
            set(run['peer_sites']) for run in feasible])) if feasible else []
        if indispensable:
            notes.append('以下参照在部分留一场景中无法被替代: ' + ','.join(indispensable))
    return {
        'site': site,
        'months': panel['months'],
        'caliber': panel['caliber'],
        'baseline': {
            'efficiency_score': baseline['efficiency_score'],
            'peer_sites': sorted(baseline_peers),
        },
        'runs': runs,
        'stability_notes': notes,
    }


def correction_sensitivity(store, site, corrections, months=None, include_satisfaction=True):
    """比较一组临时输入更正应用前后的边界结论；更正只在内存中生效。

    corrections 形如 [{"site": "west", "resource": "care_hours", "delta": -100}]，
    delta 为更正差额。资源被更正到负值将被拒绝；缺测满意度仍不按零处理。
    """
    before = leave_one_out(store, site, months=months, include_satisfaction=include_satisfaction)
    panel = build_panel(store, site, months=months, include_satisfaction=include_satisfaction)
    adjusted = dict(panel)
    adjusted_entries = []
    for entry in panel['entries']:
        updated = dict(entry)
        updated['inputs'] = dict(entry['inputs'])
        adjusted_entries.append(updated)
    index_by_site = {entry['site']: i for i, entry in enumerate(adjusted_entries)}
    for change in corrections:
        target = change['site']
        if target not in index_by_site:
            raise ValueError('更正院区不在可比面板内: ' + target)
        entry = adjusted_entries[index_by_site[target]]
        resource = change['resource']
        value = entry['inputs'].get(resource, 0.0) + change['delta']
        if value < 0:
            raise ValueError('更正后资源投入不能为负')
        entry['inputs'][resource] = value
    adjusted['entries'] = adjusted_entries
    if len(adjusted_entries) < 2:
        after_summary = None
        notes = ['更正后可比院区仍不足两个，无法比较']
    else:
        after = _score_site(adjusted, site)
        after_summary = {
            'efficiency_score': after['efficiency_score'],
            'is_efficient': after['is_efficient'],
            'peer_sites': sorted(after['statistical_reference']['lambda_weights']),
        }
        baseline = before['baseline'] or {}
        notes = []
        if baseline and after_summary['efficiency_score'] != baseline.get('efficiency_score'):
            notes.append('效率分发生变化，原结论需重新解释')
        if baseline and after_summary['peer_sites'] != baseline.get('peer_sites'):
            notes.append('参照院区集合发生变化')
        if not notes:
            notes.append('该组更正未改变效率分与参照集合')
    return {
        'site': site,
        'months': panel['months'],
        'applied_corrections': corrections,
        'before': before['baseline'],
        'after': after_summary,
        'sensitivity_notes': notes,
    }
