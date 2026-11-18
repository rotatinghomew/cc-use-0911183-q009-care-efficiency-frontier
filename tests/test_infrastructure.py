import json
import tempfile
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from threading import Thread

from frontier.app import Application
from frontier.store import Conflict, Store
from frontier.web import csv_text, handler_for, load_dataset


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = str(Path(self.folder.name) / 'state.sqlite3')
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_restart_retains_record(self):
        self.store.put('example', {'id': 'a', 'value': '中文'})
        other = Store(self.path)
        try:
            self.assertEqual(other.get('example', 'a')['value'], '中文')
        finally:
            other.close()

    def test_atomic_batch_conflict(self):
        self.store.put_many('example', [{'id': 'a'}, {'id': 'b'}])
        with self.assertRaises(Conflict):
            self.store.put_many('example', [{'id': 'a', 'value': 1}, {'id': 'b', 'value': 2}], {'a': 1, 'b': 0})
        self.assertNotIn('value', self.store.get('example', 'a'))
        self.assertEqual(self.store.revision('example', 'a'), 1)

    def test_duplicate_batch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.put_many('example', [{'id': 'a'}, {'id': 'a'}])
        self.assertEqual(self.store.all('example'), [])

    def test_csv_retains_quoted_values(self):
        text = csv_text([{'name': '甲,乙', 'value': 2}], ['name', 'value'])
        self.assertIn('"甲,乙"', text)

    def test_dataset_import_preserves_chinese_and_revisions(self):
        path = Path(self.folder.name) / 'input.json'
        path.write_text(json.dumps({'example': [{'id': 'a', 'value': '甲'}]}))
        self.assertEqual(load_dataset(self.store, path)['records'], 1)
        load_dataset(self.store, path)
        self.assertEqual(self.store.revision('example', 'a'), 2)

    def test_dataset_validation_prevents_partial_import(self):
        path = Path(self.folder.name) / 'invalid.json'
        path.write_text(json.dumps({'good': [{'id': 'a'}], 'bad': [{'value': 1}]}))
        with self.assertRaises(ValueError):
            load_dataset(self.store, path)
        self.assertEqual(self.store.all('good'), [])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.application = Application(self.store)
        self.application.seed()
        self.server = HTTPServer(('127.0.0.1', 0), handler_for(self.application))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.store.close()

    def test_health_and_existing_summary(self):
        with urllib.request.urlopen(self.base + '/health') as response:
            self.assertEqual(json.load(response), {'status': 'ok'})
        with urllib.request.urlopen(self.base + '/api/summary') as response:
            self.assertTrue(json.load(response))

    def test_unknown_endpoint(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.base + '/api/unknown')
        self.assertEqual(caught.exception.code, 404)

    def test_malformed_body_does_not_change_state(self):
        before = self.application.demo()
        request = urllib.request.Request(self.base + '/api/records', data=b'[1]', headers={'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.application.demo(), before)

    def test_frontier_endpoint_returns_real_peers_and_exclusions(self):
        with urllib.request.urlopen(self.base + '/api/frontier?site=east') as response:
            payload = json.load(response)
        self.assertEqual(payload['members'], ['east', 'west'])
        self.assertEqual(payload['excluded_outside_scope'][0]['site'], 'day')
        self.assertTrue(payload['results'])

    def test_reduced_caliber_includes_missing_satisfaction_site(self):
        with urllib.request.urlopen(self.base + '/api/frontier?site=day&caliber=reduced') as response:
            payload = json.load(response)
        self.assertEqual(payload['members'], ['day'])
        self.assertEqual(payload['caliber'], 'reduced_without_satisfaction')

    def test_attainable_endpoint_reports_skill_gate(self):
        with urllib.request.urlopen(self.base + '/api/attainable?site=east&month=2026-11') as response:
            payload = json.load(response)
        east = next(item for item in payload['projects'] if item['id'] == 'unit_east')
        self.assertFalse(east['skill_feasible'])

    def test_freeze_and_review_plan_over_http(self):
        self.store.put('availability', {
            'id': '2026-10', 'specialist_hours': 60, 'care_hours': 200,
            'available_skills': ['basic', 'bathing']})
        body = json.dumps({'id': 'http-plan', 'site': 'east', 'month': '2026-10',
                           'projects': ['unit_east']}).encode('utf-8')
        request = urllib.request.Request(self.base + '/api/plan', data=body,
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)
            frozen = json.load(response)
        self.assertTrue(frozen['frozen_panel_hash'])
        with urllib.request.urlopen(self.base + '/api/plan-review?id=http-plan') as response:
            review = json.load(response)
        self.assertTrue(review['snapshot_integrity']['frozen_hash_matches_snapshot'])

    def test_unknown_site_is_404_and_bad_window_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.base + '/api/frontier?site=missing')
        self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.base + '/api/frontier?site=east&months=2030-01')
        self.assertEqual(caught.exception.code, 400)
