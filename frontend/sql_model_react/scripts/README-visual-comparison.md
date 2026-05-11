# RDL vs Tableau Cloud Visual Comparison

This test compares a source RDL rendering against the final Tableau Cloud report with the same filter values applied.

## Run

```powershell
npm run test:visual -- --scenario scripts/visual-comparison.scenario.example.json
```

The scenario writes these files under `output.dir`:

- `reference-rdl.png`
- `generated-tableau.png`
- `diff.png`
- `visual-comparison-result.json`

## RDL Reference

The script accepts one of these inputs:

- `rdl.reference_png_path`: an already exported Power BI Report Builder PNG.
- `rdl.reference_pdf_path`: an exported PDF, converted to PNG using `pdftoppm` or `magick`.
- `rdl.export_command`: a local command that exports the RDL with the scenario filters.

When `rdl.export_command` runs, it receives:

- `VISUAL_TEST_FILTERS_JSON`
- `VISUAL_TEST_RDL_PATH`
- `VISUAL_TEST_REFERENCE_PNG`
- `VISUAL_TEST_OUTPUT_DIR`

The export command should create either `VISUAL_TEST_REFERENCE_PNG` or `reference-rdl.pdf` inside `VISUAL_TEST_OUTPUT_DIR`.

## Tableau Cloud

Use `tableau.storage_state_path` for an authenticated Playwright session. Create it once with Playwright/codegen or a small login helper, then reuse it for CI-style runs.

Filters can be applied through:

- `tableau.url_filter_params`: maps Tableau URL parameter names to scenario filter keys.
- `tableau.filter_steps`: clicks/fills/selects Tableau controls after page load.

Dynamic areas such as Tableau navigation, current time, username, and run timestamp can be ignored with `comparison.mask_rects`.
