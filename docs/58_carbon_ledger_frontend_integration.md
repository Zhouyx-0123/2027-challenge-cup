# Carbon ledger front-end integration (2026-10-29)

The carbon ledger A0-A6 audit panel is connected through the existing chain: `src/carbon_ledger_export.py` -> `data/processed/carbon_ledger_*` -> `app/services.py` -> `app/components/carbon_ledger_panel.py`.

This change keeps the original WTW carbon-accounting track separate from engineering TTW E0, preserves `simulated` labels, and does not approve any rows. Current bundled demo data: 12 simulated trips, 2.439298 tCO2e demo total, 0 actual rows, 0 formally approved rows (`NO_APPROVED_ROWS`). This is not real company emissions.

The current authoring runtime lacks `pyarrow` and generated explicit JSON sidecars for the two DataFrame outputs, with `carbon_ledger_export_manifest.json` identifying their format. In the normal environment with requirements installed, run `python -m src.carbon_ledger_export` to generate schema-contract Parquet outputs. The code removes old JSON sidecars after a successful Parquet write.

The real Streamlit browser UI was not launched in the authoring environment because `streamlit` is not installed and package-index network access failed. Adapter, services, and the real panel render function were exercised with a test-only Streamlit API stub; see the external integration delivery logs for the exact commands/results.
