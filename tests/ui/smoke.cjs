// UI smoke test (headless Chromium via Playwright).
// Usage: NODE_PATH=<dir with playwright> node tests/ui/smoke.cjs <baseUrl> <inputDir> <outputDir> <shotDir>
const { chromium } = require("playwright");
const [base, inDir, outDir, shotDir] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch(process.env.CHROME_BIN ? { executablePath: process.env.CHROME_BIN } : {});
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
  await page.goto(base + "/");
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${shotDir}/01-load.png`, fullPage: true });
  await page.fill("#input-dir", inDir);
  await page.fill("#output-dir", outDir);
  await page.click("#scan-btn");
  await page.waitForFunction(() => document.querySelectorAll("#file-list li, #file-list tr").length > 0, null, { timeout: 15000 });
  const listed = await page.locator("#file-list").innerText();
  await page.screenshot({ path: `${shotDir}/02-scan.png`, fullPage: true });
  await page.waitForFunction(() => !document.querySelector("#start-btn").disabled, null, { timeout: 10000 });
  await page.click("#start-btn");
  await page.waitForFunction(() => /conclu|finaliz|terminad/i.test(document.body.innerText) &&
      !document.querySelector("#cancel-btn:not([hidden]):not([disabled])"), null, { timeout: 120000 });
  await page.waitForTimeout(500);
  const progress = await page.locator("#progress").innerText().catch(() => "");
  const results = await page.locator("#results").innerText();
  await page.screenshot({ path: `${shotDir}/03-done.png`, fullPage: true });
  // invalid path => visible error
  await page.fill("#input-dir", "/caminho/que/nao/existe");
  await page.click("#scan-btn");
  await page.waitForSelector("#error-banner:not([hidden])", { timeout: 10000 });
  const banner = await page.locator("#error-banner").innerText();
  await page.screenshot({ path: `${shotDir}/04-error.png`, fullPage: true });
  console.log(JSON.stringify({ listed, progress, results, banner, errors }, null, 1));
  await browser.close();
})().catch((e) => { console.error("SMOKE FAIL:", e); process.exit(1); });
