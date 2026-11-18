from pathlib import Path
import json

from .resources import resource_totals, shared_allocations
from .outcomes import observed_outcomes, comparable_sites
from .improvements import (
    feasible_combinations,
    freeze_plan,
    quote_projects,
    review_plan,
    save_project,
)
from .efficiency import group_frontier
from .stability import correction_sensitivity, leave_one_out


class Application:
    def __init__(self, store):
        self.store = store

    def seed(self):
        directory = Path(__file__).resolve().parent.parent / 'fixtures'
        for path in sorted(directory.glob('*.json')):
            if not self.store.all(path.stem):
                self.store.put_many(path.stem, json.loads(path.read_text(encoding='utf-8')))

    def demo(self):
        return {'inputs': resource_totals(self.store, '2026-10'), 'shared': shared_allocations(self.store, '2026-10'), 'outcomes': observed_outcomes(self.store, '2026-10'), 'quote': quote_projects(self.store, ['unit_east'])}

    def check(self):
        value = self.demo()
        return {'status': 'ok', 'summary': value}

    def call(self, method, route, payload):
        if method == 'GET' and route == 'summary':
            return self.demo()
        if method == 'GET' and route == 'peers':
            return comparable_sites(self.store, payload['site'])
        if method == 'GET' and route == 'frontier':
            return group_frontier(
                self.store,
                payload['site'],
                months=payload.get('months'),
                include_satisfaction=payload.get('caliber', 'full') != 'reduced',
            )
        if method == 'GET' and route == 'stability':
            return leave_one_out(
                self.store,
                payload['site'],
                months=payload.get('months'),
                include_satisfaction=payload.get('caliber', 'full') != 'reduced',
            )
        if method == 'POST' and route == 'correction-sensitivity':
            return correction_sensitivity(
                self.store,
                payload['site'],
                payload['corrections'],
                months=payload.get('months'),
                include_satisfaction=payload.get('include_satisfaction', True),
            )
        if method == 'GET' and route == 'attainable':
            return feasible_combinations(self.store, payload['site'], payload['month'])
        if method == 'POST' and route == 'project':
            return {'revision': save_project(self.store, payload)}
        if method == 'POST' and route == 'project-quote':
            return quote_projects(self.store, payload['projects'])
        if method == 'POST' and route == 'plan':
            return freeze_plan(self.store, payload)
        if method == 'GET' and route == 'plan-review':
            return review_plan(self.store, payload['id'], actual=payload.get('actual'))
        raise KeyError('接口不存在')
