import fs from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.join(root, "runs", "product-ui-verification");
const url = process.argv[2] || "http://127.0.0.1:8765/app.html";
await fs.mkdir(output, { recursive: true });

const selectors = {
  view: name => [`[data-product-view="${name}"].active`, `[data-testid="view-${name}"]:not([hidden])`],
  nav: name => [
    `nav [data-product-nav="${name}"]`,
    `[data-testid="nav-${name}"]`,
    `[data-product-nav="${name}"]`,
  ],
  taskSearch: ["[data-testid=task-search]", "#taskSearch"],
  taskRow: [
    "[data-testid=task-row]:is(a,button,[tabindex])",
    "[data-testid=task-row] :is(a,button,[tabindex])",
    "[data-task-row] button[data-product-nav]",
    "[data-task-row] a[href]",
    '[data-task-row][tabindex="0"]',
  ],
  createNext: ["[data-testid=create-next]", "#createNext"],
  createPrevious: ["[data-testid=create-previous]", "#createPrev"],
  reviewPrimary: ["[data-testid=review-primary-action]", "[data-review-action=save]", "[data-review-action=accept]"],
  highRiskCount: ["[data-testid=current-task-high-risk-count]", "[data-high-risk-count]"],
  reportOpen: ["[data-testid=open-report]", "[data-open-report]"],
  reportDialog: ["[data-testid=report-dialog]", "#productReportModal.open", '[role="dialog"][aria-modal="true"]'],
  reportClose: ["[data-testid=close-report]", ".product-modal-close", "[data-close-report]"],
};

async function firstVisible(page, candidates, description) {
  for (const selector of candidates) {
    const locator = page.locator(selector);
    const count = await locator.count();
    for (let index = 0; index < count; index += 1) {
      const item = locator.nth(index);
      if (await item.isVisible()) {
        const box = await item.boundingBox();
        const viewport = page.viewportSize();
        if (box && viewport && box.x + box.width > 0 && box.x < viewport.width) return item;
      }
    }
  }
  throw new Error(`Unable to find visible ${description}: ${candidates.join(" | ")}`);
}

async function waitForView(page, name) {
  await page.waitForFunction(expected => {
    const semantic = document.querySelector(`[data-product-view="${expected}"].active`);
    const testId = document.querySelector(`[data-testid="view-${expected}"]:not([hidden])`);
    return Boolean(semantic || testId);
  }, name);
  await firstVisible(page, selectors.view(name), `${name} product view`);
}

async function clickNavigation(page, name) {
  const viewport = page.viewportSize();
  if (viewport && viewport.width <= 760 && ["dashboard", "review", "assets", "reports"].includes(name)) {
    await page.locator(".ops-sidebar-toggle").click();
    await page.locator(`.ops-nav [data-product-nav="${name}"]`).click();
    await waitForView(page, name);
    return;
  }
  const nav = await firstVisible(page, selectors.nav(name), `${name} navigation item`);
  await nav.click();
  await waitForView(page, name);
}

async function assertCurrentGlobalNavigation(page, name) {
  const locator = page.locator(`nav [data-product-nav="${name}"],[data-testid="nav-${name}"]`).first();
  if (await locator.count()) {
    assert.equal(await locator.getAttribute("aria-current"), "page", `${name} nav item needs aria-current=page`);
  }
}

async function assertNoHorizontalOverflow(page, label) {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  assert.ok(
    Math.max(dimensions.document, dimensions.body) <= dimensions.viewport + 1,
    `${label} horizontally overflows: ${JSON.stringify(dimensions)}`,
  );
}

async function waitForAppliedReview(page) {
  await page.waitForFunction(() => (
    location.pathname.endsWith("/app.html")
    && location.hash === "#/review"
    && !new URLSearchParams(location.search).has("ui")
  ));
  await waitForView(page, "review");
  await assertCurrentGlobalNavigation(page, "review");
}

async function applyWorkbenchReviewTheme(page, workbenchUrl, { layout, density = "comfortable" }) {
  await page.goto(workbenchUrl, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelector("#evalcallPreview")?.innerHTML.length > 0);
  await page.click('[data-mode="design"]');
  await page.selectOption("#pageSelect", "review");
  await page.locator(`label.option-card:has(input[name="layout"][value="${layout}"])`).click();
  assert.equal(await page.locator(`input[name="layout"][value="${layout}"]`).isChecked(), true);
  await page.selectOption("#densitySelect", density);
  await page.click("#applyButton");
  await waitForAppliedReview(page);
  await page.reload({ waitUntil: "networkidle" });
  await waitForAppliedReview(page);
}

async function stableBlackListShellFingerprint(page) {
  return page.evaluate(() => {
    const isVisible = element => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const shell = document.querySelector(".ops-app-shell");
    const sidebar = document.querySelector(".ops-sidebar");
    const sidebarStyle = sidebar ? getComputedStyle(sidebar) : null;

    return {
      uiLayout: document.body.dataset.uiLayout || "missing",
      shellColumns: shell ? getComputedStyle(shell).gridTemplateColumns : "missing",
      sidebarVisible: isVisible(sidebar),
      sidebarWidth: sidebar ? Math.round(sidebar.getBoundingClientRect().width) : 0,
      sidebarBackground: sidebarStyle?.backgroundColor || "missing",
    };
  });
}

function assertStableBlackListShell(fingerprint, baselineWidth, view) {
  assert.equal(fingerprint.uiLayout, "linear", `${view} changed the global list shell`);
  assert.equal(fingerprint.sidebarVisible, true, `${view} hides the global black sidebar`);
  assert.ok(fingerprint.sidebarWidth >= 180, `${view} collapses the global sidebar: ${JSON.stringify(fingerprint)}`);
  assert.ok(
    Math.abs(fingerprint.sidebarWidth - baselineWidth) <= 1,
    `${view} changes the global sidebar width: ${JSON.stringify(fingerprint)}`,
  );
  const channels = fingerprint.sidebarBackground.match(/\d+(?:\.\d+)?/g)?.slice(0, 3).map(Number) || [];
  assert.equal(channels.length, 3, `${view} sidebar has no measurable background color`);
  assert.ok(channels.every(channel => channel <= 48), `${view} sidebar is not black: ${fingerprint.sidebarBackground}`);
}

async function activateProductView(page, name) {
  await page.locator(`[data-product-nav="${name}"]`).first().evaluate(element => element.click());
  await waitForView(page, name);
}

async function assertDashboardIsOperational(page) {
  const bodyText = await page.locator("body").innerText();
  assert.ok(!bodyText.includes("先统一测试条件"), "dashboard still contains reasoning/marketing copy");
  assert.equal(await page.locator(".overview-hero,.marketing-hero,[data-testid=marketing-hero]").count(), 0);

  const queue = await firstVisible(
    page,
    ["[data-testid=task-table]", ".ops-work-table", ".task-table"],
    "task work queue",
  );
  const box = await queue.boundingBox();
  assert.ok(box && box.height >= 100, "task queue must be a substantial dashboard region");
  assert.ok(box.y < 1000, "task queue must be available in the first desktop viewport");

  const primaryCount = await page.evaluate(() => {
    const active = document.querySelector('[data-product-view="dashboard"].active,[data-testid="view-dashboard"]:not([hidden])');
    if (!active) return -1;
    return new Set(active.querySelectorAll('.primary,[data-variant="primary"],[data-testid="primary-action"]')).size;
  });
  assert.ok(primaryCount >= 0 && primaryCount <= 1, `dashboard exposes ${primaryCount} primary actions`);
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const pageErrors = [];
page.on("pageerror", error => pageErrors.push(error.message));

try {
  await page.goto(url, { waitUntil: "networkidle" });
  await waitForView(page, "dashboard");
  await assertCurrentGlobalNavigation(page, "dashboard");
  await assertDashboardIsOperational(page);
  await assertNoHorizontalOverflow(page, "desktop dashboard");
  await page.screenshot({ path: path.join(output, "01-dashboard.png"), fullPage: true });

  // A work-queue row must be keyboard reachable and Enter must open its object.
  const firstTask = await firstVisible(page, selectors.taskRow, "task row");
  await firstTask.focus();
  assert.equal(await firstTask.evaluate(element => element === document.activeElement), true);
  await firstTask.press("Enter");
  await page.waitForFunction(() => document.body.dataset.productCurrent !== "dashboard");
  await clickNavigation(page, "dashboard");

  // Browser back/forward must preserve orientation inside the stable product shell.
  await clickNavigation(page, "review");
  await assertCurrentGlobalNavigation(page, "review");
  await page.goBack();
  await waitForView(page, "dashboard");
  await page.goForward();
  await waitForView(page, "review");
  await clickNavigation(page, "dashboard");

  await clickNavigation(page, "create");
  const futureStep = page.locator('[data-create-step="4"]').first();
  if (await futureStep.count()) {
    const blocked = await futureStep.evaluate(element => (
      element.hasAttribute("disabled") || element.getAttribute("aria-disabled") === "true"
    ));
    assert.equal(blocked, true, "future create steps must not be reachable before validation");
  }

  const taskName = page.locator("#evaluationName,[data-testid=evaluation-name]").first();
  if (await taskName.count()) {
    await taskName.fill("");
    await (await firstVisible(page, selectors.createNext, "create next action")).click();
    await firstVisible(page, ['[data-create-panel="1"].active', '[data-testid="create-step-1"]:not([hidden])'], "create step one");
    await taskName.fill("配送时间改约 · 验收任务");
    await page.waitForTimeout(2700);
  }

  for (let step = 2; step <= 4; step += 1) {
    await (await firstVisible(page, selectors.createNext, "create next action")).click();
    await firstVisible(
      page,
      [`[data-create-panel="${step}"].active`, `[data-testid="create-step-${step}"]:not([hidden])`],
      `create step ${step}`,
    );
  }
  await page.screenshot({ path: path.join(output, "02-create-confirm.png"), fullPage: true });
  assert.ok((await page.locator("body").innerText()).includes("配送时间改约 · 验收任务"));
  await (await firstVisible(page, selectors.createNext, "create and run action")).click();
  await waitForView(page, "dashboard");
  await page.waitForFunction(() => document.querySelector("#taskTableBody")?.innerText.includes("配送时间改约 · 验收任务"));
  await clickNavigation(page, "results");

  const resultText = await (await firstVisible(page, selectors.view("results"), "results view")).innerText();
  assert.match(resultText, /待复核|条件通过/);
  assert.ok(!resultText.includes("候选模型通过门禁"));
  assert.ok(!resultText.includes("建议灰度上线"));

  await clickNavigation(page, "review");
  await page.waitForTimeout(2700);
  await page.screenshot({ path: path.join(output, "03-review.png"), fullPage: true });
  const countElement = await firstVisible(page, selectors.highRiskCount, "current-task high-risk count");
  const countBefore = Number.parseInt((await countElement.textContent()) || "", 10);
  assert.ok(Number.isFinite(countBefore) && countBefore > 0);
  await (await firstVisible(page, selectors.reviewPrimary, "primary review action")).click();
  await page.waitForFunction(before => {
    const node = document.querySelector('[data-testid="current-task-high-risk-count"],[data-high-risk-count]');
    return node && Number.parseInt(node.textContent || "", 10) === before - 1;
  }, countBefore);

  await clickNavigation(page, "results");
  await (await firstVisible(page, selectors.reportOpen, "open report action")).click();
  await firstVisible(page, selectors.reportDialog, "report dialog");
  await page.screenshot({ path: path.join(output, "04-report-preview.png"), fullPage: true });
  await (await firstVisible(page, selectors.reportClose, "close report action")).click();
  await page.waitForTimeout(2700);

  await page.setViewportSize({ width: 390, height: 844 });
  await clickNavigation(page, "dashboard");
  await page.waitForTimeout(350);
  await assertNoHorizontalOverflow(page, "mobile dashboard");
  await page.screenshot({ path: path.join(output, "05-dashboard-mobile.png"), fullPage: true });

  await clickNavigation(page, "review");
  await page.waitForTimeout(350);
  await assertNoHorizontalOverflow(page, "mobile review workbench");
  await page.screenshot({ path: path.join(output, "06-review-mobile.png"), fullPage: true });

  // The selectable layout workbench is part of the user-facing acceptance surface.
  const workbenchUrl = new URL("b2b-pattern-workbench.html", url).href;
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(workbenchUrl, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelector("#evalcallPreview")?.innerHTML.length > 0);
  await page.click('[data-mode="reference"]');
  await page.waitForFunction(() => document.querySelector("#referenceFrame")?.src.includes("reference-replicas.html"));
  await page.screenshot({ path: path.join(output, "07-pattern-workbench.png"), fullPage: true });

  // Applying list-first once establishes one global shell. Page navigation may
  // change the work area, but it must never swap the black list navigation for
  // a governance rail or a builder header.
  await applyWorkbenchReviewTheme(page, workbenchUrl, { layout: "linear", density: "comfortable" });
  assert.equal(await page.locator("body").getAttribute("data-ui-layout"), "linear");
  assert.equal(await page.locator("body").getAttribute("data-ui-density"), "comfortable");
  const appliedStatus = await firstVisible(page, ["[data-theme-apply-status]"], "applied theme status");
  assert.match(await appliedStatus.innerText(), /列表优先/);
  assert.match(await appliedStatus.innerText(), /宽松/);

  const initialShell = await stableBlackListShellFingerprint(page);
  const sidebarWidth = initialShell.sidebarWidth;
  assertStableBlackListShell(initialShell, sidebarWidth, "review");
  const shellFingerprints = { review_initial: initialShell };
  for (const view of ["dashboard", "create", "results", "review", "assets", "reports"]) {
    await activateProductView(page, view);
    const fingerprint = await stableBlackListShellFingerprint(page);
    assertStableBlackListShell(fingerprint, sidebarWidth, view);
    shellFingerprints[view] = fingerprint;
  }
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(output, "08-applied-global-linear-shell.png"), fullPage: true });

  await page.setViewportSize({ width: 1024, height: 800 });
  for (const view of ["dashboard", "create", "results", "review"]) {
    await activateProductView(page, view);
    const fingerprint = await stableBlackListShellFingerprint(page);
    assertStableBlackListShell(fingerprint, sidebarWidth, `${view} at 1024px`);
    await assertNoHorizontalOverflow(page, `${view} with the global black shell at 1024px`);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await activateProductView(page, "reports");

  await page.reload({ waitUntil: "networkidle" });
  await waitForView(page, "reports");
  const refreshedShell = await stableBlackListShellFingerprint(page);
  assertStableBlackListShell(refreshedShell, sidebarWidth, "reports after refresh");
  assert.equal(await page.locator("body").getAttribute("data-ui-density"), "comfortable");
  shellFingerprints.reports_refreshed = refreshedShell;

  // Existing browsers may still hold the old per-page v2 theme. Loading it
  // must rewrite storage to the global v3 contract and discard white shells.
  await page.evaluate(() => localStorage.setItem("evalcall-b2b-theme", JSON.stringify({
    schema: "evalcall-b2b-theme/v2",
    active_page: "review",
    page_layouts: { task_center: "linear", create: "retool", result: "linear", review: "vanta" },
    density: "compact",
    color: "yellow",
    display: { summary: true, aside: true, lock: true },
    reference: "vanta",
  })));
  const migratedAppUrl = new URL("app.html", url);
  migratedAppUrl.searchParams.set("migration_test", "v2");
  migratedAppUrl.hash = "#/review";
  await page.goto(migratedAppUrl.href, { waitUntil: "networkidle" });
  await waitForView(page, "review");
  const migratedShell = await stableBlackListShellFingerprint(page);
  assertStableBlackListShell(migratedShell, sidebarWidth, "migrated v2 review");
  const migratedTheme = await page.evaluate(() => JSON.parse(localStorage.getItem("evalcall-b2b-theme") || "null"));
  assert.equal(migratedTheme.schema, "evalcall-b2b-theme/v3");
  assert.equal(migratedTheme.shell_layout, "linear");
  assert.deepEqual(Object.values(migratedTheme.page_layouts), ["linear", "linear", "linear", "linear"]);

  if (pageErrors.length) throw new Error(`Browser page errors: ${pageErrors.join(" | ")}`);
  console.log(JSON.stringify({
    ok: true,
    screenshots: output,
    contracts: ["operator-first dashboard", "keyboard queue", "browser history", "validated create flow", "scoped review count", "mobile viewport", "pattern workbench", "global black list shell", "applied theme persistence", "legacy theme migration"],
    shellFingerprints,
  }, null, 2));
} finally {
  await context.close();
  await browser.close();
}
