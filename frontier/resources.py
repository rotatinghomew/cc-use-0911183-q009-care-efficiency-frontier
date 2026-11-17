def resource_totals(store, month):
    totals = {}
    for row in store.all('inputs'):
        if row['month'] == month:
            if row['amount'] < 0:
                raise ValueError('资源投入不能为负')
            values = totals.setdefault(row['site'], {})
            values[row['resource']] = values.get(row['resource'], 0) + row['amount']
    return totals


def shared_allocations(store, month):
    result = {}
    for row in store.all('shared'):
        if row['month'] != month:
            continue
        if abs(sum(row['shares'].values()) - 1) > 1e-9 or any(value < 0 for value in row['shares'].values()):
            raise ValueError('共享分摊比例不守恒')
        for site, share in row['shares'].items():
            values = result.setdefault(site, {})
            values[row['resource']] = values.get(row['resource'], 0) + row['amount'] * share
    return result
