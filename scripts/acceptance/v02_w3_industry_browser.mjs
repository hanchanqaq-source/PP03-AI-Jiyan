import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import process from "node:process";


function requiredPath(name) {
  const value = process.env[name];
  if (!value || !path.isAbsolute(value)) throw new Error(`${name} must be an absolute path`);
  return path.resolve(value);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const toolsRoot = requiredPath("V02_W3_PLAYWRIGHT_TOOLS");
const browsersRoot = requiredPath("PLAYWRIGHT_BROWSERS_PATH");
const requireFromTools = createRequire(path.join(toolsRoot, "package.json"));
const packageJson = requireFromTools("playwright/package.json");
if (packageJson.version !== "1.62.1") {
  throw new Error(`Playwright version mismatch: ${packageJson.version}`);
}
const { chromium } = requireFromTools("playwright");
const executablePath = path.resolve(chromium.executablePath());
const browserPrefix = `${browsersRoot.toLocaleLowerCase()}${path.sep}`;
assert(
  executablePath.toLocaleLowerCase().startsWith(browserPrefix),
  `Chromium executable escaped PLAYWRIGHT_BROWSERS_PATH: ${executablePath}`,
);
assert(fs.existsSync(executablePath), `Chromium executable missing: ${executablePath}`);

if (process.argv.includes("--preflight")) {
  process.stdout.write(`${JSON.stringify({
    phase: "preflight",
    playwrightVersion: packageJson.version,
    toolsRoot,
    browsersRoot,
    executablePath,
  })}\n`);
  process.exit(0);
}

assert(process.argv.includes("--run"), "expected --preflight or --run");
const baseUrl = process.env.V02_W3_BASE_URL;
const backendUrl = process.env.V02_W3_BACKEND_URL;
assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(baseUrl ?? ""), "invalid V02_W3_BASE_URL");
assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(backendUrl ?? ""), "invalid V02_W3_BACKEND_URL");
const outputDir = requiredPath("V02_W3_OUTPUT_DIR");
const profileDir = requiredPath("V02_W3_PROFILE_DIR");
fs.mkdirSync(outputDir, { recursive: true });
fs.mkdirSync(profileDir, { recursive: true });

const consoleMessages = [];
const pageErrors = [];
const failedRequests = [];
const network = [];
const responses = [];
const checks = [];
const record = (name, details = {}) => checks.push({ name, status: "pass", ...details });

const context = await chromium.launchPersistentContext(profileDir, {
  executablePath,
  headless: true,
  viewport: { width: 1440, height: 1100 },
  locale: "zh-CN",
  colorScheme: "dark",
  reducedMotion: "reduce",
});
const chromiumVersion = context.browser()?.version() ?? "unknown";
let page;

try {
  const pages = context.pages();
  page = pages[0] ?? await context.newPage();
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      consoleMessages.push({ type: message.type(), text: message.text() });
    }
  });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("request", (request) => network.push({ method: request.method(), url: request.url() }));
  page.on("response", (response) => responses.push({ status: response.status(), url: response.url() }));
  page.on("requestfailed", (request) => failedRequests.push({
    method: request.method(),
    url: request.url(),
    failure: request.failure()?.errorText ?? "unknown",
  }));

  await page.goto(`${baseUrl}/industry-research`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "切换到存储" }).click();
  const storageArticle = page.locator('[data-industry-report-top] > article[data-industry-id="storage"]');
  await storageArticle.waitFor({ state: "visible" });
  await page.getByText("隔离演示快照 DEMO-S-TRUSTED-001", { exact: false }).waitFor();

  const sectionIds = await storageArticle.locator("section[id]").evaluateAll((nodes) => nodes.map((node) => node.id));
  assert(
    JSON.stringify(sectionIds) === JSON.stringify(["overview", "cycle", "chain", "metrics", "capital", "companies", "funds", "news-risk"]),
    `unexpected report section order: ${sectionIds.join(",")}`,
  );
  const storageText = await storageArticle.innerText();
  for (const text of [
    "隔离演示", "来源失败", "当前显示的旧可信快照", "DEMO-S-TRUSTED-001",
    "待核验", "冲突", "暂无可靠数据", "未读取真实持仓，当前没有可展示的持仓关联",
  ]) assert(storageText.includes(text), `storage report missing state: ${text}`);
  assert(storageText.includes("DRAM 价格") && storageText.includes("NAND 价格"), "storage metrics missing");
  assert(!storageText.includes("持仓金额") && !storageText.includes("持仓成本") && !storageText.includes("用户账号"), "private holdings fields rendered");
  record("storage-normal-partial-candidate-conflict-old-snapshot-no-holdings", { sectionIds });

  const windowCounts = {};
  for (const days of [7, 30, 90]) {
    await page.getByRole("button", { name: `最近 ${days} 天` }).click();
    const historyText = await storageArticle.locator("#news-risk").innerText();
    windowCounts[days] = new Set(historyText.match(/DEMO-S-(?:NEWS|PENDING|CONFLICT)-[A-Z0-9-]+/g) ?? []).size;
  }
  assert(windowCounts[7] < windowCounts[30] && windowCounts[30] < windowCounts[90], `history windows did not expand: ${JSON.stringify(windowCounts)}`);
  record("history-7-30-90", { windowCounts });

  const metricsAnchor = page.getByRole("link", { name: "核心数据" });
  await metricsAnchor.click();
  await page.waitForFunction(() => window.location.hash === "#metrics");
  assert(await metricsAnchor.getAttribute("aria-current") === "location", "current anchor not exposed");
  const stickyPosition = await page.locator('div.sticky').first().evaluate((node) => getComputedStyle(node).position);
  assert(stickyPosition === "sticky", "compound navigation is not sticky");
  record("sticky-anchor-hash");

  const evidenceButton = storageArticle.getByRole("button", { name: /查看.*证据/ }).first();
  if (await evidenceButton.count()) {
    await evidenceButton.focus();
    await page.keyboard.press("Enter");
    await page.getByRole("dialog").waitFor();
    const evidenceText = await page.getByRole("dialog").innerText();
    for (const text of ["数据来源", "数据日期", "数据口径", "判断依据", "失效条件", "evidence"])
      assert(evidenceText.toLocaleLowerCase().includes(text.toLocaleLowerCase()), `evidence drawer missing ${text}`);
    await page.keyboard.press("Escape");
    await evidenceButton.waitFor({ state: "visible" });
    record("keyboard-evidence-dialog-focus");
  }

  await page.getByRole("button", { name: "切换到半导体" }).click();
  const semiconductorArticle = page.locator('[data-industry-report-top] > article[data-industry-id="semiconductor"]');
  await semiconductorArticle.waitFor();
  const semiconductorText = await semiconductorArticle.innerText();
  assert(!semiconductorText.includes("DRAM 价格") && !semiconductorText.includes("DEMO-S-"), "semiconductor leaked storage data");
  assert(semiconductorText.includes("设备订单与出货"), "semiconductor differential template missing");
  record("semiconductor-differential-isolation");

  await page.getByRole("button", { name: "切换到机器人" }).click();
  const roboticsArticle = page.locator('[data-industry-report-top] > article[data-industry-id="robotics"]');
  await roboticsArticle.waitFor();
  const roboticsText = await roboticsArticle.innerText();
  assert(!roboticsText.includes("DRAM 价格") && !roboticsText.includes("DEMO-S-"), "robotics leaked storage data");
  assert(roboticsText.includes("样机进展") && roboticsText.includes("量产进度"), "robotics differential template missing");
  record("robotics-differential-isolation");

  await page.getByRole("button", { name: "切换到存储" }).click();
  await page.getByRole("button", { name: "切换到半导体" }).click();
  await page.getByRole("button", { name: "切换到机器人" }).click();
  await roboticsArticle.waitFor();
  assert((await roboticsArticle.getAttribute("data-industry-id")) === "robotics", "rapid switch ended on wrong industry");
  assert(!(await roboticsArticle.innerText()).includes("DEMO-S-"), "late response overwrote current industry");
  record("rapid-three-industry-switch");

  const addButton = page.getByRole("button", { name: "添加标签" });
  await addButton.click();
  await page.getByRole("textbox", { name: "自定义行业名称" }).fill("先进封装验收");
  await page.getByRole("button", { name: "创建并激活" }).click();
  await page.getByText("该行业报告正在建设", { exact: true }).waitFor();
  const customTag = page.getByRole("button", { name: "切换到先进封装验收" });
  await customTag.waitFor();
  assert(await customTag.getAttribute("aria-pressed") === "true", "custom tag was not activated immediately");
  const buildingText = await page.locator("[data-industry-report-top]").innerText();
  assert(buildingText.includes("系统不会根据标签名称自动补造行业数据"), "custom tag building state omitted no-fabrication boundary");
  record("custom-tag-immediate-building-state");

  await page.getByRole("button", { name: "将先进封装验收左移" }).focus();
  await page.keyboard.press("Enter");
  assert((await page.locator('[data-testid^="selected-tag-"]').allInnerTexts()).some((text) => text.includes("先进封装验收")), "keyboard reorder removed custom tag");
  record("keyboard-tag-reorder-focus");

  await page.evaluate(() => {
    localStorage.setItem("vr-page-tags:industry_research", JSON.stringify({
      version: 2, ids: [], activeId: "", order: [],
    }));
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.getByText("暂无可靠数据", { exact: true }).waitFor();
  assert((await page.locator("[data-industry-report-top]").innerText()).includes("当前未选择行业标签"), "no-snapshot empty state missing");
  record("no-trusted-snapshot-empty-state");

  await page.evaluate(() => {
    localStorage.setItem("vr-page-tags:industry_research", JSON.stringify({
      version: 2,
      ids: ["semiconductor", "storage", "robotics"],
      activeId: "storage",
      order: ["semiconductor", "storage", "robotics"],
    }));
  });
  await page.reload({ waitUntil: "networkidle" });

  await page.getByRole("button", { name: "切换到存储" }).click();
  await storageArticle.waitFor();
  await page.setViewportSize({ width: 1440, height: 1100 });
  const bodyFont = await page.locator("body").evaluate((node) => Number.parseFloat(getComputedStyle(node).fontSize));
  const paragraphFont = await storageArticle.locator("p").first().evaluate((node) => Number.parseFloat(getComputedStyle(node).fontSize));
  assert(bodyFont >= 14 && paragraphFont >= 12, `readability floor violated: body=${bodyFont}, auxiliary=${paragraphFont}`);
  await page.screenshot({ path: path.join(outputDir, "01-industry-research-storage.png"), fullPage: false });
  record("desktop-1440x1100-readability-screenshot", { bodyFont, paragraphFont });

  for (const viewport of [{ width: 1024, height: 768 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    assert(await storageArticle.isVisible(), `report hidden at ${viewport.width}x${viewport.height}`);
    const dimensions = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
    assert(dimensions.scrollWidth <= dimensions.clientWidth + 2, `page overflow at ${viewport.width}x${viewport.height}`);
  }
  record("responsive-1024-and-390");

  assert(consoleMessages.length === 0, `console errors/warnings: ${JSON.stringify(consoleMessages)}`);
  assert(pageErrors.length === 0, `page errors: ${JSON.stringify(pageErrors)}`);
  assert(failedRequests.length === 0, `blocking request failures: ${JSON.stringify(failedRequests)}`);
  record("console-pageerror-requestfailed-clean");

  fs.writeFileSync(path.join(outputDir, "browser-results.json"), `${JSON.stringify({
    status: "pass",
    playwrightVersion: packageJson.version,
    chromiumVersion,
    executablePath,
    baseUrl,
    backendUrl,
    checks,
    consoleMessages,
    pageErrors,
    failedRequests,
    network,
    responses,
  }, null, 2)}\n`, "utf8");
} catch (error) {
  const pageText = page
    ? await page.locator("body").innerText({ timeout: 2000 }).catch(() => "<body unavailable>")
    : "<page unavailable>";
  const failure = {
    status: "fail",
    error: error instanceof Error ? (error.stack ?? error.message) : String(error),
    pageUrl: page?.url() ?? "<page unavailable>",
    pageText: pageText.slice(0, 20000),
    consoleMessages,
    pageErrors,
    failedRequests,
    network,
    responses,
  };
  fs.writeFileSync(
    path.join(outputDir, "browser-failure.json"),
    `${JSON.stringify(failure, null, 2)}\n`,
    "utf8",
  );
  process.stderr.write(`${JSON.stringify(failure)}\n`);
  throw error;
} finally {
  await context.close();
}
