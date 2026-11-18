import unittest

from frontier.app import Application
from frontier.benchmark import build_panel, comparison_scope
from frontier.efficiency import group_frontier
from frontier.improvements import feasible_combinations, freeze_plan, review_plan
from frontier.stability import correction_sensitivity, leave_one_out
from frontier.store import Store


class PanelStore(Store):
    def __init__(self):
        super().__init__(':memory:')
        Application(self).seed()

    def site(self, sid, scope='residential', group='mixed'):
        return {'id': sid, 'service_scope': scope, 'care_group': group}

    def input_row(self, iid, sid, resource, amount, month='2026-10'):
        return {'id': iid, 'site': sid, 'month': month, 'resource': resource, 'amount': amount}

    def outcome_row(self, oid, sid, covered, unfinished, complaints, satisfaction, month='2026-10'):
        return {'id': oid, 'site': sid, 'month': month, 'covered': covered,
                'unfinished': unfinished, 'complaints': complaints, 'satisfaction': satisfaction}

    def panel_sites(self, specs):
        self.put_many('sites', [self.site(sid) for sid in specs])

    def panel_data(self, table, rows):
        self.put_many(table, rows)


class CaliberTests(unittest.TestCase):
    def setUp(self):
        self.store = PanelStore()

    def tearDown(self):
        self.store.close()

    def test_scope_explains_non_comparable_sites(self):
        scope = comparison_scope(self.store, 'east')
        self.assertEqual(scope['members'], ['east', 'west'])
        self.assertEqual(scope['excluded'], [{'site': 'day', 'reasons': ['服务范围不同', '照护组不同']}])

    def test_missing_satisfaction_is_not_zero(self):
        panel = build_panel(self.store, 'day')
        self.assertEqual(panel['members'], [])
        self.assertEqual(panel['excluded_within_scope'][0]['reasons'],
                         ['满意程度在窗口内全部缺测，不能按零处理'])
        reduced = build_panel(self.store, 'day', include_satisfaction=False)
        self.assertEqual(reduced['members'], ['day'])
        self.assertIsNone(reduced['entries'][0]['satisfaction'])
        self.assertEqual(reduced['caliber'], 'reduced_without_satisfaction')

    def test_different_statistical_windows_are_not_mixed(self):
        self.store.panel_sites(['a', 'b'])
        self.store.panel_data('inputs', [
            self.store.input_row('ia1', 'a', 'care_hours', 100),
            self.store.input_row('ib1', 'b', 'care_hours', 100, month='2026-09'),
        ])
        self.store.panel_data('outcomes', [
            self.store.outcome_row('oa1', 'a', 50, 1, 0, 4.0),
            self.store.outcome_row('ob1', 'b', 50, 1, 0, 4.0, month='2026-09'),
        ])
        panel = build_panel(self.store, 'a', months=['2026-10'])
        self.assertIn('a', panel['members'])
        self.assertNotIn('b', panel['members'])
        exclusion = next(item for item in panel['excluded_within_scope'] if item['site'] == 'b')
        self.assertEqual(exclusion['reasons'], ['统计期间与参照窗口不一致'])

    def test_unknown_window_rejected(self):
        with self.assertRaises(ValueError):
            build_panel(self.store, 'east', months=['2030-01'])

    def test_shared_allocation_conserved_in_panel(self):
        panel = build_panel(self.store, 'east')
        specialist = sum(entry['inputs']['specialist_hours'] for entry in panel['entries'])
        self.assertAlmostEqual(specialist, 100.0)


class FrontierTests(unittest.TestCase):
    def setUp(self):
        self.store = PanelStore()
        self.store.panel_sites(['a', 'b', 'c'])
        # b 是标杆：相同好产出，一半投入，坏产出为零；c 与 a 同构但满意度缺测
        self.store.panel_data('inputs', [
            self.store.input_row('ia', 'a', 'care_hours', 100),
            self.store.input_row('ib', 'b', 'care_hours', 50),
            self.store.input_row('ic', 'c', 'care_hours', 100),
        ])
        self.store.panel_data('outcomes', [
            self.store.outcome_row('oa', 'a', 100, 10, 5, 4.0),
            self.store.outcome_row('ob', 'b', 100, 2, 1, 4.0),
            self.store.outcome_row('oc', 'c', 100, 10, 5, None),
        ])

    def tearDown(self):
        self.store.close()

    def test_efficiency_score_and_real_peer(self):
        result = group_frontier(self.store, 'a')
        rows = {row['site']: row for row in result['results']}
        self.assertEqual(rows['b']['is_efficient'], True)
        self.assertAlmostEqual(rows['a']['efficiency_score'], 0.5, places=6)
        self.assertEqual([peer['site'] for peer in rows['a']['peers']], ['b'])
        self.assertEqual(rows['a']['statistical_reference']['kind'], 'virtual_convex_target')
        self.assertIn('不是真实存在的混合院区', rows['a']['statistical_reference']['note'])

    def test_undesirable_outputs_treated_as_inputs_with_zero_safety(self):
        store = PanelStore()
        try:
            store.panel_sites(['z1', 'z2'])
            store.panel_data('inputs', [
                store.input_row('iz1', 'z1', 'care_hours', 80),
                store.input_row('iz2', 'z2', 'care_hours', 100),
            ])
            store.panel_data('outcomes', [
                store.outcome_row('oz1', 'z1', 60, 0, 0, 4.0),
                store.outcome_row('oz2', 'z2', 60, 4, 2, 4.0),
            ])
            result = group_frontier(store, 'z1')
            row = next(item for item in result['results'] if item['site'] == 'z1')
            bad_metrics = [gap['metric'] for gap in row['input_side_gaps']
                           if gap['metric'] in ('unfinished', 'complaints')]
            self.assertEqual(bad_metrics, ['unfinished', 'complaints'])
            # 零坏产出不产生除零错误，相对差距回退为 None 而非 NaN
            gap = next(item for item in row['input_side_gaps'] if item['metric'] == 'unfinished')
            self.assertIsNone(gap['relative_gap'])
        finally:
            store.close()

    def test_peer_demonstrates_real_capability(self):
        result = group_frontier(self.store, 'a')
        a_row = next(row for row in result['results'] if row['site'] == 'a')
        strengths = {item['metric']: item for item in a_row['peers'][0]['demonstrated_strengths']}
        self.assertIn('care_hours', strengths)
        self.assertIn('unfinished', strengths)
        self.assertEqual(strengths['unfinished']['direction'], '更少的坏产出')

    def test_null_satisfaction_excluded_full_but_included_reduced(self):
        full = group_frontier(self.store, 'a')
        self.assertIn('a', full['members'])
        self.assertIn('b', full['members'])
        self.assertNotIn('c', full['members'])
        reduced = group_frontier(self.store, 'a', include_satisfaction=False)
        self.assertIn('c', reduced['members'])
        self.assertEqual(reduced['desirable_outputs'], ['covered'])
        c_row = next(row for row in reduced['results'] if row['site'] == 'c')
        self.assertAlmostEqual(c_row['efficiency_score'], 0.5, places=6)

    def test_single_member_group_returns_diagnostics_not_frontier(self):
        store = PanelStore()
        try:
            result = group_frontier(store, 'day')
            self.assertEqual(result['results'], [])
            self.assertTrue(result['diagnostics']['small_sample'])
            self.assertIn('无法构造边界', result['diagnostics']['notes'][0])
        finally:
            store.close()

    def test_super_efficiency_flags_extreme_peer(self):
        result = group_frontier(self.store, 'b')
        b_row = next(row for row in result['results'] if row['site'] == 'b')
        self.assertIsNotNone(b_row['super_efficiency'])
        self.assertGreaterEqual(b_row['super_efficiency'], 1.5)
        self.assertIn('b', result['diagnostics']['extreme_peer_sites'])


class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.store = PanelStore()
        self.store.panel_sites(['a', 'b', 'c'])
        self.store.panel_data('inputs', [
            self.store.input_row('ia', 'a', 'care_hours', 100),
            self.store.input_row('ib', 'b', 'care_hours', 50),
            self.store.input_row('ic', 'c', 'care_hours', 55),
        ])
        self.store.panel_data('outcomes', [
            self.store.outcome_row('oa', 'a', 100, 10, 5, 4.0),
            self.store.outcome_row('ob', 'b', 100, 0, 0, 4.0),
            self.store.outcome_row('oc', 'c', 100, 1, 1, 4.0),
        ])

    def tearDown(self):
        self.store.close()

    def test_leave_one_out_recomputes_reference(self):
        stability = leave_one_out(self.store, 'a')
        self.assertEqual(stability['baseline']['peer_sites'], ['b'])
        removed_b = next(run for run in stability['runs'] if run['removed_site'] == 'b')
        self.assertTrue(removed_b['feasible'])
        self.assertEqual(removed_b['peer_sites'], ['c'])
        self.assertTrue(removed_b['peer_set_changed'])
        self.assertEqual(removed_b['lost_peer_support'], ['b'])

    def test_correction_sensitivity_compares_before_after(self):
        result = correction_sensitivity(
            self.store, 'a', [{'site': 'b', 'resource': 'care_hours', 'delta': 200}])
        self.assertIsNotNone(result['after'])
        self.assertIn('参照院区集合发生变化', result['sensitivity_notes'])
        # 临时更正不写库
        panel = build_panel(self.store, 'a')
        b_entry = next(entry for entry in panel['entries'] if entry['site'] == 'b')
        self.assertEqual(b_entry['inputs']['care_hours'], 50.0)

    def test_negative_correction_rejected(self):
        with self.assertRaises(ValueError):
            correction_sensitivity(
                self.store, 'a', [{'site': 'b', 'resource': 'care_hours', 'delta': -999}])


class AttainableCombinationTests(unittest.TestCase):
    def setUp(self):
        self.store = PanelStore()
        self.store.put('availability', {
            'id': '2026-11', 'specialist_hours': 50, 'care_hours': 200,
            'available_skills': ['basic']})

    def tearDown(self):
        self.store.close()

    def test_indivisible_projects_are_zero_one(self):
        result = feasible_combinations(self.store, 'east', '2026-11')
        for combo in result['feasible_combinations']:
            self.assertEqual(len(combo['projects']), len(set(combo['projects'])))
        # bathing 资质本期不可获得，unit_east 被排除
        east = next(item for item in result['projects'] if item['id'] == 'unit_east')
        self.assertFalse(east['skill_feasible'])
        self.assertIn('资质不可获得', east['ineligible_reason'])
        west = next(item for item in result['projects'] if item['id'] == 'unit_west')
        self.assertTrue(west['skill_feasible'])

    def test_cross_site_conflict_explains_resource_tradeoff(self):
        # 把资源池压到两个项目不能同时启用
        self.store.put('availability', {
            'id': '2026-11', 'specialist_hours': 40, 'care_hours': 200,
            'available_skills': ['basic', 'bathing']})
        result = feasible_combinations(self.store, 'east', '2026-11')
        conflict_resources = {item['resource'] for item in result['cross_site_conflicts']}
        self.assertIn('specialist_hours', conflict_resources)
        joint = [combo for combo in result['feasible_combinations']
                 if set(combo['projects']) == {'unit_east', 'unit_west'}]
        self.assertEqual(joint, [])
        singles = {tuple(combo['projects']) for combo in result['feasible_combinations'] if combo['projects']}
        self.assertEqual(singles, {('unit_east',), ('unit_west',)})

    def test_missing_period_availability_is_404_style_error(self):
        with self.assertRaises(KeyError):
            feasible_combinations(self.store, 'east', '2099-01')


class FrozenPlanTests(unittest.TestCase):
    def setUp(self):
        self.store = PanelStore()
        self.store.put('availability', {
            'id': '2026-10', 'specialist_hours': 60, 'care_hours': 200,
            'available_skills': ['basic', 'bathing']})

    def tearDown(self):
        self.store.close()

    def test_freeze_captures_boundary_and_window(self):
        frozen = freeze_plan(self.store, {
            'id': 'p1', 'site': 'east', 'month': '2026-10', 'projects': ['unit_east']})
        self.assertEqual(frozen['revision'], 1)
        review = review_plan(self.store, 'p1')
        self.assertEqual(review['window_months'], ['2026-10'])
        self.assertIsInstance(review['frozen_reference']['peer_sites'], list)
        self.assertTrue(review['snapshot_integrity']['frozen_hash_matches_snapshot'])

    def test_plan_not_rewritten_by_later_sample_or_corrections(self):
        freeze_plan(self.store, {
            'id': 'p1', 'site': 'east', 'month': '2026-10', 'projects': ['unit_east']})
        before = self.store.get('plans', 'p1')
        self.store.put('sites', {'id': 'north', 'service_scope': 'residential', 'care_group': 'mixed'})
        self.store.put('inputs', dict(self.store.get('inputs', 'i1'), amount=2400))
        after = self.store.get('plans', 'p1')
        self.assertEqual(before, after)
        review = review_plan(self.store, 'p1', actual={'coverage_gain': 9, 'projects': ['unit_east']})
        self.assertEqual(review['review']['target_coverage_gain'], 12)
        self.assertAlmostEqual(review['review']['attainment_ratio'], 0.75)

    def test_freeze_rejects_infeasible_skill_and_pool(self):
        self.store.put('availability', {
            'id': '2026-12', 'specialist_hours': 60, 'care_hours': 200,
            'available_skills': ['basic']})
        with self.assertRaises(ValueError):
            freeze_plan(self.store, {
                'id': 'p2', 'site': 'east', 'month': '2026-12', 'projects': ['unit_east']})
        self.store.put('availability', {
            'id': '2026-12', 'specialist_hours': 10, 'care_hours': 200,
            'available_skills': ['basic', 'bathing']})
        with self.assertRaises(ValueError):
            freeze_plan(self.store, {
                'id': 'p3', 'site': 'east', 'month': '2026-12', 'projects': ['unit_east']})

    def test_statistical_reference_distinct_from_executable_combination(self):
        freeze_plan(self.store, {
            'id': 'p1', 'site': 'east', 'month': '2026-10', 'projects': ['unit_east']})
        plan = self.store.get('plans', 'p1')
        self.assertEqual(plan['projects'], ['unit_east'])
        self.assertEqual(plan['statistical_reference_at_freeze']['statistical_target']['kind'],
                         'virtual_convex_target')
        self.assertIn('不是可执行组合', plan['attainability_note'])


if __name__ == '__main__':
    unittest.main()
