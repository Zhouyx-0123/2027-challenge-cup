"""Offline contract checks of changed OCR functions; does not test live OCR."""
import ast, csv, re, tempfile, unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
ROOT=Path(__file__).resolve().parents[1]
def load_parts(path,names,env):
 tree=ast.parse(path.read_text(encoding="utf-8"));nodes=[]
 for node in tree.body:
  name=node.name if isinstance(node,(ast.FunctionDef,ast.ClassDef)) else node.target.id if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) else node.targets[0].id if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) else ''
  if name in names:nodes.append(node)
 exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),env)
 return env
class OCRContractTests(unittest.TestCase):
 def test_parser_keeps_printed_tax_fields(self):
  env=load_parts(ROOT/'src/invoice.py',{'EXTRACT_PROMPT','_CURRENCY_RE','_UNIT_RE','_to_float','_to_text','parse_invoice'},dict(re=re,Any=Any,Dict=Dict,List=List,Optional=Optional))
  out=env['parse_invoice']({'invoice_amount':1334,'amount_excluding_tax':1180.53,'tax_amount':153.47,'invoice_type':'电子发票（普通发票）','tax_rate':'13%','quantity':1})
  self.assertEqual(out['tax_rate'],'13%');self.assertEqual(out['tax_amount'],153.47);self.assertEqual(out['invoice_type'],'电子发票（普通发票）');self.assertIn('"tax_rate"',env['EXTRACT_PROMPT'])
 def test_csv_keeps_new_fields(self):
  env=load_parts(ROOT/'tools/invoice_ocr.py',{'LEDGER_COLUMNS','write_ledger'},dict(csv=csv,Path=Path,List=List,Dict=Dict,Any=Any))
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'ocr.csv';env['write_ledger'](p,[{'invoice_no':'0001','invoice_type':'普通发票','tax_rate':'13%','tax_amount':13,'mock':True}])
   with p.open(encoding='utf-8-sig') as f:row=next(csv.DictReader(f))
   self.assertEqual(row['invoice_no'],'0001');self.assertEqual(row['tax_rate'],'13%');self.assertEqual(row['mock'],'True')
if __name__=='__main__':unittest.main()
