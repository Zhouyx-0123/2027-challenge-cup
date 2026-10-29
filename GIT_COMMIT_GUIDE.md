# Git commit guide

## Recommended branch
`feat/carbon-ledger-integration`

## Suggested commit
`feat(carbon-ledger): connect A0-A6 data to audit panel`

## Included paths
- `src/carbon_ledger_export.py`
- `app/services.py`
- `app/components/carbon_ledger_panel.py`
- `tests/test_carbon_ledger.py`
- `carbon_ledger/` updated A0-A6 files and 12 explicit simulated evidence placeholders
- `data/processed/carbon_ledger_*` JSON results plus `carbon_ledger_export_manifest.json` (current offline fallback format)
- `docs/58_carbon_ledger_frontend_integration.md`
- `碳慧智算汇报.pptx` is included as a source file because the evidence index references it; consider repository policy on PPT files before committing.

## Before committing
1. Review `diffs/` and ensure this is a branch, not `main`.
2. Run `pip install -r requirements.txt`; run `python -m src.carbon_ledger_export` so a dependency-complete environment emits Parquet.
3. Run `python -m pytest tests/test_carbon_ledger.py -q`.
4. Run `python carbon_ledger/a6_verification/self_check.py`.
5. Review generated results and confirm `simulated_rows=12`, `actual_rows=0`, `approved_rows=0`.

## Do not commit
- Secrets, API tokens, `.env`, private invoice/vehicle records, customer PII or real sensor payloads unless repository policy explicitly approves them.
- Python `__pycache__` and `.pyc` files.
- Do not describe simulated placeholder files as real evidence or current results as formally approved.

No remote push was executed by this task.
