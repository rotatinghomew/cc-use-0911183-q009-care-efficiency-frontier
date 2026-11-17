def observed_outcomes(store, month):
    rows = [row for row in store.all('outcomes') if row['month'] == month]
    for row in rows:
        if row['covered'] < 0 or row['unfinished'] < 0 or row['complaints'] < 0:
            raise ValueError('业务数量不能为负')
        if row['satisfaction'] is not None and not 1 <= row['satisfaction'] <= 5:
            raise ValueError('满意程度不合法')
    return rows


def comparable_sites(store, site):
    selected = store.get('sites', site)
    return sorted(row['id'] for row in store.all('sites') if row['service_scope'] == selected['service_scope'] and row['care_group'] == selected['care_group'])
