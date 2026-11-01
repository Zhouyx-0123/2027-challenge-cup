import copy, importlib.util, json, shutil, sys, tempfile, unittest
from pathlib import Path
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import invoice_to_master as imp
import finance_master_interface as api
BASE={'invoice_no':'00012345678901234567890','invoice_date':'2026-10-25','item_name':'柴油','invoice_amount':113,'amount_excluding_tax':100,'tax_amount':13,'quantity':10,'unit':'L','invoice_type':'电子发票（普通发票）','tax_rate':'13%','mock':True}

class RegisterTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'main.xlsx'
  imp.write_master(self.path,[BASE],'simulated');self.record=api.export_master(self.path,'simulated')['records'][0];self.rid=self.record['record_id']
 def envelope(self,kind,values):
  return dict(schema_version=api.SCHEMA,source_mode='simulated',kind=kind,source='test',reviewer='测试审核人',reviewed_at='2026-10-31T12:00:00+08:00',reason='测试依据',updates=[dict(record_id=self.rid,expected={k:self.record[k] for k in values},values=values)])
 def test_rate_and_tax_direct(self):
  self.assertEqual(self.record['tax_rate'],'0.13');self.assertEqual(self.record['tax_amount'],'13');self.assertEqual(self.record['deduction_status'],'待确认');self.assertIsNone(self.record['deductible_input_tax'])
 def test_rate_missing_not_inferred(self):self.assertIsNone(imp.normalize(dict(BASE,tax_rate=None))['tax_rate'])
 def test_rate_text(self):self.assertEqual(imp.normalize(dict(BASE,tax_rate='免税'))['tax_rate'],'免税')
 def test_rate_ambiguous_rejected(self):self.assertRaises(ValueError,imp.normalize,dict(BASE,tax_rate=13))
 def test_repeat_import(self):
  self.assertEqual(imp.write_master(self.path,[BASE],'simulated')['duplicates'],1);self.assertEqual(len(api.export_master(self.path,'simulated')['records']),1)
 def test_dry_run_existing(self):self.assertEqual(imp.write_master(self.path,[BASE],'simulated',dry_run=True)['duplicates'],1)
 def test_invoice_conflict(self):self.assertEqual(len(imp.write_master(self.path,[dict(BASE,seller_name='changed')],'simulated')['rejected']),1)
 def test_bad_batch_conflict(self):self.assertEqual(imp.prepare([BASE,dict(BASE,seller_name='x')],'simulated','test')[0],[])
 def test_real_mock(self):self.assertEqual(imp.prepare([dict(BASE,mock='True')],'real','test')[0],[])
 def test_mode_mismatch(self):self.assertRaises(ValueError,api.export_master,self.path,'real')
 def test_multi_item(self):self.assertRaises(ValueError,imp.normalize,dict(BASE,items=[{},{}]))
 def test_invalid_money(self):self.assertRaises(ValueError,imp.number,'NaN');self.assertRaises(ValueError,imp.number,'1e1000')
 def test_classification(self):
  for name,result in [('车辆维修费','维修'),('高速通行费','路桥'),('电费','电力'),('柴油及电费','待分类')]:self.assertEqual(imp.category(name),result)
 def test_association_and_idempotence(self):
  e=self.envelope('association',{'activity_id':'ACT-001'});self.assertEqual(api.apply_updates(self.path,e)['updated'],1);self.assertEqual(api.apply_updates(self.path,e)['unchanged'],1)
  self.assertEqual(load_workbook(self.path)['接口回填记录'].max_row,2)
 def test_order_independent(self):
  imp.write_master(self.path,[dict(BASE,invoice_no='OTHER')],'simulated');w=load_workbook(self.path);s=w['财务主表'];a=[c.value for c in s[2]];b=[c.value for c in s[3]]
  for n,vals in [(2,b),(3,a)]:
   for j,v in enumerate(vals,1):s.cell(n,j).value=v
  w.save(self.path);api.apply_updates(self.path,self.envelope('association',{'trip_id':'TRIP-1'}));self.assertEqual(load_workbook(self.path)['财务主表']['U3'].value,'TRIP-1')
 def test_atomic_reject(self):
  before=self.path.read_bytes();e=self.envelope('association',{'activity_id':'A'});e['updates'].append(dict(record_id='MISSING',expected={'activity_id':None},values={'activity_id':'B'}));self.assertRaises(ValueError,api.apply_updates,self.path,e);self.assertEqual(before,self.path.read_bytes())
 def test_old_value_conflict(self):
  e=self.envelope('association',{'activity_id':'A'});e['updates'][0]['expected']['activity_id']='STALE';self.assertRaises(ValueError,api.apply_updates,self.path,e)
 def test_no_invoice_overwrite(self):self.assertRaises(ValueError,api.apply_updates,self.path,self.envelope('deduction',{'tax_amount':'0'}))
 def test_deduction(self):
  e=self.envelope('deduction',{'deduction_status':'不可抵扣','deductible_input_tax':'0.00','deduction_basis':'测试明确不可抵扣'});api.apply_updates(self.path,e);r=api.export_master(self.path,'simulated')['records'][0];self.assertEqual(r['tax_amount'],'13');self.assertEqual(r['deductible_input_tax'],'0')
 def test_deduction_not_auto(self):
  e=self.envelope('deduction',{'deduction_status':'待确认','deductible_input_tax':'13','deduction_basis':None});self.assertRaises(ValueError,api.apply_updates,self.path,e)
 def test_deduction_over_limit(self):
  e=self.envelope('deduction',{'deduction_status':'可部分抵扣','deductible_input_tax':'14','deduction_basis':'x'});self.assertRaises(ValueError,api.apply_updates,self.path,e)
 def test_posting_validation(self):
  e=self.envelope('verification',{'verification_status':'异常待复核','posting_allowed':True,'reviewer':'测试审核人','reviewed_at':'2026-10-31T12:00:00+08:00'});self.assertRaises(ValueError,api.apply_updates,self.path,e)
 def test_verification(self):
  e=self.envelope('verification',{'verification_status':'一致','posting_allowed':True,'reviewer':'测试审核人','reviewed_at':'2026-10-31T12:00:00+08:00'});api.apply_updates(self.path,e);self.assertIs(api.export_master(self.path,'simulated')['records'][0]['posting_allowed'],True)
 def test_cost(self):
  api.apply_updates(self.path,self.envelope('cost',{'confirmed_cost':'100.00'}));self.assertEqual(api.export_master(self.path,'simulated')['records'][0]['confirmed_cost'],'100')
 def test_literal_formula(self):
  api.apply_updates(self.path,self.envelope('association',{'trip_id':'=1+1'}));self.assertEqual(load_workbook(self.path)['财务主表']['U2'].data_type,'s')
 def test_template_preserved(self):
  template=ROOT/'docs/finance/项目财务主表_费用明细版.xlsx'
  shutil.copyfile(template,self.path);before=load_workbook(self.path);report=imp.write_master(self.path,[BASE],'simulated');after=load_workbook(self.path)
  self.assertEqual(report['added'],1);self.assertEqual(after['财务主表']['G5'].value,BASE['invoice_no']);self.assertEqual(after.sheetnames,before.sheetnames)
  self.assertEqual(after['财务主表']['A2'].value,before['财务主表']['A2'].value);self.assertEqual(after['财务主表']['P2'].value,before['财务主表']['P2'].value)
  self.assertEqual(len(after['财务主表'].data_validations.dataValidation),len(before['财务主表'].data_validations.dataValidation))
  self.assertEqual(api.export_master(self.path,'simulated')['records'][-1]['tax_rate'],'0.13')

if __name__=='__main__':unittest.main()
