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
const expectedCancelledRequests = [];
const blockingFailedRequests = [];
const network = [];
const responses = [];
const unexpectedNon2xxResponses = [];
const checks = [];
const record = (name, details = {}) => checks.push({ name, status: "pass", ...details });
let activeCancellationAction = null;
const requestActionLabels = new WeakMap();
const industryActionEvidence = [];
const expectedIndustryPaths = new Set([
  "/api/industry-research/storage",
  "/api/industry-research/semiconductor",
  "/api/industry-research/robotics",
]);
const reportArticleSelectors = {
  storage: '[data-industry-report-top] > article[data-industry-id="storage"]',
  semiconductor: '[data-industry-report-top] > article[data-industry-id="semiconductor"]',
  robotics: '[data-industry-report-top] > article[data-industry-id="robotics"]',
};

function isExpectedIndustryCancellation(entry) {
  if (!entry.actionLabel || entry.method !== "GET" || entry.failure !== "net::ERR_ABORTED") return false;
  let parsed;
  try {
    parsed = new URL(entry.url);
  } catch {
    return false;
  }
  const windowDays = parsed.searchParams.get("window_days");
  const onlyWindowDays = [...parsed.searchParams.keys()].every((key) => key === "window_days");
  return parsed.origin === baseUrl
    && expectedIndustryPaths.has(parsed.pathname)
    && onlyWindowDays
    && parsed.searchParams.getAll("window_days").length === 1
    && ["7", "30", "90"].includes(windowDays);
}

function exactIndustryRequest(url, industryId, windowDays = "90") {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  return parsed.origin === baseUrl
    && parsed.pathname === `/api/industry-research/${industryId}`
    && [...parsed.searchParams.keys()].every((key) => key === "window_days")
    && parsed.searchParams.getAll("window_days").length === 1
    && parsed.searchParams.get("window_days") === windowDays;
}

function summarizeResponseStatuses() {
  const responseStatusCounts = {};
  for (const response of responses) {
    const key = String(response.status);
    responseStatusCounts[key] = (responseStatusCounts[key] ?? 0) + 1;
  }
  return responseStatusCounts;
}

function summarizeExpectedCancellationActions() {
  return industryActionEvidence.map((action) => ({
    ...action,
    expectedCancellationCount: expectedCancelledRequests.filter(
      (request) => request.actionLabel === action.label,
    ).length,
  }));
}

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
  page.on("request", (request) => {
    network.push({ method: request.method(), url: request.url() });
    if (activeCancellationAction) requestActionLabels.set(request, activeCancellationAction.label);
  });
  page.on("response", (response) => {
    const entry = {
      method: response.request().method(),
      status: response.status(),
      url: response.url(),
      actionLabel: requestActionLabels.get(response.request()) ?? null,
    };
    responses.push(entry);
    if (response.status() >= 400) unexpectedNon2xxResponses.push(entry);
  });
  page.on("requestfailed", (request) => {
    const entry = {
      method: request.method(),
      url: request.url(),
      failure: request.failure()?.errorText ?? "unknown",
      actionLabel: requestActionLabels.get(request) ?? null,
    };
    failedRequests.push(entry);
    if (isExpectedIndustryCancellation(entry)) expectedCancelledRequests.push(entry);
    else blockingFailedRequests.push(entry);
  });

  async function runIndustryAction(label, targetIndustryId, trigger) {
    assert(activeCancellationAction === null, `nested cancellation action: ${label}`);
    activeCancellationAction = { label, targetIndustryId };
    const successorPromise = page.waitForResponse((response) => (
      response.request().method() === "GET"
      && response.status() >= 200
      && response.status() < 300
      && exactIndustryRequest(response.url(), targetIndustryId)
    ));
    try {
      await trigger();
      const successor = await successorPromise;
      const articleSelector = reportArticleSelectors[targetIndustryId];
      assert(articleSelector, `${label} target industry is outside the approved matrix`);
      const article = page.locator(articleSelector);
      await article.waitFor({ state: "visible" });
      const successorArticleIndustryId = await article.getAttribute("data-industry-id");
      assert(successorArticleIndustryId === targetIndustryId, `${label} successor article mismatch`);
      const evidence = {
        label,
        targetIndustryId,
        successorStatus: successor.status(),
        successorUrl: successor.url(),
        successorArticleIndustryId,
      };
      industryActionEvidence.push(evidence);
      return { article, evidence };
    } finally {
      activeCancellationAction = null;
    }
  }

  const initialStorage = await runIndustryAction(
    "initial-goto-storage",
    "storage",
    () => page.goto(`${baseUrl}/industry-research`, { waitUntil: "domcontentloaded" }),
  );
  const storageArticle = initialStorage.article;
  await page.getByText("隔离演示快照 DEMO-S-TRUSTED-001", { exact: false }).waitFor();

  const sectionIds = await storageArticle.locator("section[id]").evaluateAll((nodes) => nodes.map((node) => node.id));
  assert(
    JSON.stringify(sectionIds) === JSON.stringify(["overview", "cycle", "chain", "metrics", "capital", "companies", "funds", "news-risk"]),
    `unexpected report section order: ${sectionIds.join(",")}`,
  );
  const trustedOverview = storageArticle.locator("#overview");
  const trustedOverviewText = await trustedOverview.innerText();
  const trustedBasisIds = ["dram_price", "nand_price"];
  for (const basisId of trustedBasisIds)
    assert(trustedOverviewText.includes(basisId), `trusted overview missing basis ${basisId}`);
  const trustedConclusion = (await trustedOverview.locator("p").allInnerTexts())
    .find((text) => text.includes("规则=demo-storage-cycle-v1"));
  assert(trustedConclusion?.includes("状态=verified"), "trusted conclusion was not rendered from admitted basis");
  for (const untrustedValue of [
    "storage_candidate_signal", "隔离演示候选值", "storage_conflicting_signal",
    "DEMO-S-CONFLICT-A", "DEMO-S-CONFLICT-B", "上升", "下降",
  ]) assert(!trustedOverviewText.includes(untrustedValue), `trusted overview leaked untrusted value: ${untrustedValue}`);

  const candidateRegion = storageArticle.getByRole("complementary", { name: "候选数据与冲突" });
  const candidateText = await candidateRegion.innerText();
  assert(candidateText.includes("storage_candidate_signal"), "candidate metric id missing from candidate region");
  assert(candidateText.includes("隔离演示候选值"), "candidate metric value missing from candidate region");
  const candidateEvidenceButton = candidateRegion.getByRole("button", { name: "查看 待核验候选信号证据" });
  await candidateEvidenceButton.click();
  const candidateEvidenceDialog = page.getByRole("dialog", { name: "待核验候选信号证据" });
  await candidateEvidenceDialog.waitFor();
  const candidateEvidenceText = await candidateEvidenceDialog.innerText();
  assert(candidateEvidenceText.includes("DEMO-S-PENDING-EVIDENCE-001"), "candidate evidence id missing");
  await page.keyboard.press("Escape");
  await page.waitForFunction((button) => document.activeElement === button, await candidateEvidenceButton.elementHandle());

  const conflictRegion = candidateRegion.getByRole("alert");
  const conflictText = await conflictRegion.innerText();
  for (const conflictValue of ["DEMO-S-CONFLICT-A", "DEMO-S-CONFLICT-B", "上升", "下降"])
    assert(conflictText.includes(conflictValue), `conflict region missing ${conflictValue}`);
  const conflictAggregatePresent = conflictText.includes("冲突综合值");
  assert(!conflictAggregatePresent, "conflict region rendered a conflict aggregate value");

  const oldSnapshotBanner = storageArticle.locator(':scope > [role="alert"]').first();
  const demoSnapshotBanner = storageArticle.locator(":scope > div").filter({ hasText: "隔离演示快照 DEMO-S-TRUSTED-001" }).first();
  const oldBannerSnapshotId = (await oldSnapshotBanner.innerText()).match(/DEMO-S-TRUSTED-\d+/)?.[0] ?? null;
  const demoBannerSnapshotId = (await demoSnapshotBanner.innerText()).match(/DEMO-S-TRUSTED-\d+/)?.[0] ?? null;
  const headerSnapshotId = await storageArticle.locator("dt").filter({ hasText: /^当前可信快照$/ }).evaluate(
    (node) => node.nextElementSibling?.textContent?.trim() ?? null,
  );
  assert(oldBannerSnapshotId === "DEMO-S-TRUSTED-001", "old snapshot banner lineage mismatch");
  assert(demoBannerSnapshotId === oldBannerSnapshotId, "demo banner lineage mismatch");
  assert(headerSnapshotId === oldBannerSnapshotId, "header lineage mismatch");

  const storageText = await storageArticle.innerText();
  for (const text of [
    "隔离演示", "来源失败", "当前显示的旧可信快照", "DEMO-S-TRUSTED-001",
    "待核验", "冲突", "暂无可靠数据", "未读取真实持仓，当前没有可展示的持仓关联",
  ]) assert(storageText.includes(text), `storage report missing state: ${text}`);
  assert(storageText.includes("DRAM 价格") && storageText.includes("NAND 价格"), "storage metrics missing");
  assert(!storageText.includes("持仓金额") && !storageText.includes("持仓成本") && !storageText.includes("用户账号"), "private holdings fields rendered");
  record("storage-normal-partial-candidate-conflict-old-snapshot-no-holdings", {
    sectionIds,
    truthPartition: {
      trustedBasisIds,
      trustedConclusion,
      candidateMetricId: "storage_candidate_signal",
      candidateValue: "隔离演示候选值",
      candidateEvidenceId: "DEMO-S-PENDING-EVIDENCE-001",
      conflictEvidenceIds: ["DEMO-S-CONFLICT-A", "DEMO-S-CONFLICT-B"],
      conflictValues: ["上升", "下降"],
      conflictAggregatePresent,
    },
    lineage: { oldBannerSnapshotId, demoBannerSnapshotId, headerSnapshotId },
  });

  const windowCounts = {};
  const oldestOccurredAt = {};
  for (const days of [7, 30, 90]) {
    await page.getByRole("button", { name: `最近 ${days} 天` }).click();
    await page.waitForFunction((expectedDays) => [...document.querySelectorAll("button")].some((button) => (
      button.getAttribute("aria-label") === `最近 ${expectedDays} 天`
      && button.getAttribute("aria-pressed") === "true"
    )), days);
    const historyText = await storageArticle.locator("#news-risk").innerText();
    windowCounts[days] = new Set(historyText.match(/DEMO-S-(?:NEWS|PENDING|CONFLICT)-[A-Z0-9-]+/g) ?? []).size;
    const occurredAtValues = historyText.match(/2026-\d{2}-\d{2}T00:00:00\+00:00/g) ?? [];
    oldestOccurredAt[days] = occurredAtValues.sort()[0] ?? null;
  }
  assert(windowCounts[7] < windowCounts[30] && windowCounts[30] < windowCounts[90], `history windows did not expand: ${JSON.stringify(windowCounts)}`);
  assert(oldestOccurredAt[7] === "2026-08-23T00:00:00+00:00", `unexpected 7-day oldest event: ${oldestOccurredAt[7]}`);
  assert(oldestOccurredAt[30] === "2026-08-02T00:00:00+00:00", `unexpected 30-day oldest event: ${oldestOccurredAt[30]}`);
  assert(oldestOccurredAt[90] === "2026-06-20T00:00:00+00:00", `unexpected 90-day oldest event: ${oldestOccurredAt[90]}`);
  assert(oldestOccurredAt[7] > oldestOccurredAt[30], "7-day history did not narrow to newer events");
  assert(oldestOccurredAt[30] > oldestOccurredAt[90], "30-day history did not narrow against 90-day events");
  record("history-7-30-90", { windowCounts, oldestOccurredAt });

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
    for (const text of ["原始快照", "证据快照", "数据日期", "数据口径", "判断依据", "失效条件", "来源族"])
      assert(evidenceText.toLocaleLowerCase().includes(text.toLocaleLowerCase()), `evidence drawer missing ${text}`);
    await page.keyboard.press("Escape");
    await evidenceButton.waitFor({ state: "visible" });
    const evidenceButtonHandle = await evidenceButton.elementHandle();
    await page.waitForFunction((button) => document.activeElement === button, evidenceButtonHandle);
    const restoredFocusLabel = await evidenceButton.getAttribute("aria-label");
    record("keyboard-evidence-dialog-focus", { restoredFocusLabel, exactTriggerRestored: true });
  }

  const semiconductorSwitch = await runIndustryAction(
    "switch-semiconductor",
    "semiconductor",
    () => page.getByRole("button", { name: "切换到半导体" }).click(),
  );
  const semiconductorArticle = semiconductorSwitch.article;
  const semiconductorText = await semiconductorArticle.innerText();
  const semiconductorTitle = (await semiconductorArticle.locator("h1").innerText()).replace(/\s+/g, "");
  const semiconductorSections = await semiconductorArticle.locator("section[id]").evaluateAll((nodes) => nodes.map((node) => node.id));
  const semiconductorIndustryIds = [
    await semiconductorArticle.getAttribute("data-industry-id"),
    ...await semiconductorArticle.locator("[data-industry-id]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-industry-id"))),
  ];
  const articleIndustryIds = [...new Set(semiconductorIndustryIds)];
  assert(semiconductorTitle === "半导体行业研究", `unexpected semiconductor title: ${semiconductorTitle}`);
  assert(JSON.stringify(semiconductorSections) === JSON.stringify(sectionIds), `semiconductor sections mismatch: ${semiconductorSections}`);
  assert(semiconductorText.includes("DEMO-H-TRUSTED-001"), "semiconductor snapshot id missing");
  assert(articleIndustryIds.length === 1 && articleIndustryIds[0] === "semiconductor", `semiconductor identity leak: ${articleIndustryIds}`);
  assert(!semiconductorText.includes("DRAM 价格") && !semiconductorText.includes("DEMO-S-"), "semiconductor leaked storage data");
  assert(semiconductorText.includes("设备订单与出货"), "semiconductor differential template missing");
  assert(semiconductorText.includes("隔离演示快照 DEMO-H-TRUSTED-001"), "semiconductor demo boundary missing");
  record("semiconductor-differential-isolation", {
    title: semiconductorTitle,
    sectionIds: semiconductorSections,
    expectedSnapshotId: "DEMO-H-TRUSTED-001",
    articleIndustryIds,
  });

  const roboticsSwitch = await runIndustryAction(
    "switch-robotics",
    "robotics",
    () => page.getByRole("button", { name: "切换到机器人" }).click(),
  );
  const roboticsArticle = roboticsSwitch.article;
  const roboticsText = await roboticsArticle.innerText();
  const roboticsTitle = (await roboticsArticle.locator("h1").innerText()).replace(/\s+/g, "");
  const roboticsSections = await roboticsArticle.locator("section[id]").evaluateAll((nodes) => nodes.map((node) => node.id));
  const roboticsIndustryIds = [...new Set([
    await roboticsArticle.getAttribute("data-industry-id"),
    ...await roboticsArticle.locator("[data-industry-id]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-industry-id"))),
  ])];
  assert(roboticsTitle === "机器人行业研究", `unexpected robotics title: ${roboticsTitle}`);
  assert(JSON.stringify(roboticsSections) === JSON.stringify(sectionIds), `robotics sections mismatch: ${roboticsSections}`);
  assert(roboticsText.includes("DEMO-R-TRUSTED-001"), "robotics snapshot id missing");
  assert(roboticsIndustryIds.length === 1 && roboticsIndustryIds[0] === "robotics", `robotics identity leak: ${roboticsIndustryIds}`);
  assert(!roboticsText.includes("DRAM 价格") && !roboticsText.includes("DEMO-S-"), "robotics leaked storage data");
  assert(roboticsText.includes("样机进展") && roboticsText.includes("量产进度"), "robotics differential template missing");
  assert(roboticsText.includes("隔离演示快照 DEMO-R-TRUSTED-001"), "robotics demo boundary missing");
  record("robotics-differential-isolation", {
    title: roboticsTitle,
    sectionIds: roboticsSections,
    expectedSnapshotId: "DEMO-R-TRUSTED-001",
    articleIndustryIds: roboticsIndustryIds,
  });

  const rapidSwitch = await runIndustryAction("rapid-three-industry-switch", "robotics", async () => {
    await page.getByRole("button", { name: "切换到存储" }).click();
    await page.getByRole("button", { name: "切换到半导体" }).click();
    await page.getByRole("button", { name: "切换到机器人" }).click();
  });
  await rapidSwitch.article.waitFor();
  assert((await roboticsArticle.getAttribute("data-industry-id")) === "robotics", "rapid switch ended on wrong industry");
  assert(!(await roboticsArticle.innerText()).includes("DEMO-S-"), "late response overwrote current industry");
  record("rapid-three-industry-switch", rapidSwitch.evidence);

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

  const customTagContainer = customTag.locator("..");
  const customTagTestId = await customTagContainer.getAttribute("data-testid");
  assert(customTagTestId?.startsWith("selected-tag-"), "custom tag test id missing");
  const customTagId = customTagTestId.slice("selected-tag-".length);
  const selectedTagOrder = async () => page.locator('[data-testid^="selected-tag-"]').evaluateAll(
    (nodes) => nodes.map((node) => node.getAttribute("data-testid")?.slice("selected-tag-".length) ?? ""),
  );
  const initialOrder = await selectedTagOrder();
  const initialIndex = initialOrder.indexOf(customTagId);
  assert(initialIndex >= 0, "custom tag missing from the actual order");
  let preparedIndex = initialIndex;
  let prepareCustomMoveRight = { required: false, initialOrder, initialIndex, preparedOrder: initialOrder, preparedIndex };
  if (initialIndex === 0) {
    const customMoveRight = page.getByRole("button", { name: "将先进封装验收右移" });
    await customMoveRight.focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(({ tagId, expectedIndex }) => [...document.querySelectorAll('[data-testid^="selected-tag-"]')]
      .findIndex((node) => node.getAttribute("data-testid") === `selected-tag-${tagId}`) === expectedIndex,
    { tagId: customTagId, expectedIndex: initialIndex + 1 });
    const preparedOrder = await selectedTagOrder();
    preparedIndex = preparedOrder.indexOf(customTagId);
    assert(preparedIndex === initialIndex + 1, `custom tag right-move preparation failed: ${initialIndex} -> ${preparedIndex}`);
    prepareCustomMoveRight = { required: true, initialOrder, initialIndex, preparedOrder, preparedIndex };
  }
  const beforeOrder = await selectedTagOrder();
  const beforeIndex = beforeOrder.indexOf(customTagId);
  assert(beforeIndex > 0, `custom tag cannot move left from index ${beforeIndex}`);
  const customMoveLeft = page.getByRole("button", { name: "将先进封装验收左移" });
  await customMoveLeft.focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(({ tagId, expectedIndex }) => [...document.querySelectorAll('[data-testid^="selected-tag-"]')]
    .findIndex((node) => node.getAttribute("data-testid") === `selected-tag-${tagId}`) === expectedIndex,
  { tagId: customTagId, expectedIndex: beforeIndex - 1 });
  const afterOrder = await selectedTagOrder();
  const afterIndex = afterOrder.indexOf(customTagId);
  assert(afterIndex === beforeIndex - 1, `custom tag left move was a no-op: ${beforeIndex} -> ${afterIndex}`);
  const focusState = await customTagContainer.evaluate((container) => ({
    remainsInsideCustomTag: container.contains(document.activeElement),
    focusedControlLabel: document.activeElement?.getAttribute("aria-label") ?? null,
  }));
  assert(focusState.remainsInsideCustomTag, "focus left the reordered custom tag");
  assert(focusState.focusedControlLabel?.includes("先进封装验收"), `unexpected reordered focus: ${focusState.focusedControlLabel}`);
  const focusedControlLabel = focusState.focusedControlLabel;
  record("keyboard-tag-reorder-focus", {
    customTagId, prepareCustomMoveRight, beforeOrder, afterOrder, beforeIndex, afterIndex, focusedControlLabel,
  });

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
  await runIndustryAction(
    "reload-restored-storage",
    "storage",
    () => page.reload({ waitUntil: "domcontentloaded" }),
  );

  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  const screenshotSwitch = await runIndustryAction(
    "final-storage-screenshot",
    "storage",
    () => page.getByRole("button", { name: "切换到存储" }).click(),
  );
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const stickyNavigation = page.locator("div.sticky").first();
  const switchStickyBox = await stickyNavigation.boundingBox();
  const switchOldBannerBox = await oldSnapshotBanner.boundingBox();
  const switchDemoBannerBox = await demoSnapshotBanner.boundingBox();
  assert(switchStickyBox && switchOldBannerBox && switchDemoBannerBox, "post-switch sticky/banner geometry unavailable");
  assert(
    switchOldBannerBox.y >= switchStickyBox.y + switchStickyBox.height,
    `old snapshot banner covered after switch: sticky=${JSON.stringify(switchStickyBox)} banner=${JSON.stringify(switchOldBannerBox)}`,
  );
  assert(
    switchDemoBannerBox.y >= switchStickyBox.y + switchStickyBox.height,
    `demo banner covered after switch: sticky=${JSON.stringify(switchStickyBox)} banner=${JSON.stringify(switchDemoBannerBox)}`,
  );
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert(await oldSnapshotBanner.isVisible(), "old snapshot banner not visible before screenshot");
  assert(await demoSnapshotBanner.isVisible(), "demo snapshot banner not visible before screenshot");
  const stickyBox = await stickyNavigation.boundingBox();
  const oldBannerBox = await oldSnapshotBanner.boundingBox();
  const demoBannerBox = await demoSnapshotBanner.boundingBox();
  assert(stickyBox && oldBannerBox && demoBannerBox, "screenshot sticky/banner geometry unavailable");
  const stickyBottom = stickyBox.y + stickyBox.height;
  const oldBannerTop = oldBannerBox.y;
  const demoBannerTop = demoBannerBox.y;
  assert(oldBannerTop >= stickyBottom, `old snapshot banner covered before screenshot: ${oldBannerTop} < ${stickyBottom}`);
  assert(demoBannerTop >= stickyBottom, `demo banner covered before screenshot: ${demoBannerTop} < ${stickyBottom}`);
  const bodyFont = await page.locator("body").evaluate((node) => Number.parseFloat(getComputedStyle(node).fontSize));
  const paragraphFont = await storageArticle.locator("p").first().evaluate((node) => Number.parseFloat(getComputedStyle(node).fontSize));
  assert(bodyFont >= 14 && paragraphFont >= 12, `readability floor violated: body=${bodyFont}, auxiliary=${paragraphFont}`);
  await page.screenshot({ path: path.join(outputDir, "01-industry-research-storage.png"), fullPage: false });
  record("desktop-1440x1100-readability-screenshot", {
    bodyFont,
    paragraphFont,
    stickyBottom,
    oldBannerTop,
    demoBannerTop,
    postSwitch: {
      action: screenshotSwitch.evidence,
      stickyBottom: switchStickyBox.y + switchStickyBox.height,
      oldBannerTop: switchOldBannerBox.y,
      demoBannerTop: switchDemoBannerBox.y,
    },
  });

  for (const viewport of [{ width: 1024, height: 768 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    assert(await storageArticle.isVisible(), `report hidden at ${viewport.width}x${viewport.height}`);
    const dimensions = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
    assert(dimensions.scrollWidth <= dimensions.clientWidth + 2, `page overflow at ${viewport.width}x${viewport.height}`);
  }
  record("responsive-1024-and-390");

  assert(consoleMessages.length === 0, `console errors/warnings: ${JSON.stringify(consoleMessages)}`);
  assert(pageErrors.length === 0, `page errors: ${JSON.stringify(pageErrors)}`);
  assert(blockingFailedRequests.length === 0, `blocking request failures: ${JSON.stringify(blockingFailedRequests)}`);
  assert(unexpectedNon2xxResponses.length === 0, `unexpected 4xx/5xx responses: ${JSON.stringify(unexpectedNon2xxResponses)}`);
  const responseStatusCounts = summarizeResponseStatuses();
  const expectedCancellationActions = summarizeExpectedCancellationActions();
  record("console-pageerror-requestfailed-classified", {
    expectedCancellationCount: expectedCancelledRequests.length,
    expectedCancellationActions,
    responseStatusCounts,
    unexpectedNon2xxResponses,
    blockingRequestFailureCount: blockingFailedRequests.length,
  });

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
    expectedCancelledRequests,
    blockingFailedRequests,
    network,
    responses,
    responseStatusCounts,
    unexpectedNon2xxResponses,
    expectedCancellationActions,
    industryActionEvidence,
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
    expectedCancelledRequests,
    blockingFailedRequests,
    network,
    responses,
    responseStatusCounts: summarizeResponseStatuses(),
    unexpectedNon2xxResponses,
    expectedCancellationActions: summarizeExpectedCancellationActions(),
    industryActionEvidence,
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
