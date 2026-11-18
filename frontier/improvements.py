"""改进项目报价、可达组合与冻结计划。

项目不可拆分：只有启用完整服务单元才能拿到覆盖增量，同时占用共享人员与固定
费用。可达组合是 0/1 选择问题，不允许把一个项目按比例重复。统计参照（效率
边界的虚拟凸组合）与本期可执行组合在这里严格分开：前者说明差距，后者只从
真实存在、资质可行、资源池放得下的项目里枚举。
"""

import hashlib
import json

from .benchmark import build_panel
from .efficiency import evaluate_projection, project_panel

COMBINATION_LIMIT = 2000


def quote_projects(store, selected):
    if len(set(selected)) != len(selected):
        raise ValueError('项目重复')
    rows = [store.get('projects', key) for key in selected]
    needs = {}
    for row in rows:
        for resource, value in row['resource_need'].items():
            needs[resource] = needs.get(resource, 0) + value
    return {'projects': selected, 'fixed_cents': sum(row['fixed_cents'] for row in rows), 'resource_need': needs, 'coverage_gain': sum(row['coverage_gain'] for row in rows)}


def save_project(store, row):
    store.get('sites', row['site'])
    if row['fixed_cents'] < 0 or any(value < 0 for value in row['resource_need'].values()):
        raise ValueError('项目资源不合法')
    return store.put('projects', row)


def period_availability(store, month):
    try:
        return store.get('availability', month)
    except KeyError:
        raise KeyError('该期间没有可用资源与资质资料')


def _combination_summary(rows):
    needs = {}
    for row in rows:
        for resource, value in row['resource_need'].items():
            needs[resource] = needs.get(resource, 0.0) + value
    return {
        'projects': sorted(row['id'] for row in rows),
        'sites': sorted(set(row['site'] for row in rows)),
        'fixed_cents': sum(row['fixed_cents'] for row in rows),
        'resource_need': needs,
        'coverage_gain': sum(row['coverage_gain'] for row in rows),
    }


def feasible_combinations(store, site, month):
    """枚举本期可执行的改进组合。

    资质不可获得的项目直接排除；资源池按集团共享口径约束，因此跨院区项目的
    冲突会显式列出。枚举超过上限时截断并注明，不静默抽样。
    """
    availability = period_availability(store, month)
    skills = set(availability.get('available_skills', []))
    pool = {key: value for key, value in availability.items()
            if key not in ('id', 'available_skills') and isinstance(value, (int, float))}

    project_rows = store.all('projects')
    described, candidates = [], []
    for row in project_rows:
        skill_ok = row['requires_skill'] in skills
        item = {
            'id': row['id'],
            'site': row['site'],
            'requires_skill': row['requires_skill'],
            'skill_feasible': skill_ok,
            'ineligible_reason': None if skill_ok else '本期资质不可获得: ' + row['requires_skill'],
            'fixed_cents': row['fixed_cents'],
            'resource_need': dict(row['resource_need']),
            'coverage_gain': row['coverage_gain'],
        }
        described.append(item)
        if skill_ok:
            candidates.append(row)

    results, truncated = [_combination_summary([])], False

    def fits(rows):
        needs = {}
        for row in rows:
            for resource, value in row['resource_need'].items():
                needs[resource] = needs.get(resource, 0.0) + value
        return all(need <= pool.get(resource, 0.0) + 1e-9 for resource, need in needs.items()), needs

    def search(start, chosen):
        nonlocal truncated
        for position in range(start, len(candidates)):
            trial = chosen + [candidates[position]]
            ok, _ = fits(trial)
            if not ok:
                continue
            if len(results) >= COMBINATION_LIMIT:
                truncated = True
                return
            results.append(_combination_summary(trial))
            search(position + 1, trial)
            if truncated:
                return

    search(0, [])
    results.sort(key=lambda item: (-item['coverage_gain'], item['fixed_cents'], item['projects']))
    for item in results:
        needs = item['resource_need']
        item['pool_slack'] = {resource: round(pool.get(resource, 0.0) - needs.get(resource, 0.0), 6)
                              for resource in sorted(pool)}
        item['contains_other_site_projects'] = any(one != site for one in item['sites'])

    conflicts = []
    for resource in sorted(pool):
        demand = [{'site': row['site'], 'project': row['id'], 'amount': row['resource_need'].get(resource, 0)}
                  for row in candidates if row['resource_need'].get(resource, 0) > 0]
        total = sum(item['amount'] for item in demand)
        if total > pool[resource] + 1e-9 and len(demand) > 1:
            conflicts.append({
                'resource': resource,
                'pool_amount': pool[resource],
                'total_demand': total,
                'competing_projects': sorted(demand, key=lambda item: (item['site'], item['project'])),
                'note': '共享资源池不足以同时支持全部候选，调配给一个院区会减少其他院区的可用服务',
            })

    target_ids = {row['id'] for row in project_rows if row['site'] == site}
    return {
        'site': site,
        'month': month,
        'pool': pool,
        'available_skills': sorted(skills),
        'projects': sorted(described, key=lambda item: item['id']),
        'feasible_combinations': results,
        'cross_site_conflicts': conflicts,
        'target_site_project_ids': sorted(target_ids),
        'enumeration_truncated': truncated,
        'combination_limit': COMBINATION_LIMIT,
        'caliber_note': '组合为本期可执行的真实项目集合；效率边界给出的统计参照不是可执行组合',
    }


def _panel_hash(panel):
    payload = json.dumps(panel['entries'], ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def freeze_plan(store, payload):
    """保存冻结的改进计划：当时的面板口径、边界与约束随计划一起固化。

    冻结后即使后续样本增加或资料更正，计划仍保留当时采用的边界与目标，
    复盘时对比的是实际做法与原目标，而不是被自动改写后的新目标。
    """
    site = payload['site']
    month = payload['month']
    months = payload.get('months')
    project_ids = payload.get('projects', [])
    if len(set(project_ids)) != len(project_ids):
        raise ValueError('项目重复')
    panel = build_panel(store, site, months=months, include_satisfaction=True)
    if not panel['members']:
        raise ValueError('冻结计划需要至少一个全口径可比院区')
    project_rows = [store.get('projects', key) for key in project_ids]
    for row in project_rows:
        if row['site'] != site:
            raise ValueError('冻结计划只能包含本院区项目，跨院区调配请使用可达组合分析')
    availability = period_availability(store, month)
    skills = set(availability.get('available_skills', []))
    for row in project_rows:
        if row['requires_skill'] not in skills:
            raise ValueError('项目所需资质本期不可获得: ' + row['id'])
    quote = quote_projects(store, project_ids)
    pool = {key: value for key, value in availability.items()
            if key not in ('id', 'available_skills') and isinstance(value, (int, float))}
    for resource, need in quote['resource_need'].items():
        if need > pool.get(resource, 0.0) + 1e-9:
            raise ValueError('项目组合超出本期共享资源池: ' + resource)

    projected = project_panel(panel, {site: {'resource_need': quote['resource_need'],
                                             'coverage_gain': quote['coverage_gain']}})
    projection = evaluate_projection(projected, site) if len(panel['entries']) >= 2 else None
    plan = {
        'id': payload['id'],
        'site': site,
        'frozen_month': month,
        'window_months': list(panel['months']),
        'caliber': panel['caliber'],
        'projects': list(project_ids),
        'quote': quote,
        'constraints': {'pool': pool, 'available_skills': sorted(skills)},
        'frozen_panel': panel,
        'frozen_panel_hash': _panel_hash(panel),
        'statistical_reference_at_freeze': None if projection is None else {
            'efficiency_score': projection['efficiency_score'],
            'peer_sites': sorted(projection['statistical_reference']['lambda_weights']),
            'statistical_target': projection['statistical_reference'],
        },
        'attainability_note': '统计参照只说明差距、不是可执行组合；projects 为本期可执行的真实且不可拆分的项目组合',
        'revision_note': '计划冻结后不随后续样本增加或资料更正自动改写',
    }
    revision = store.put('plans', plan, payload.get('expected_revision'))
    return {'revision': revision, 'id': plan['id'], 'frozen_panel_hash': plan['frozen_panel_hash']}


def review_plan(store, plan_id, actual=None):
    """按冻结时的边界复盘，不重新计算或改写原改进目标。"""
    plan = store.get('plans', plan_id)
    frozen = plan['frozen_panel']
    current_hash = _panel_hash(frozen)
    drift = {
        'frozen_hash_matches_snapshot': current_hash == plan['frozen_panel_hash'],
        'note': '复盘始终以冻结面板为基准；当前库中的新样本与更正不影响原目标',
    }
    actual = actual or {}
    target_gain = plan['quote']['coverage_gain']
    actual_gain = actual.get('coverage_gain', 0)
    comparison = {
        'target_coverage_gain': target_gain,
        'actual_coverage_gain': actual_gain,
        'target_projects': list(plan['projects']),
        'activated_projects': list(actual.get('projects', [])),
        'attainment_ratio': round(actual_gain / target_gain, 6) if target_gain else None,
        'target_constraints': plan['constraints'],
        'actual_resource_use': actual.get('resource_use', {}),
    }
    return {
        'id': plan_id,
        'site': plan['site'],
        'frozen_month': plan['frozen_month'],
        'window_months': plan['window_months'],
        'frozen_reference': plan['statistical_reference_at_freeze'],
        'snapshot_integrity': drift,
        'review': comparison,
    }
