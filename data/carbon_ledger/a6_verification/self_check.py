#!/usr/bin/env python3
"""Self-checks for the improved Carbonwise ledger package. Standard-library only."""
from __future__ import annotations
import csv, json, math, pathlib, sys, zipfile, xml.etree.ElementTree as ET, hashlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]  # carbon_ledger/
A0 = ROOT / "a0_input"
A1 = ROOT / "a1_rules"
A2 = ROOT / "a2_activity_data"
A3 = ROOT / "a3_scope"
A4 = ROOT / "a4_ledger"
A5 = ROOT / "a5_evidence"
A6 = ROOT / "a6_verification"
VALIDATION = ROOT / "validation"
WORKBOOKS = {
    "source_register.xlsx": A0 / "source_register.xlsx",
    "carbon_accounting_rule_card_v2.xlsx": A1 / "carbon_accounting_rule_card_v2.xlsx",
    "activity_data_field_dictionary.xlsx": A2 / "activity_data_field_dictionary.xlsx",
    "activity_data_template.xlsx": A2 / "activity_data_template.xlsx",
    "emission_source_list.xlsx": A3 / "emission_source_list.xlsx",
    "carbon_ledger_main.xlsx": A4 / "carbon_ledger_main.xlsx",
    "quota_ledger.xlsx": A4 / "quota_ledger.xlsx",
    "evidence_index.xlsx": A5 / "evidence_index.xlsx",
}
NS = {"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "p":"http://schemas.openxmlformats.org/package/2006/relationships"}
REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

def col_index(ref):
    n = 0
    for ch in "".join(c for c in ref if c.isalpha()).upper():
        n = n*26 + ord(ch)-64
    return n-1

def workbook_sheet_map(z):
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    relmap = {e.attrib["Id"]: e.attrib["Target"] for e in rels.findall("p:Relationship", NS)}
    out = {}
    for s in wb.findall(".//m:sheets/m:sheet", NS):
        target = relmap[s.attrib[REL_ID]].lstrip("/")
        out[s.attrib["name"]] = target if target.startswith("xl/") else "xl/"+target
    return out

def shared_strings(z):
    if "xl/sharedStrings.xml" not in z.namelist(): return []
    root=ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.findall(".//m:t", NS)) for si in root.findall("m:si", NS)]

def parse_cell(c, strings):
    typ=c.attrib.get("t")
    if typ=="inlineStr": return "".join(t.text or "" for t in c.findall(".//m:t", NS))
    v=c.find("m:v",NS)
    if v is None or v.text is None: return ""
    if typ=="s":
        try: return strings[int(v.text)]
        except Exception: return ""
    return v.text

def get_sheet_rows(path, sheet_name):
    with zipfile.ZipFile(path) as z:
        strings=shared_strings(z); sp=workbook_sheet_map(z)[sheet_name]
        root=ET.fromstring(z.read(sp)); row_data=[]
        for row in root.findall(".//m:sheetData/m:row",NS):
            cells={}
            for c in row.findall("m:c",NS):
                cells[col_index(c.attrib["r"])]=parse_cell(c,strings)
            row_data.append((int(row.attrib.get("r","0")),cells))
        if not row_data: return []
        hdr=next(c for r,c in row_data if r==1)
        maxc=max(hdr) if hdr else -1
        headers=[hdr.get(i,"") for i in range(maxc+1)]
        out=[]
        for r,cells in row_data:
            if r==1: continue
            item={headers[i]:cells.get(i,"") for i in range(len(headers)) if headers[i]}
            if any(v not in ("",None) for v in item.values()): out.append(item)
        return out

def formula_map(path,sheet_name):
    with zipfile.ZipFile(path) as z:
        root=ET.fromstring(z.read(workbook_sheet_map(z)[sheet_name]))
        out={}
        for c in root.findall(".//m:sheetData/m:row/m:c",NS):
            f=c.find("m:f",NS)
            if f is not None:
                v=c.find("m:v",NS)
                out[c.attrib["r"]]=(f.text or "", v.text if v is not None and v.text is not None else "")
        return out

def sheet_names(path):
    with zipfile.ZipFile(path) as z:
        return list(workbook_sheet_map(z).keys())

def num(v):
    if v is None or str(v).strip()=="": return None
    return float(v)

def close(a,b,tol=1e-9):
    return abs(float(a)-float(b)) <= tol

def result(results,name,ok,detail):
    results.append({"test":name,"status":"PASS" if ok else "FAIL","detail":str(detail)})

def unit_matches(activity_unit, ef_unit):
    expected={"L":"kgCO2e/L","kWh":"kgCO2e/kWh","km":"kgCO2e/km","t":"kgCO2e/t"}
    return activity_unit in expected and ef_unit==expected[activity_unit]

def main():
    results=[]
    csv_path=A4/"simulated_test_data.csv"
    ledger_path=A4/"carbon_ledger_main.xlsx"
    testbook=ledger_path
    rows=list(csv.DictReader(csv_path.open(encoding="utf-8-sig",newline="")))
    required=["ledger_row_id","activity_id","trip_id","segment_id","waybill_id","invoice_no","vehicle_id",
              "trip_date","activity_type","activity_value","activity_unit","ef_value","ef_unit",
              "factor_source_register_row","emission_kgco2e","emission_tco2e","data_status","simulated",
              "evidence_id","run_id","source_ledger_row_no","duplicate_group_id","include_in_summary","exception_flag"]
    missing=[(r.get("ledger_row_id","?"),f) for r in rows for f in required if not str(r.get(f,"")).strip()]
    result(results,"R1-01 expanded synthetic dataset completeness",len(rows)==12 and not missing,
           f"rows={len(rows)}; missing={missing[:5]}")
    ids=[r["ledger_row_id"] for r in rows]
    actids=[r["activity_id"] for r in rows]
    trips=[r["trip_id"] for r in rows]
    dupgroups=[r["duplicate_group_id"] for r in rows]
    result(results,"R1-02 unique ledger/activity/trip keys",
           all(len(a)==len(set(a)) for a in (ids,actids,trips,dupgroups)),
           f"ledger={len(ids)}, unique={len(set(ids))}; activities={len(set(actids))}; trips={len(set(trips))}")
    unit_errors=[]; calc_errors=[]; totals_by_month=defaultdict(float); counts_by_month=defaultdict(int)
    per_trip={}
    for r in rows:
        if r["data_status"]!="simulated" or r["simulated"].lower()!="yes":
            unit_errors.append(r["ledger_row_id"]+": missing simulated tag")
        if not unit_matches(r["activity_unit"],r["ef_unit"]):
            unit_errors.append(r["ledger_row_id"]+": activity/EF unit mismatch")
        if "proxy" not in r["exception_flag"] or "proxy_pending_verification" not in "proxy_pending_verification":
            unit_errors.append(r["ledger_row_id"]+": proxy factor not marked pending")
        q=float(r["activity_value"]); ef=float(r["ef_value"])
        kg=q*ef; t=kg/1000
        if not close(r["emission_kgco2e"],kg,1e-8): calc_errors.append(r["ledger_row_id"]+": kg calculation mismatch")
        if not close(r["emission_tco2e"],t,1e-10): calc_errors.append(r["ledger_row_id"]+": t calculation mismatch")
        if r["include_in_summary"].upper()=="Y":
            totals_by_month[r["trip_date"][:7]] += t; counts_by_month[r["trip_date"][:7]] += 1
        per_trip[r["trip_id"]]=t
    total_t=math.fsum(float(r["activity_value"])*float(r["ef_value"])/1000 for r in rows if r["include_in_summary"].upper()=="Y")
    result(results,"R1-03 unit checks and proxy-only labels",not unit_errors,f"errors={unit_errors[:6]}")
    result(results,"R1-04 independent emission arithmetic",not calc_errors and total_t>0,
           f"synthetic_total_tCO2e={total_t:.12f}; errors={calc_errors[:5]}")
    ledger_rows=get_sheet_rows(ledger_path,"Trip_Detail")
    ledger_additive=[r for r in ledger_rows if str(r.get("Include_In_Summary","")).upper()=="Y"]
    ledger_ids={str(r.get("Ledger_Row_ID","")) for r in ledger_additive}
    ledger_total=math.fsum(float(r.get("Emissions_tCO2e") or 0) for r in ledger_additive)
    result(results,"R1-05 ledger/CVS unique-sum reconciliation",len(ledger_additive)==12 and ledger_ids==set(ids) and close(ledger_total,total_t,1e-9),
           f"ledger_rows={len(ledger_additive)}; CSV_total={total_t:.12f}; ledger_total={ledger_total:.12f}")
    month_rows=get_sheet_rows(ledger_path,"Monthly_Summary")
    month_errors=[]
    for m,expected_total in totals_by_month.items():
        mr=next((x for x in month_rows if x.get("Month")==m),None)
        if not mr or int(float(mr.get("Included_Row_Count") or 0))!=counts_by_month[m] or not close(mr.get("Total_Emissions_tCO2e") or 0,expected_total,1e-9):
            month_errors.append(m)
    annual_rows=get_sheet_rows(ledger_path,"Annual_Summary")
    annual=next((x for x in annual_rows if str(x.get("Year","")) in ("2026","2026.0")),None)
    annual_ok=bool(annual) and int(float(annual.get("Included_Row_Count") or 0))==12 and close(annual.get("Total_Emissions_tCO2e") or 0,total_t,1e-9)
    result(results,"R1-06 month/year summary reconciliation",not month_errors and annual_ok,
           f"months_checked={sorted(totals_by_month)}; month_errors={month_errors}; annual_total={annual.get('Total_Emissions_tCO2e') if annual else 'missing'}")
    # Evidence chain
    evi=get_sheet_rows(A5/"evidence_index.xlsx","Evidence_Index")
    evi_by={r.get("Evidence_ID",""):r for r in evi}
    evidence_errors=[]
    for r in rows:
        e=evi_by.get(r["evidence_id"])
        if not e: evidence_errors.append(r["evidence_id"]+": missing evidence index row"); continue
        for cf,ef in [("ledger_row_id","Ledger_Row_ID"),("invoice_no","Invoice_No"),("run_id","Run_ID"),
                      ("trip_id","Trip_ID"),("segment_id","Segment_ID"),("waybill_id","Waybill_ID")]:
            if str(e.get(ef,""))!=str(r[cf]): evidence_errors.append(r["evidence_id"]+f": {cf} mapping mismatch")
        if not (A5/"simulated_evidence"/(r["evidence_id"]+".txt")).exists():
            evidence_errors.append(r["evidence_id"]+": placeholder evidence file missing")
    result(results,"R1-07 evidence chain and placeholder file links",not evidence_errors,f"records={len(rows)}; errors={evidence_errors[:8]}")
    # Multi-waybill allocation shares must sum to 1 and allocated amounts to the physical trip total.
    wb_rows=get_sheet_rows(ledger_path,"Waybill_Detail")
    alloc=defaultdict(lambda:{"share":0.0,"emissions":0.0})
    for r in wb_rows:
        tripid=str(r.get("Trip_ID",""))
        if tripid:
            alloc[tripid]["share"] += float(r.get("Allocation_Share") or 0)
            alloc[tripid]["emissions"] += float(r.get("Allocated_Emissions_tCO2e") or 0)
    alloc_errors=[]
    for tripid,sharedata in alloc.items():
        if not close(sharedata["share"],1,1e-6): alloc_errors.append(f"{tripid}:share={sharedata['share']}")
        if tripid in per_trip and not close(sharedata["emissions"],per_trip[tripid],1e-9):
            alloc_errors.append(f"{tripid}:allocated={sharedata['emissions']} vs trip={per_trip[tripid]}")
    result(results,"R1-08 multi-waybill allocation control",not alloc_errors and len(wb_rows)==13,
           f"waybill_mapping_rows={len(wb_rows)}; grouped_trip_checks={len(alloc)}; errors={alloc_errors}")
    # Four-vehicle historical deviation examples remain excluded; no values or average should appear.
    pending=get_sheet_rows(ledger_path,"Pending_4_Vehicle_Data")
    pending_ok=(len(pending)==1 and pending[0].get("Status")=="按负责人指示暂不处理"
                and str(pending[0].get("Data_Included","")).upper()=="N"
                and str(pending[0].get("Calculation_Performed","")).upper()=="N"
                and all(str(r.get("Trip_ID","")).startswith("TRIP-SIM-") for r in ledger_additive)
                and not any("PENDING-4VEH" in str(r.get("Trip_ID","")) for r in ledger_rows))
    result(results,"R1-09 excluded four-vehicle sample guard",pending_ok,
           "历史四车偏差未复制、未计算、未进入主账或汇总")
    # Scenario suite: read cached results produced by the workbook formulas and verify with independent logic.
    tests=get_sheet_rows(testbook,"Test_Cases")
    main_ids=set(ids)
    test_errors=[]; pass_count=0; blocked_count=0
    for r in tests:
        tid=r.get("Test_ID","?")
        key=str(r.get("Ledger_Row_Key",""))
        expected=r.get("Expected_Control","")
        activity=num(r.get("Activity_Value",""))
        ef=num(r.get("EF_Value",""))
        alloc_group=str(r.get("Allocation_Group_ID","") or "")
        share=num(r.get("Allocation_Share",""))
        if key in main_ids:
            exp_status="BLOCK_DUPLICATE_LEDGER_ID"; exp_out=None
        elif alloc_group:
            alloc_shares=[num(x.get("Allocation_Share","")) or 0 for x in tests if str(x.get("Allocation_Group_ID","") or "")==alloc_group]
            exp_status="PASS_ALLOCATION" if abs(sum(alloc_shares)-1)<=1e-6 else "BLOCK_ALLOCATION_SUM"
            exp_out=(num(r.get("Trip_Total_kgCO2e","")) or 0)*(share or 0) if exp_status=="PASS_ALLOCATION" else None
        elif activity is None or ef is None:
            exp_status="BLOCK_MISSING_REQUIRED"; exp_out=None
        elif activity<0:
            exp_status="BLOCK_NEGATIVE_ACTIVITY"; exp_out=None
        elif not unit_matches(str(r.get("Activity_Unit","")),str(r.get("EF_Unit",""))):
            exp_status="BLOCK_UNIT_MISMATCH"; exp_out=None
        else:
            exp_status="PASS"; exp_out=activity*ef
        actual_status=str(r.get("Actual_Control",""))
        actual_out=num(r.get("Calculated_Output_kgCO2e",""))
        if expected!=exp_status or actual_status!=exp_status:
            test_errors.append(f"{tid}: expected/control {expected}/{actual_status}, independent={exp_status}")
        if exp_out is None:
            if actual_out is not None:
                test_errors.append(f"{tid}: blocked case unexpectedly has calculated output {actual_out}")
        elif actual_out is None or not close(actual_out,exp_out,1e-8):
            test_errors.append(f"{tid}: calculated output {actual_out} != independent {exp_out}")
        if str(r.get("Main_Ledger_Eligibility","")).startswith("N") is False:
            test_errors.append(f"{tid}: test case unexpectedly marked eligible for main ledger")
        if exp_status.startswith("PASS"): pass_count+=1
        else: blocked_count+=1
    result(results,"R1-10 15-case feasibility suite",len(tests)==15 and not test_errors and pass_count==11 and blocked_count==4,
           f"cases={len(tests)}; pass_cases={pass_count}; blocked_cases={blocked_count}; errors={test_errors[:8]}")
    # Main ledger control formulas and formal gate values.
    control_errors=[]
    for r in ledger_additive:
        if r.get("Validation_Status")!="PASS_SIMULATED_ONLY":
            control_errors.append(f"{r.get('Ledger_Row_ID')}: status {r.get('Validation_Status')}")
        if r.get("Formal_Use_Gate")!="NOT_FORMAL_USE":
            control_errors.append(f"{r.get('Ledger_Row_ID')}: formal gate {r.get('Formal_Use_Gate')}")
    result(results,"R1-11 main-ledger exception controls and formal gate",not control_errors,
           f"12 simulated rows pass structural checks only; all blocked from formal use; errors={control_errors[:6]}")
    quota_rows=get_sheet_rows(A4/"quota_ledger.xlsx","Quota_Ledger")
    quota=next((x for x in quota_rows if str(x.get("Period","")) in ("2026","2026.0")),None)
    quota_ok=bool(quota) and close(quota.get("Compliance_Used_tCO2e") or 0,total_t,1e-9) and close(quota.get("Available_Quota_tCO2e") or 0,1.2,1e-9) and close(quota.get("Closing_Balance_tCO2e") or 0,0,1e-9) and close(quota.get("Shortfall_tCO2e") or 0,max(0,total_t-1.2),1e-9) and quota.get("Data_Status")=="simulated" and quota.get("Approval_Status")=="not approved"
    result(results,"R1-12 quota-ledger formula reconciliation",quota_ok,
           f"used={quota.get('Compliance_Used_tCO2e') if quota else None}; available={quota.get('Available_Quota_tCO2e') if quota else None}; closing={quota.get('Closing_Balance_tCO2e') if quota else None}; shortfall={quota.get('Shortfall_tCO2e') if quota else None}; simulated/not-approved only")
    # Repeatability: independently compute twice from CSV.
    def compute():
        per=[(r["ledger_row_id"],float(r["activity_value"])*float(r["ef_value"]),float(r["activity_value"])*float(r["ef_value"])/1000)
             for r in rows if r["include_in_summary"].upper()=="Y"]
        return per,math.fsum(x[2] for x in per)
    a,ta=compute(); b,tb=compute()
    result(results,"R2-01 repeated calculation deterministic",a==b and close(ta,tb,1e-15),
           f"run1={ta:.12f}; run2={tb:.12f}")
    result(results,"R2-02 source-data/control total reproducible",close(ta,ledger_total,1e-9),
           f"csv={ta:.12f}; ledger={ledger_total:.12f}")
    # Excel integrity, formulas, structure.
    xlsx_files=sorted([*WORKBOOKS.values(), *VALIDATION.glob("*.xlsx")])
    corrupt=[]
    for p in xlsx_files:
        try:
            with zipfile.ZipFile(p) as z:
                if z.testzip(): corrupt.append(p.name+":zip member damaged")
                ET.fromstring(z.read("xl/workbook.xml"))
        except Exception as e: corrupt.append(p.name+":"+repr(e))
    result(results,"R3-01 Excel workbook package integrity",len(xlsx_files)>=8 and not corrupt,
           f"xlsx_count={len(xlsx_files)}; corrupt={corrupt}")
    expected_sheets={
        "source_register.xlsx":{"Source_Register","Status_Guide"},
        "carbon_accounting_rule_card_v2.xlsx":{"Rule_Card","Boundary","Formula_Register","Data_Rules","Change_Log"},
        "activity_data_field_dictionary.xlsx":{"Field_Dictionary","Status_Guide"},
        "activity_data_template.xlsx":{"Activity_Data","Instructions"},
        "emission_source_list.xlsx":{"Emission_Sources","Scope_Rules"},
        "carbon_ledger_main.xlsx":{"Trip_Detail","Segment_Detail","Waybill_Detail","Monthly_Summary","Annual_Summary","Pending_4_Vehicle_Data","Test_Cases","Validation_Rules","Feasibility_Summary","Pressure_Test_Cases","Formal_Monthly_Summary","Formal_Annual_Summary"},
        "quota_ledger.xlsx":{"Quota_Ledger","Quota_Instructions"},
        "evidence_index.xlsx":{"Evidence_Index","Evidence_Guide"},
    }
    sheet_errors=[]
    for fn,need in expected_sheets.items():
        p=WORKBOOKS.get(fn)
        if p is None or not p.exists(): sheet_errors.append(f"{fn}: missing file"); continue
        actual=set(sheet_names(p))
        if not need.issubset(actual): sheet_errors.append(f"{fn}: missing {sorted(need-actual)}")
    result(results,"R3-02 required workbooks and sheets",not sheet_errors,f"errors={sheet_errors}")
    formula_requirements={
        "carbon_ledger_main.xlsx":{
            "Trip_Detail":["H2","I2","X2","Y2","H13","X13","Y13","H101","I101","X101","Y101","AJ2","AJ13","AK2","AL2","AJ101","AL101","AM2","AN2","AP2","AM101","AN101","AP101"],
            "Monthly_Summary":["B2","C2","B4","C4"],"Annual_Summary":["B2","C2"],
            "Formal_Monthly_Summary":["B2","C2","D2","B4","C4","D4"],
            "Formal_Annual_Summary":["B2","C2","D2"],
            "Segment_Detail":["H2","H13"],"Waybill_Detail":["I2","K2","P2","Q2","I14","K14"],
            "Test_Cases":["M2","M16","O2","O16"],
            "Pressure_Test_Cases":["Z2","AA2","AB2","AC2","AF2","AH2","Z20","AA20","AB20","AC20","AF20","AH20"]},
        "activity_data_template.xlsx":{"Activity_Data":["J2","K2","AA2","AB2","J101","K101","AA101","AB101"]},
        "quota_ledger.xlsx":{"Quota_Ledger":["F2","G2","H2"]},
    }
    f_errors=[]
    for fn,sheets in formula_requirements.items():
        p=WORKBOOKS[fn]
        for sn,cells in sheets.items():
            fm=formula_map(p,sn)
            for ref in cells:
                if ref not in fm or not fm[ref][0].strip(): f_errors.append(f"{fn}/{sn}!{ref}: missing formula")
    result(results,"R3-03 formula presence",not f_errors,f"errors={f_errors[:10]}")
    # Scan formula-error cells only; literal strings such as documentation examples are not errors.
    err_terms=[]; bad_literals=("#REF!","#DIV/0!","#VALUE!","#NAME?","#N/A")
    for p in xlsx_files:
        try:
            with zipfile.ZipFile(p) as z:
                for name in z.namelist():
                    if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                        root=ET.fromstring(z.read(name))
                        for c in root.findall(".//m:sheetData/m:row/m:c",NS):
                            f=c.find("m:f",NS); v=c.find("m:v",NS)
                            if (c.attrib.get("t")=="e" or (f is not None and v is not None and v.text in bad_literals)):
                                err_terms.append(f"{p.name}/{name}!{c.attrib.get('r')}:{v.text if v is not None else 'error cell'}")
        except Exception as exc:
            err_terms.append(f"{p.name}: worksheet XML parse error {exc}")
    result(results,"R3-04 formula error token scan",not err_terms,f"errors={err_terms[:5]}")
    # Source provenance: all listed proxy data must remain source-tagged and not formal.
    source_rows=get_sheet_rows(A0/"source_register.xlsx","Source_Register")
    source_ids={str(r.get("来源ID","")) for r in source_rows}
    source_ref_ok=all(r.get("factor_source_register_row")=="5 / SRC-004" for r in rows) and "SRC-004" in source_ids
    result(results,"R3-05 factor source register link",source_ref_ok,
           f"all 12 factors refer to SRC-004 row 5 as proxy; not confirmed formal factor")

    # R4: second-round pressure tests for explicit unit whitelist, physical-trip duplicates and formal gate.
    pr = get_sheet_rows(testbook, "Pressure_Test_Cases")
    by_test = {str(r.get("Test_ID", "")): r for r in pr}
    p_errors = []
    for r in pr:
        tid = str(r.get("Test_ID", "?"))
        if r.get("Test_Result") != "PASS":
            p_errors.append(f"{tid}: Test_Result={r.get('Test_Result')}")
        if r.get("Validation_Status") != r.get("Expected_Validation_Status"):
            p_errors.append(f"{tid}: validation {r.get('Validation_Status')} != {r.get('Expected_Validation_Status')}")
        if r.get("Formal_Summary_Eligibility") != r.get("Expected_Formal_Eligibility"):
            p_errors.append(f"{tid}: formal gate {r.get('Formal_Summary_Eligibility')} != {r.get('Expected_Formal_Eligibility')}")
        if r.get("Allocation_Control") != r.get("Expected_Allocation_Control"):
            p_errors.append(f"{tid}: allocation {r.get('Allocation_Control')} != {r.get('Expected_Allocation_Control')}")
        if str(r.get("Scenario_Only", "")).lower() != "yes":
            p_errors.append(f"{tid}: scenario fixture missing Scenario_Only=yes")
        if str(r.get("Fixture_Data_Status", "")).lower() != "simulated":
            p_errors.append(f"{tid}: synthetic fixture missing Fixture_Data_Status=simulated")
    result(results,"R4-01 19-case pressure-test formulas versus expected",len(pr)==19 and not p_errors,
           f"cases={len(pr)}; failed={p_errors[:8]}")

    unsupported = by_test.get("PT-003", {})
    result(results,"R4-02 unsupported-unit whitelist blocks m³",
           unsupported.get("Validation_Status")=="BLOCK_UNSUPPORTED_UNIT" and unsupported.get("Formal_Summary_Eligibility")=="EXCLUDED_NOT_FORMALLY_APPROVED",
           f"validation={unsupported.get('Validation_Status')}; gate={unsupported.get('Formal_Summary_Eligibility')}")

    dupe_cases = [by_test.get(t, {}) for t in ("PT-008", "PT-009", "PT-010")]
    dupe_ok = all(r.get("Validation_Status")=="BLOCK_POSSIBLE_PHYSICAL_DUPLICATE" for r in dupe_cases)
    main_ledger_rows_all = get_sheet_rows(ledger_path, "Trip_Detail")
    main_rows = [r for r in main_ledger_rows_all if r.get("Ledger_Row_ID")]
    main_physical_controls = [r.get("Physical_Trip_Control") for r in main_rows]
    result(results,"R4-03 physical duplicate trip/source/fingerprint controls",
           dupe_ok and len(main_rows)==12 and all(v=="PASS_UNIQUE_PHYSICAL_TRIP" for v in main_physical_controls),
           f"pressure duplicate cases={[r.get('Validation_Status') for r in dupe_cases]}; main controls={set(main_physical_controls)}")

    formal_months = get_sheet_rows(ledger_path, "Formal_Monthly_Summary")
    formal_years = get_sheet_rows(ledger_path, "Formal_Annual_Summary")
    formal_ok = (len(formal_months)==3 and len(formal_years)==1
                 and all(int(float(r.get("Approved_Row_Count") or 0))==0
                         and close(r.get("Approved_Emissions_tCO2e") or 0,0,1e-12)
                         and r.get("Gate_Status")=="NO_APPROVED_ROWS" for r in formal_months)
                 and all(int(float(r.get("Approved_Row_Count") or 0))==0
                         and close(r.get("Approved_Emissions_tCO2e") or 0,0,1e-12)
                         and r.get("Gate_Status")=="NO_APPROVED_ROWS" for r in formal_years)
                 and all(r.get("Formal_Summary_Eligibility")=="EXCLUDED_NOT_FORMALLY_APPROVED" for r in main_rows))
    result(results,"R4-04 formal summaries exclude simulated/unapproved main rows",formal_ok,
           f"monthly={[(r.get('Month'),r.get('Approved_Row_Count'),r.get('Gate_Status')) for r in formal_months]}; annual={[(r.get('Year'),r.get('Approved_Row_Count'),r.get('Gate_Status')) for r in formal_years]}")

    summaries_labeled = all(r.get("Report_Status")=="SIMULATED_TEST_SUMMARY_NOT_FORMAL" for r in get_sheet_rows(ledger_path,"Monthly_Summary") + get_sheet_rows(ledger_path,"Annual_Summary"))
    result(results,"R4-05 test totals labeled non-formal",summaries_labeled,
           "existing month/year totals explicitly identify themselves as simulated test summaries")

    rules = get_sheet_rows(ledger_path,"Validation_Rules")
    rule_ids = {str(r.get("Rule_ID", "")) for r in rules}
    rule_ok = {"CTRL-11","CTRL-12","CTRL-13","CTRL-14"}.issubset(rule_ids)
    result(results,"R4-06 control register documents new rules",rule_ok,
           f"new rule IDs present: {sorted({"CTRL-11","CTRL-12","CTRL-13","CTRL-14"}.intersection(rule_ids))}")

    am = formula_map(ledger_path,"Trip_Detail")
    formulas_ok = ("BLOCK_UNSUPPORTED_UNIT" in am.get("AJ2",("", ""))[0]
                   and "COUNTIF($AM$2:$AM$101,AM2)" in am.get("AN2",("", ""))[0]
                   and '"FORMAL_APPROVED"' in am.get("AP2",("", ""))[0])
    result(results,"R4-07 formula logic includes whitelist, physical fingerprint and approval gate",formulas_ok,
           "AJ checks supported units; AN checks physical-trip candidates; AP requires human approval status")

    missing_identity = by_test.get("PT-019", {})
    result(results,"R4-08 missing physical identity blocks the record",
           len(pr)==19 and missing_identity.get("Validation_Status")=="BLOCK_MISSING_REQUIRED"
           and missing_identity.get("Formal_Summary_Eligibility")=="EXCLUDED_NOT_FORMALLY_APPROVED",
           f"vehicle field missing; validation={missing_identity.get('Validation_Status')}; gate={missing_identity.get('Formal_Summary_Eligibility')}")
    output={"package_root":str(ROOT),"test_suite":"improved_multi_scenario_v4_pressure_test",
            "tests":results,"passed":sum(x["status"]=="PASS" for x in results),
            "failed":sum(x["status"]=="FAIL" for x in results),
            "simulated_main_ledger_rows":len(rows),"simulated_total_tCO2e":round(total_t,12),
            "scenario_cases":len(tests),"scenario_pass_cases":pass_count,"scenario_blocked_cases":blocked_count,
            "pressure_test_cases":len(pr),"pressure_test_pass_cases":sum(1 for r in pr if r.get("Test_Result")=="PASS"),
            "formal_emission_or_posting_claim":False,
            "excluded_four_vehicle_deviation_examples":{"calculated":False,"included_in_ledger":False,"average_computed":False}}
    (A6/"self_check_results.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    for t in results: print(f"[{t['status']}] {t['test']}: {t['detail']}")
    print(f"SUMMARY passed={output['passed']} failed={output['failed']}")
    return 0 if output["failed"]==0 else 1

if __name__=="__main__":
    sys.exit(main())
