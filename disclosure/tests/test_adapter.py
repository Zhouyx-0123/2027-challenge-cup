from io import BytesIO
import unittest
import pandas as pd
from openpyxl import load_workbook
from docx import Document
from disclosure import core

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.data, self.config = core.demo(), core.defaults()

    def test_sample_regression(self):
        r=core.calculate(self.data,self.config)
        for key,value in [('emission',622.22),('distance',1320),('allowance',718.74),('gap',0),('shadow',60.3384),('low',7.39256),('high',13.72904)]:
            self.assertAlmostEqual(r[key],value)
        self.assertAlmostEqual(r['scenarios'][3]['情景金额'],3.416)

    def test_netting_and_yellow_exclusion(self):
        data=self.data.iloc[:2].copy()
        data[core.QUALITY]='绿框'
        data[core.DISTANCE]=100
        data[core.EMISSION]=[100,0]
        self.assertEqual(core.calculate(data,self.config)['gap'],0)
        data.loc[data.index[1],core.QUALITY]='黄框'
        data.loc[data.index[1],core.EMISSION]=1000000
        self.assertAlmostEqual(core.calculate(data,self.config)['gap'],45.55)

    def test_reject_invalid_and_demote_unknown(self):
        for invalid in [None,-1,float('inf'),'bad']:
            data=self.data.copy();data[core.EMISSION]=data[core.EMISSION].astype(object);data.loc[0,core.EMISSION]=invalid
            with self.assertRaises(ValueError): core.validate(data)
        self.data.loc[0,core.QUALITY]='未知'
        r=core.calculate(self.data,self.config)
        self.assertEqual(len(r['green']),5)
        self.assertTrue(r['notes'])
        self.data.loc[0,core.QUALITY]='绿框黄框'
        with self.assertRaises(ValueError): core.validate(self.data)

    def test_export_engine_and_text_consistency(self):
        files=core.export(self.data,self.config)
        wb=load_workbook(BytesIO(next(v for k,v in files.items() if k.endswith('.xlsx'))),data_only=True)
        self.assertEqual(wb.sheetnames,['00_参数','01_原始数据','02_配额缺口模拟','03_影子碳成本','04_披露文本生成'])
        doc=Document(BytesIO(next(v for k,v in files.items() if k.endswith('.docx'))))
        paragraphs='\n'.join(p.text for p in doc.paragraphs)
        for row in range(2,6):
            text=str(wb['04_披露文本生成'].cell(row,2).value)
            self.assertIn(text,paragraphs)

if __name__=='__main__': unittest.main()
