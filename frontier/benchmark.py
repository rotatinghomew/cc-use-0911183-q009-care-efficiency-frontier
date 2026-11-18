"""统一口径的比较面板。

比较只在服务范围（service_scope）与照护组（care_group）相同的院区之间进行。
共享投入按既有守恒分摊落到各院区；JSON 空值表示缺测，绝不按零处理；
统计期间不同的院区不混入同一块面板，而是给出排除原因，保证口径可解释。
"""

# 指标角色在全服务中只有一处定义，效率分析与改进组合共用。
INPUT_RESOURCES = ('care_hours', 'specialist_hours')
DESIRABLE_OUTPUTS = ('covered', 'satisfaction')
UNDESIRABLE_OUTPUTS = ('unfinished', 'complaints')
COUNT_OUTPUTS = ('covered', 'unfinished', 'complaints')


def _month_rows(rows, months):
    return [row for row in rows if row['month'] in months]


def comparison_scope(store, site):
    """返回院区所在的可比范围，及与其他院区为何不可比。"""
    selected = store.get('sites', site)
    members, excluded = [], []
    for row in store.all('sites'):
        reasons = []
        if row['service_scope'] != selected['service_scope']:
            reasons.append('服务范围不同')
        if row['care_group'] != selected['care_group']:
            reasons.append('照护组不同')
        if reasons:
            excluded.append({'site': row['id'], 'reasons': reasons})
        else:
            members.append(row['id'])
    return {
        'site': site,
        'service_scope': selected['service_scope'],
        'care_group': selected['care_group'],
        'members': sorted(members),
        'excluded': sorted(excluded, key=lambda item: item['site']),
    }


def available_months(store):
    months = set()
    for row in store.all('inputs'):
        months.add(row['month'])
    for row in store.all('outcomes'):
        months.add(row['month'])
    return sorted(months)


def _requested_months(store, months):
    known = set(available_months(store))
    if months is None:
        return sorted(known)
    if isinstance(months, str):
        requested = [item.strip() for item in months.split(',') if item.strip()]
    elif isinstance(months, (list, tuple)):
        requested = [str(item).strip() for item in months if str(item).strip()]
    else:
        raise ValueError('统计期间格式不合法')
    if not requested:
        raise ValueError('统计期间不能为空')
    unknown = [item for item in requested if item not in known]
    if unknown:
        raise ValueError('统计期间缺少资料: ' + ','.join(unknown))
    return requested


def _site_inputs(store, site, months):
    values = {resource: 0.0 for resource in INPUT_RESOURCES}
    for row in _month_rows(store.all('inputs'), months):
        if row['site'] != site:
            continue
        if row['amount'] < 0:
            raise ValueError('资源投入不能为负')
        values[row['resource']] = values.get(row['resource'], 0.0) + row['amount']
    for resource, amount in _shared_totals(store, months).get(site, {}).items():
        values[resource] = values.get(resource, 0.0) + amount
    return values


def _shared_totals(store, months):
    """共享投入在集团账上只发生一次，这里按守恒比例分摊后再按期汇总。"""
    result = {}
    for row in _month_rows(store.all('shared'), months):
        total = sum(row['shares'].values())
        if abs(total - 1) > 1e-9 or any(value < 0 for value in row['shares'].values()):
            raise ValueError('共享分摊比例不守恒')
        for site, share in row['shares'].items():
            values = result.setdefault(site, {})
            values[row['resource']] = values.get(row['resource'], 0.0) + row['amount'] * share
    return result


def _site_outcomes(store, site, months):
    rows = _month_rows(store.all('outcomes'), months)
    selected = [row for row in rows if row['site'] == site]
    for row in selected:
        if row['covered'] < 0 or row['unfinished'] < 0 or row['complaints'] < 0:
            raise ValueError('业务数量不能为负')
        if row['satisfaction'] is not None and not 1 <= row['satisfaction'] <= 5:
            raise ValueError('满意程度不合法')
    totals = {name: 0.0 for name in COUNT_OUTPUTS}
    observed_satisfaction = []
    for row in selected:
        for name in COUNT_OUTPUTS:
            totals[name] += row[name]
        if row['satisfaction'] is not None:
            observed_satisfaction.append(row['satisfaction'])
    satisfaction = sum(observed_satisfaction) / len(observed_satisfaction) if observed_satisfaction else None
    totals['satisfaction'] = satisfaction
    totals['satisfaction_observed_months'] = len(observed_satisfaction)
    return totals


def build_panel(store, site, months=None, include_satisfaction=True):
    """构造口径一致的比较面板。

    include_satisfaction=False 时使用去掉满意度的缩减口径，使满意度全部缺测的
    院区仍能参与比较；全口径下面板会把缺测院区排除并写明原因。
    """
    scope = comparison_scope(store, site)
    window = _requested_months(store, months)
    window_set = set(window)
    outcome_months = {}
    for row in store.all('outcomes'):
        if row['month'] in window_set:
            outcome_months.setdefault(row['site'], set()).add(row['month'])
    input_months = {}
    for row in store.all('inputs'):
        if row['month'] in window_set:
            input_months.setdefault(row['site'], set()).add(row['month'])

    entries, excluded = [], []
    for member in scope['members']:
        missing_outcome = sorted(window_set - outcome_months.get(member, set()))
        missing_input = sorted(window_set - input_months.get(member, set()))
        if missing_outcome or missing_input:
            excluded.append({
                'site': member,
                'reasons': ['统计期间与参照窗口不一致'],
                'missing_outcome_months': missing_outcome,
                'missing_input_months': missing_input,
            })
            continue
        outcomes = _site_outcomes(store, member, window)
        satisfaction_missing = outcomes['satisfaction'] is None
        if include_satisfaction and satisfaction_missing:
            excluded.append({
                'site': member,
                'reasons': ['满意程度在窗口内全部缺测，不能按零处理'],
                'missing_outcome_months': [],
                'missing_input_months': [],
                'satisfaction_observed_months': 0,
            })
            continue
        entry = {
            'site': member,
            'service_scope': scope['service_scope'],
            'care_group': scope['care_group'],
            'inputs': _site_inputs(store, member, window),
            'covered': outcomes['covered'],
            'unfinished': outcomes['unfinished'],
            'complaints': outcomes['complaints'],
            'satisfaction': outcomes['satisfaction'] if include_satisfaction else None,
            'satisfaction_observed_months': outcomes['satisfaction_observed_months'],
            'window_months': len(window),
        }
        entries.append(entry)

    return {
        'site': site,
        'months': window,
        'caliber': 'full' if include_satisfaction else 'reduced_without_satisfaction',
        'service_scope': scope['service_scope'],
        'care_group': scope['care_group'],
        'input_resources': list(INPUT_RESOURCES),
        'desirable_outputs': list(DESIRABLE_OUTPUTS) if include_satisfaction else ['covered'],
        'undesirable_outputs': list(UNDESIRABLE_OUTPUTS),
        'members': [entry['site'] for entry in entries],
        'entries': entries,
        'excluded_outside_scope': scope['excluded'],
        'excluded_within_scope': excluded,
    }
