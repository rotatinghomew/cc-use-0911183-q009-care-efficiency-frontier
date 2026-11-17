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
