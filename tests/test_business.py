import unittest
from frontier.app import Application
from frontier.store import Store
from frontier.resources import resource_totals, shared_allocations
from frontier.outcomes import observed_outcomes, comparable_sites
from frontier.improvements import quote_projects, save_project


class FrontierTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        Application(self.store).seed()

    def tearDown(self):
        self.store.close()

    def test_shared_input_conserved(self):
        self.assertEqual(sum(row['specialist_hours'] for row in shared_allocations(self.store, '2026-10').values()), 100)

    def test_bad_shares_rejected(self):
        self.store.put('shared', dict(self.store.get('shared', 's1'), shares={'east': 0.8, 'west': 0.8}))
        with self.assertRaises(ValueError):
            shared_allocations(self.store, '2026-10')

    def test_scope_changes_comparators(self):
        self.assertEqual(comparable_sites(self.store, 'east'), ['east', 'west'])
        self.assertEqual(comparable_sites(self.store, 'day'), ['day'])

    def test_input_month_filter(self):
        self.assertEqual(resource_totals(self.store, '2027-01'), {})

    def test_missing_satisfaction_preserved(self):
        row = next(row for row in observed_outcomes(self.store, '2026-10') if row['site'] == 'day')
        self.assertIsNone(row['satisfaction'])

    def test_negative_outcome_rejected(self):
        self.store.put('outcomes', dict(self.store.get('outcomes', 'o1'), unfinished=-1))
        with self.assertRaises(ValueError):
            observed_outcomes(self.store, '2026-10')

    def test_project_quote_keeps_shared_needs(self):
        self.assertEqual(quote_projects(self.store, ['unit_east', 'unit_west'])['resource_need']['specialist_hours'], 65)

    def test_project_not_fractionally_repeated(self):
        with self.assertRaises(ValueError):
            quote_projects(self.store, ['unit_east', 'unit_east'])

    def test_unknown_project_site(self):
        row = dict(self.store.get('projects', 'unit_east'), site='missing')
        with self.assertRaises(KeyError):
            save_project(self.store, row)

    def test_empty_project_quote(self):
        self.assertEqual(quote_projects(self.store, [])['fixed_cents'], 0)
