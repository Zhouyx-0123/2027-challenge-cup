"""验证情景逻辑及绿黄隔离；不修改输入或生成结果。"""
import unittest
from pathlib import Path
import pandas as pd
import tempfile
import json
import main
import scenario
from rules import TIER_GREEN, TIER_YELLOW


class ScenarioTests(unittest.TestCase):
    def test_net_before_positive(self):
        allowances = [scenario.allowance(100, .55) for _ in range(2)]
        emissions = [79, 29]
        self.assertGreater(sum(max(0, e-a) for e,a in zip(emissions,allowances)), 0)
        self.assertEqual(scenario.balance(sum(emissions),sum(allowances),100)['gap'], 0)

    def test_positive_net_and_tonne_conversion(self):
        result = scenario.balance(110,scenario.allowance(200,.55),100)
        self.assertAlmostEqual(result['gap'],1.1)
        self.assertAlmostEqual(result['cost'],.11)

    def test_invalid_allowance_inputs(self):
        for args in [(-1,.55,.99),(1,0,.99),(1,.55,0),(1,.55,1.01),
                     (float('nan'),.55,.99),(1,float('inf'),.99)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                scenario.allowance(*args)
        self.assertEqual(scenario.allowance(0,.55),0)

    def test_invalid_balances(self):
        for args in [(-1,2,100),(1,-2,100),(1,2,0),(1,2,float('inf'))]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                scenario.balance(*args)

    def test_yellow_excluded_even_with_large_emissions(self):
        data = pd.DataFrame({'数据层级标签':[TIER_GREEN,TIER_YELLOW],
                             '行驶里程(km)':[100,999],
                             '绝对碳排(kgCO₂e)':[60,1e9]})
        gap=main.build_gap_frame(data,dict(main.DEFAULT_CONFIG))
        self.assertEqual(len(gap),1)
        self.assertAlmostEqual(gap['情景额度(kg)'].sum(),54.45)
        self.assertAlmostEqual(scenario.balance(gap['绝对碳排(kgCO₂e)'].sum(),
                                               gap['情景额度(kg)'].sum(),100)['gap'],5.55)

    def test_sample_and_stress(self):
        cfg=main.load_config(Path(__file__).with_name('config.yaml'))
        fixture=json.loads((Path(__file__).parents[1]/'fixtures/demo.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'input_data.xlsx'
            pd.DataFrame(fixture['rows'],columns=fixture['columns']).to_excel(path,index=False,sheet_name='原始数据')
            data=main.attach_tier(main.read_input(path))
        gap=main.build_gap_frame(data,cfg)
        stats=main.summarize(gap,main.build_shadow_frame(data,cfg),cfg)
        self.assertEqual((stats['n_green'],stats['n_yellow']),(6,4))
        self.assertAlmostEqual(stats['scenario_allowance'],718.74)
        self.assertAlmostEqual(stats['scenario_delta'],-96.52)
        self.assertEqual((stats['gap_kg'],stats['gap_cost']),(0,0))
        self.assertEqual((stats['shadow_total'],stats['shadow_green'],stats['shadow_yellow']),
                         (60.34,49.78,10.56))
        self.assertEqual((stats['shadow_low'],stats['shadow_high']),(7.39,13.73))
        for intensity,expected_gap,expected_cost in [(.55,0,0),(.52,0,0),(.50,0,0),(.45,34.16,3.416)]:
            with self.subTest(intensity=intensity):
                a=scenario.allowance(stats['distance_green'],intensity,cfg['scenario_control_factor'])
                result=scenario.balance(stats['emission_green'],a,cfg['gap_carbon_price'])
                self.assertAlmostEqual(result['gap'],expected_gap)
                self.assertAlmostEqual(result['cost'],expected_cost)


if __name__ == '__main__':
    unittest.main()
