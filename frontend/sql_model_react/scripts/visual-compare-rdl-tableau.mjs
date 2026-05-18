#!/usr/bin/env node
import { chromium } from "@playwright/test";
import { PNG } from "pngjs";
import pixelmatch from "pixelmatch";
import { exec, execFile } from "node:child_process";
import fsSync from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execAsync = promisify(exec);
const execFileAsync = promisify(execFile);
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const packageRoot = path.resolve(scriptDir, "..");

const OUTPUT_FILES = {
  reference: "reference-rdl.png",
  generated: "generated-tableau.png",
  diff: "diff.png",
  result: "visual-comparison-result.json",
};

function printUsage() {
  console.log(`
Usage:
  npm run test:visual -- --scenario scripts/visual-comparison.scenario.example.json

Options:
  --scenario <path>  JSON scenario containing filters, RDL settings, and Tableau Cloud/local TWB settings.
  --headful          Run Tableau capture with a visible browser.
`);
}

function parseArgs(argv) {
  const args = { scenario: "", headful: false };
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (item === "--help" || item === "-h") {
      printUsage();
      process.exit(0);
    }
    if (item === "--headful") {
      args.headful = true;
      continue;
    }
    if (item === "--scenario") {
      args.scenario = argv[index + 1] || "";
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${item}`);
  }
  if (!args.scenario) {
    args.scenario = "scripts/visual-comparison.scenario.example.json";
  }
  return args;
}

function resolveFromPackage(value) {
  if (!value) return "";
  return path.isAbsolute(value) ? value : path.resolve(packageRoot, value);
}

async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, "utf8"));
}

async function ensureDir(dirPath) {
  await fs.mkdir(dirPath, { recursive: true });
}

async function fileExists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

function envForScenario(scenario, outputDir) {
  const tableauArtifactPath = resolveFromPackage(
    scenario.tableau?.artifact_path || scenario.tableau?.twb_path || "",
  );
  return {
    ...process.env,
    VISUAL_TEST_FILTERS_JSON: JSON.stringify(scenario.filters || {}),
    VISUAL_TEST_SCENARIO_JSON: JSON.stringify(scenario),
    VISUAL_TEST_RDL_PATH: resolveFromPackage(scenario.rdl?.source_path || ""),
    VISUAL_TEST_TABLEAU_TWB_PATH: tableauArtifactPath,
    VISUAL_TEST_TABLEAU_ARTIFACT_PATH: tableauArtifactPath,
    VISUAL_TEST_REFERENCE_PNG: path.join(outputDir, OUTPUT_FILES.reference),
    VISUAL_TEST_GENERATED_TABLEAU_PNG: path.join(outputDir, OUTPUT_FILES.generated),
    VISUAL_TEST_GENERATED_TABLEAU_PDF: path.join(outputDir, "generated-tableau.pdf"),
    VISUAL_TEST_OUTPUT_DIR: outputDir,
  };
}

async function runExportCommand(command, scenario, outputDir) {
  if (!command || !String(command).trim()) return;
  const { stdout, stderr } = await execAsync(command, {
    cwd: packageRoot,
    env: envForScenario(scenario, outputDir),
    windowsHide: true,
    maxBuffer: 20 * 1024 * 1024,
  });
  if (stdout.trim()) console.log(stdout.trim());
  if (stderr.trim()) console.error(stderr.trim());
}

async function convertPdfToPng(pdfPath, pngPath, dpi = 160) {
  const prefix = pngPath.replace(/\.png$/i, "");
  try {
    await execFileAsync("pdftoppm", ["-png", "-singlefile", "-r", String(dpi), pdfPath, prefix], {
      cwd: packageRoot,
      windowsHide: true,
    });
    if (await fileExists(pngPath)) return;
  } catch {
    // Try ImageMagick below.
  }

  try {
    await execFileAsync("magick", ["-density", String(dpi), `${pdfPath}[0]`, "-quality", "100", pngPath], {
      cwd: packageRoot,
      windowsHide: true,
    });
    if (await fileExists(pngPath)) return;
  } catch (error) {
    throw new Error(
      `Unable to convert PDF to PNG. Install Poppler (pdftoppm) or ImageMagick (magick). Details: ${
        error.message || error
      }`,
    );
  }

  throw new Error(`PDF conversion did not create ${pngPath}`);
}

async function prepareReferencePng(scenario, outputDir) {
  const outputPath = path.join(outputDir, OUTPUT_FILES.reference);
  const rdl = scenario.rdl || {};

  await runExportCommand(rdl.export_command, scenario, outputDir);
  if (await fileExists(outputPath)) return outputPath;

  const exportedPdfPath = path.join(outputDir, "reference-rdl.pdf");
  if (await fileExists(exportedPdfPath)) {
    await convertPdfToPng(exportedPdfPath, outputPath, rdl.pdf_dpi || 160);
    return outputPath;
  }

  const referencePng = resolveFromPackage(rdl.reference_png_path || "");
  if (referencePng && (await fileExists(referencePng))) {
    await fs.copyFile(referencePng, outputPath);
    return outputPath;
  }

  const referencePdf = resolveFromPackage(rdl.reference_pdf_path || "");
  if (referencePdf && (await fileExists(referencePdf))) {
    await convertPdfToPng(referencePdf, outputPath, rdl.pdf_dpi || 160);
    return outputPath;
  }

  throw new Error(
    "No RDL reference rendering is available. Provide rdl.reference_png_path, rdl.reference_pdf_path, " +
      "or rdl.export_command that creates VISUAL_TEST_REFERENCE_PNG.",
  );
}

function scenarioValue(scenario, keyOrValue) {
  const filters = scenario.filters || {};
  if (Object.prototype.hasOwnProperty.call(filters, keyOrValue)) {
    return String(filters[keyOrValue] ?? "");
  }
  return String(keyOrValue ?? "");
}

function buildTableauUrl(scenario) {
  const rawUrl = scenario.tableau?.url;
  if (!rawUrl) throw new Error("Missing tableau.url in scenario.");
  const url = new URL(rawUrl);
  const params = scenario.tableau?.url_filter_params || {};

  if (url.hash && url.hash.includes("/views/")) {
    const hashWithoutMarker = url.hash.slice(1);
    const queryStart = hashWithoutMarker.indexOf("?");
    const hashPath = queryStart >= 0 ? hashWithoutMarker.slice(0, queryStart) : hashWithoutMarker;
    const hashQuery = queryStart >= 0 ? hashWithoutMarker.slice(queryStart + 1) : "";
    const hashParams = new URLSearchParams(hashQuery);
    for (const [tableauParam, filterKey] of Object.entries(params)) {
      hashParams.set(tableauParam, scenarioValue(scenario, filterKey));
    }
    const serialized = hashParams.toString();
    url.hash = serialized ? `${hashPath}?${serialized}` : hashPath;
  } else {
    for (const [tableauParam, filterKey] of Object.entries(params)) {
      url.searchParams.set(tableauParam, scenarioValue(scenario, filterKey));
    }
  }
  return url.toString();
}

async function waitForRender(page, tableau) {
  const loadingSelectors = tableau.loading_selectors || [];
  for (const selector of loadingSelectors) {
    try {
      await page.locator(selector).first().waitFor({ state: "hidden", timeout: 15000 });
    } catch {
      // Tableau Cloud loaders vary by version; continue to stability wait.
    }
  }
  try {
    await page.waitForLoadState("networkidle", { timeout: 20000 });
  } catch {
    // Tableau keeps some long-poll requests alive; a fixed settle wait follows.
  }
  await page.waitForTimeout(Number(tableau.wait_after_render_ms || 3000));
}

function locatorForStep(page, step) {
  const root = step.frame_selector ? page.frameLocator(step.frame_selector) : page;
  if (step.selector) return root.locator(step.selector).first();
  if (step.label) return root.getByLabel(step.label).first();
  if (step.text) return root.getByText(step.text, { exact: Boolean(step.exact) }).first();
  throw new Error(`Filter step is missing selector, label, or text: ${JSON.stringify(step)}`);
}

async function applyTableauFilterStep(page, scenario, step) {
  const type = String(step.type || "fill").toLowerCase();
  if (type === "wait") {
    await page.waitForTimeout(Number(step.milliseconds || 1000));
    return;
  }

  const value = scenarioValue(scenario, step.filter_key ?? step.value ?? "");
  const locator = locatorForStep(page, step);
  await locator.waitFor({ state: "visible", timeout: Number(step.timeout_ms || 30000) });

  if (type === "fill") {
    await locator.fill(value);
    if (step.press_enter !== false) await locator.press("Enter");
  } else if (type === "select") {
    await locator.selectOption(step.option_value ? scenarioValue(scenario, step.option_value) : { label: value });
  } else if (type === "click") {
    await locator.click();
  } else if (type === "click-option") {
    await locator.click();
    const optionText = step.option_text ? scenarioValue(scenario, step.option_text) : value;
    const optionRoot = step.frame_selector ? page.frameLocator(step.frame_selector) : page;
    if (step.option_selector) {
      await optionRoot.locator(step.option_selector.replaceAll("{value}", optionText)).first().click();
    } else {
      await optionRoot.getByText(optionText, { exact: step.exact !== false }).first().click();
    }
  } else {
    throw new Error(`Unsupported filter step type: ${step.type}`);
  }
}

async function captureTableauPng(scenario, outputDir, cliArgs) {
  const tableau = scenario.tableau || {};
  const storageStatePath = resolveFromPackage(tableau.storage_state_path || "");
  const browser = await chromium.launch({ headless: cliArgs.headful ? false : tableau.headless !== false });
  const contextOptions = {
    viewport: tableau.viewport || { width: 1600, height: 1000 },
    deviceScaleFactor: Number(tableau.device_scale_factor || 1),
  };
  if (storageStatePath && (await fileExists(storageStatePath))) {
    contextOptions.storageState = storageStatePath;
  }

  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  try {
    await page.goto(buildTableauUrl(scenario), { waitUntil: "domcontentloaded", timeout: Number(tableau.timeout_ms || 120000) });
    if (tableau.dashboard_selector) {
      await page.locator(tableau.dashboard_selector).first().waitFor({ state: "visible", timeout: 60000 });
    }
    await waitForRender(page, tableau);

    for (const step of tableau.filter_steps || []) {
      await applyTableauFilterStep(page, scenario, step);
      await page.waitForTimeout(Number(step.wait_after_ms || tableau.wait_after_filter_ms || 1500));
      await waitForRender(page, tableau);
    }

    const screenshotPath = path.join(outputDir, OUTPUT_FILES.generated);
    await page.screenshot({
      path: screenshotPath,
      fullPage: Boolean(tableau.full_page),
      clip: tableau.screenshot_clip || undefined,
    });
    return screenshotPath;
  } finally {
    await browser.close();
  }
}

function tableauMode(scenario) {
  const mode = String(scenario.tableau?.mode || "").toLowerCase();
  if (mode) return mode;
  return scenario.tableau?.url ? "cloud" : "local_twb";
}

function localTableauArtifactPath(scenario) {
  return resolveFromPackage(scenario.tableau?.artifact_path || scenario.tableau?.twb_path || "");
}

async function prepareLocalTableauPng(scenario, outputDir) {
  const tableau = scenario.tableau || {};
  const outputPath = path.join(outputDir, OUTPUT_FILES.generated);
  const artifactPath = localTableauArtifactPath(scenario);

  if (artifactPath && !(await fileExists(artifactPath))) {
    throw new Error(`Local Tableau artifact not found: ${artifactPath}`);
  }

  await runExportCommand(tableau.export_command, scenario, outputDir);
  if (await fileExists(outputPath)) {
    return {
      path: outputPath,
      targetReportType: "Local Tableau workbook (.twb)",
      tableauArtifactPath: artifactPath,
    };
  }

  const exportedPdfPath = path.join(outputDir, "generated-tableau.pdf");
  if (await fileExists(exportedPdfPath)) {
    await convertPdfToPng(exportedPdfPath, outputPath, tableau.pdf_dpi || 160);
    return {
      path: outputPath,
      targetReportType: "Local Tableau workbook (.twb)",
      tableauArtifactPath: artifactPath,
    };
  }

  const generatedPng = resolveFromPackage(tableau.generated_png_path || "");
  if (generatedPng && (await fileExists(generatedPng))) {
    await fs.copyFile(generatedPng, outputPath);
    return {
      path: outputPath,
      targetReportType: "Local Tableau workbook (.twb)",
      tableauArtifactPath: artifactPath,
    };
  }

  const generatedPdf = resolveFromPackage(tableau.generated_pdf_path || "");
  if (generatedPdf && (await fileExists(generatedPdf))) {
    await convertPdfToPng(generatedPdf, outputPath, tableau.pdf_dpi || 160);
    return {
      path: outputPath,
      targetReportType: "Local Tableau workbook (.twb)",
      tableauArtifactPath: artifactPath,
    };
  }

  throw new Error(
    "A local .twb cannot be pixel-matched directly because it is a Tableau workbook definition, not a rendered image. " +
      "Provide tableau.generated_png_path, tableau.generated_pdf_path, or tableau.export_command that creates " +
      "VISUAL_TEST_GENERATED_TABLEAU_PNG or generated-tableau.pdf inside VISUAL_TEST_OUTPUT_DIR.",
  );
}

async function prepareTableauPng(scenario, outputDir, cliArgs) {
  const mode = tableauMode(scenario);
  if (mode === "cloud") {
    return {
      path: await captureTableauPng(scenario, outputDir, cliArgs),
      targetReportType: "Tableau Cloud",
      tableauArtifactPath: "",
    };
  }
  if (mode === "local_twb" || mode === "local" || mode === "twb") {
    return prepareLocalTableauPng(scenario, outputDir);
  }
  throw new Error(`Unsupported tableau.mode: ${scenario.tableau?.mode}`);
}

function readPng(filePath) {
  return PNG.sync.read(fsSync.readFileSync(filePath));
}

async function writePng(filePath, png) {
  await fs.writeFile(filePath, PNG.sync.write(png));
}

function backgroundFromScenario(scenario) {
  const bg = scenario.comparison?.background || {};
  return {
    r: Number.isFinite(Number(bg.r)) ? Number(bg.r) : 255,
    g: Number.isFinite(Number(bg.g)) ? Number(bg.g) : 255,
    b: Number.isFinite(Number(bg.b)) ? Number(bg.b) : 255,
    a: Number.isFinite(Number(bg.a)) ? Number(bg.a) : 255,
  };
}

function createCanvas(width, height, background) {
  const png = new PNG({ width, height });
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const idx = (width * y + x) << 2;
      png.data[idx] = background.r;
      png.data[idx + 1] = background.g;
      png.data[idx + 2] = background.b;
      png.data[idx + 3] = background.a;
    }
  }
  return png;
}

function normalizePng(source, width, height, background) {
  if (source.width === width && source.height === height) return source;
  const target = createCanvas(width, height, background);
  const copyWidth = Math.min(source.width, width);
  const copyHeight = Math.min(source.height, height);
  for (let y = 0; y < copyHeight; y += 1) {
    for (let x = 0; x < copyWidth; x += 1) {
      const sourceIdx = (source.width * y + x) << 2;
      const targetIdx = (width * y + x) << 2;
      target.data[targetIdx] = source.data[sourceIdx];
      target.data[targetIdx + 1] = source.data[sourceIdx + 1];
      target.data[targetIdx + 2] = source.data[sourceIdx + 2];
      target.data[targetIdx + 3] = source.data[sourceIdx + 3];
    }
  }
  return target;
}

function maskRects(png, rects, background) {
  const unique = new Set();
  for (const rect of rects || []) {
    const x0 = Math.max(0, Math.floor(Number(rect.x || 0)));
    const y0 = Math.max(0, Math.floor(Number(rect.y || 0)));
    const x1 = Math.min(png.width, x0 + Math.max(0, Math.floor(Number(rect.width || 0))));
    const y1 = Math.min(png.height, y0 + Math.max(0, Math.floor(Number(rect.height || 0))));
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        const idx = (png.width * y + x) << 2;
        png.data[idx] = background.r;
        png.data[idx + 1] = background.g;
        png.data[idx + 2] = background.b;
        png.data[idx + 3] = background.a;
        unique.add(`${x},${y}`);
      }
    }
  }
  return unique.size;
}

function clampRect(rect, width, height) {
  const x = Math.max(0, Math.floor(Number(rect.x || 0)));
  const y = Math.max(0, Math.floor(Number(rect.y || 0)));
  const rectWidth = Math.max(0, Math.floor(Number(rect.width || 0)));
  const rectHeight = Math.max(0, Math.floor(Number(rect.height || 0)));
  const x1 = Math.min(width, x + rectWidth);
  const y1 = Math.min(height, y + rectHeight);
  if (x >= width || y >= height || x1 <= x || y1 <= y) return null;
  return { x, y, width: x1 - x, height: y1 - y };
}

function countMaskedPixelsInRect(rect, maskRectsConfig) {
  const unique = new Set();
  for (const mask of maskRectsConfig || []) {
    const maskRect = clampRect(mask, rect.x + rect.width, rect.y + rect.height);
    if (!maskRect) continue;
    const x0 = Math.max(rect.x, maskRect.x);
    const y0 = Math.max(rect.y, maskRect.y);
    const x1 = Math.min(rect.x + rect.width, maskRect.x + maskRect.width);
    const y1 = Math.min(rect.y + rect.height, maskRect.y + maskRect.height);
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        unique.add(`${x},${y}`);
      }
    }
  }
  return unique.size;
}

function cropPng(source, rect) {
  const cropped = new PNG({ width: rect.width, height: rect.height });
  for (let y = 0; y < rect.height; y += 1) {
    for (let x = 0; x < rect.width; x += 1) {
      const sourceIdx = (source.width * (rect.y + y) + rect.x + x) << 2;
      const targetIdx = (rect.width * y + x) << 2;
      cropped.data[targetIdx] = source.data[sourceIdx];
      cropped.data[targetIdx + 1] = source.data[sourceIdx + 1];
      cropped.data[targetIdx + 2] = source.data[sourceIdx + 2];
      cropped.data[targetIdx + 3] = source.data[sourceIdx + 3];
    }
  }
  return cropped;
}

function compareRegion(reference, generated, region, comparison, acceptedThreshold) {
  const rect = clampRect(region, reference.width, reference.height);
  if (!rect) {
    return {
      id: region.id || region.name || "unnamed-region",
      name: region.name || region.id || "Unnamed region",
      type: region.type || "visual",
      bounds: {
        x: Number(region.x || 0),
        y: Number(region.y || 0),
        width: Number(region.width || 0),
        height: Number(region.height || 0),
      },
      total_pixels: 0,
      different_pixels: 0,
      difference_percentage: 100,
      conformity_score: 0,
      accepted_threshold: Number(region.accepted_threshold ?? acceptedThreshold),
      status: "failed",
      reason: "Region is outside the normalized screenshot bounds.",
    };
  }

  const referenceCrop = cropPng(reference, rect);
  const generatedCrop = cropPng(generated, rect);
  const diffCrop = new PNG({ width: rect.width, height: rect.height });
  const differentPixels = pixelmatch(referenceCrop.data, generatedCrop.data, diffCrop.data, rect.width, rect.height, {
    threshold: Number(region.pixelmatch_threshold ?? comparison.pixelmatch_threshold ?? 0.1),
    includeAA: Boolean(region.include_antialias ?? comparison.include_antialias),
  });
  const maskedPixels = countMaskedPixelsInRect(rect, comparison.mask_rects || []);
  const totalPixels = Math.max(1, rect.width * rect.height - maskedPixels);
  const differencePercentage = (differentPixels / totalPixels) * 100;
  const conformityScore = Math.max(0, 100 - differencePercentage);
  const threshold = Number(region.accepted_threshold ?? acceptedThreshold);

  return {
    id: region.id || region.name || "unnamed-region",
    name: region.name || region.id || "Unnamed region",
    type: region.type || "visual",
    bounds: rect,
    total_pixels: totalPixels,
    different_pixels: differentPixels,
    difference_percentage: Number(differencePercentage.toFixed(4)),
    conformity_score: Number(conformityScore.toFixed(4)),
    accepted_threshold: threshold,
    status: conformityScore >= threshold ? "passed" : "failed",
  };
}

function summarizeVisualRegions(reference, generated, comparison, acceptedThreshold) {
  const visualRegions = (comparison.visual_regions || []).map((region) =>
    compareRegion(reference, generated, region, comparison, acceptedThreshold),
  );
  const nonConformingVisuals = visualRegions
    .filter((region) => region.status !== "passed")
    .map((region) => ({
      id: region.id,
      name: region.name,
      type: region.type,
      bounds: region.bounds,
      conformity_score: region.conformity_score,
      difference_percentage: region.difference_percentage,
      different_pixels: region.different_pixels,
      accepted_threshold: region.accepted_threshold,
      reason: region.reason || "Pixel mismatch above the accepted threshold for this visual region.",
    }));

  return { visualRegions, nonConformingVisuals };
}

async function comparePngs(scenario, outputDir, referencePath, generatedCapture) {
  const comparison = scenario.comparison || {};
  const generatedPath = typeof generatedCapture === "string" ? generatedCapture : generatedCapture.path;
  const targetReportType =
    generatedCapture.targetReportType ||
    (tableauMode(scenario) === "cloud" ? "Tableau Cloud" : "Local Tableau workbook (.twb)");
  const background = backgroundFromScenario(scenario);
  const referenceRaw = readPng(referencePath);
  const generatedRaw = readPng(generatedPath);
  const outputSize = comparison.output_size || {};
  const width = Number(outputSize.width || Math.max(referenceRaw.width, generatedRaw.width));
  const height = Number(outputSize.height || Math.max(referenceRaw.height, generatedRaw.height));
  const reference = normalizePng(referenceRaw, width, height, background);
  const generated = normalizePng(generatedRaw, width, height, background);

  const maskedPixels = Math.max(
    maskRects(reference, comparison.mask_rects || [], background),
    maskRects(generated, comparison.mask_rects || [], background),
  );

  await writePng(referencePath, reference);
  await writePng(generatedPath, generated);

  const diff = new PNG({ width, height });
  const differentPixels = pixelmatch(reference.data, generated.data, diff.data, width, height, {
    threshold: Number(comparison.pixelmatch_threshold ?? 0.1),
    includeAA: Boolean(comparison.include_antialias),
  });

  const diffPath = path.join(outputDir, OUTPUT_FILES.diff);
  await writePng(diffPath, diff);

  const totalPixels = Math.max(1, width * height - maskedPixels);
  const differencePercentage = (differentPixels / totalPixels) * 100;
  const conformityScore = Math.max(0, 100 - differencePercentage);
  const acceptedThreshold = Number(comparison.accepted_threshold ?? 95);
  const globalStatus = conformityScore >= acceptedThreshold ? "passed" : "failed";
  const { visualRegions, nonConformingVisuals } = summarizeVisualRegions(
    reference,
    generated,
    comparison,
    acceptedThreshold,
  );
  const status = globalStatus === "passed" && nonConformingVisuals.length === 0 ? "passed" : "failed";

  return {
    source_report_type: "RDL rendered from Power BI Report Builder",
    target_report_type: targetReportType,
    applied_filters: scenario.filters || {},
    total_pixels: totalPixels,
    different_pixels: differentPixels,
    difference_percentage: Number(differencePercentage.toFixed(4)),
    conformity_score: Number(conformityScore.toFixed(4)),
    accepted_threshold: acceptedThreshold,
    global_status: globalStatus,
    status,
    semantic_visual_comparison: {
      method:
        "Configured visual regions are compared with pixel matching after the same filters and global masks are applied.",
      visual_regions: visualRegions,
      non_conforming_visuals: nonConformingVisuals,
    },
    artifacts: {
      reference_rdl_png: referencePath,
      tableau_workbook_twb: generatedCapture.tableauArtifactPath || undefined,
      generated_tableau_png: generatedPath,
      diff_png: diffPath,
    },
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const scenarioPath = resolveFromPackage(args.scenario);
  const scenario = await readJson(scenarioPath);
  const outputDir = resolveFromPackage(scenario.output?.dir || "../../outputs/visual-comparison/latest");
  await ensureDir(outputDir);

  const referencePath = await prepareReferencePng(scenario, outputDir);
  const generatedCapture = await prepareTableauPng(scenario, outputDir, args);
  const result = await comparePngs(scenario, outputDir, referencePath, generatedCapture);
  const resultPath = path.join(outputDir, OUTPUT_FILES.result);
  await fs.writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");

  console.log(`Visual conformity score: ${result.conformity_score}% (${result.status})`);
  console.log(`Result: ${resultPath}`);
  if (result.status !== "passed") {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});
