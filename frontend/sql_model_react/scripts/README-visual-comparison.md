# RDL vs Tableau Visual Comparison

This test compares a source RDL rendering against a final Tableau report with the same filter values applied.
The Tableau target can be either a Tableau Cloud view or a local `.twb` artifact.

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

## Local Tableau TWB

Use `tableau.mode: "local_twb"` when the target report is a workbook artifact on your machine.

```json
{
  "tableau": {
    "mode": "local_twb",
    "artifact_path": "../../outputs/rdl_to_twb/20260512T085235Z_RegionalSales/06_tableau_publish/data_model_to_publish_consumer_final.twb",
    "generated_png_path": "../../outputs/visual-comparison/manual-tableau/generated-tableau.png",
    "generated_pdf_path": "",
    "export_command": ""
  }
}
```

Important: a `.twb` file is a Tableau workbook definition, not a rendered dashboard image.
For pixel matching, the script still needs a rendered image from that workbook:

- `tableau.generated_png_path`: an already exported Tableau PNG.
- `tableau.generated_pdf_path`: an already exported Tableau PDF, converted to PNG using `pdftoppm` or `magick`.
- `tableau.export_command`: a local command that exports the `.twb` and creates `VISUAL_TEST_GENERATED_TABLEAU_PNG` or `generated-tableau.pdf` inside `VISUAL_TEST_OUTPUT_DIR`.

For local TWB mode, make sure the rendered PNG/PDF was produced after applying the same scenario filters.
If you automate that export with `tableau.export_command`, that command must apply `VISUAL_TEST_FILTERS_JSON` itself.

When `tableau.export_command` runs, it receives:

- `VISUAL_TEST_FILTERS_JSON`
- `VISUAL_TEST_TABLEAU_TWB_PATH`
- `VISUAL_TEST_GENERATED_TABLEAU_PNG`
- `VISUAL_TEST_GENERATED_TABLEAU_PDF`
- `VISUAL_TEST_OUTPUT_DIR`

## Tableau Cloud

Use `tableau.storage_state_path` for an authenticated Playwright session. Create it once with Playwright/codegen or a small login helper, then reuse it for CI-style runs.

Filters can be applied through:

- `tableau.url_filter_params`: maps Tableau URL parameter names to scenario filter keys.
- `tableau.filter_steps`: clicks/fills/selects Tableau controls after page load.

Dynamic areas such as Tableau navigation, current time, username, and run timestamp can be ignored with `comparison.mask_rects`.

## Semantic Visual Checks

To know which exact visual is not compliant, define named rectangles in `comparison.visual_regions`.
Each region is compared with the same `pixelmatch` settings as the global report, and it can override `accepted_threshold`.

```json
{
  "comparison": {
    "accepted_threshold": 95,
    "visual_regions": [
      {
        "id": "sales-by-region",
        "name": "Sales by region chart",
        "type": "chart",
        "x": 40,
        "y": 220,
        "width": 720,
        "height": 320,
        "accepted_threshold": 95
      }
    ]
  }
}
```

The result JSON includes:

- `semantic_visual_comparison.visual_regions`: score and status for every configured visual.
- `semantic_visual_comparison.non_conforming_visuals`: only the visuals whose score is below their threshold.
- `status`: fails when the global report score fails or when any configured visual region fails.
