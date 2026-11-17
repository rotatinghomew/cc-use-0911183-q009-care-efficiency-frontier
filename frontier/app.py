from pathlib import Path
import json

from .resources import *
from .outcomes import *
from .improvements import *


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
        if method == 'POST' and route == 'project':
            return {'revision': save_project(self.store, payload)}
        if method == 'POST' and route == 'project-quote':
            return quote_projects(self.store, payload['projects'])
        raise KeyError('接口不存在')
