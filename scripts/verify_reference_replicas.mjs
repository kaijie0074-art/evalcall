import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.join(root, "runs", "reference-replica-validation");
const baseUrl = process.argv[2] || "http://127.0.0.1:8765/reference-replicas.html";

const targets = [
  { view: "linear", id: "linear-my-issues", width: 1920, height: 868 },
  { view: "vanta", id: "vanta-risk-register", width: 1888, height: 980 },
  { view: "retool", id: "retool-admin-panel", width: 1920, height: 1148 },
];

await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const manifest = [];

try {
  for (const target of targets) {
    const context = await browser.newContext({
      viewport: { width: target.width, height: target.height },
      deviceScaleFactor: 1,
      colorScheme: "light",
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    const pageErrors = [];
    const consoleErrors = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    page.on("console", message => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });

    const url = new URL(baseUrl);
    url.searchParams.set("view", target.view);
    await page.goto(url.href, { waitUntil: "networkidle", timeout: 30_000 });
    await page.waitForSelector(`.replica-${target.view}`, { state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(120);

    const diagnostics = await page.evaluate(({ width, height, view }) => {
      const rootNode = document.querySelector("#replicaRoot");
      const replica = document.querySelector(`.replica-${view}`);
      const rootRect = rootNode?.getBoundingClientRect();
      const replicaRect = replica?.getBoundingClientRect();
      return {
        viewport: { width: innerWidth, height: innerHeight },
        document: {
          scrollWidth: document.documentElement.scrollWidth,
          scrollHeight: document.documentElement.scrollHeight,
        },
        root: rootRect && { x: rootRect.x, y: rootRect.y, width: rootRect.width, height: rootRect.height },
        replica: replicaRect && {
          x: replicaRect.x,
          y: replicaRect.y,
          width: replicaRect.width,
          height: replicaRect.height,
        },
        expected: { width, height },
        referenceId: document.documentElement.dataset.reference || null,
      };
    }, target);

    if (pageErrors.length || consoleErrors.length) {
      throw new Error(
        `${target.id} browser errors: ${[...pageErrors, ...consoleErrors].join(" | ")}`,
      );
    }
    if (
      diagnostics.viewport.width !== target.width ||
      diagnostics.viewport.height !== target.height ||
      diagnostics.document.scrollWidth !== target.width ||
      diagnostics.document.scrollHeight !== target.height
    ) {
      throw new Error(`${target.id} viewport overflow: ${JSON.stringify(diagnostics)}`);
    }

    const screenshot = path.join(output, `${target.id}.png`);
    await page.screenshot({ path: screenshot, fullPage: false, animations: "disabled" });
    manifest.push({
      ...target,
      url: url.href,
      screenshot: path.relative(root, screenshot),
      diagnostics,
    });
    await context.close();
  }
} finally {
  await browser.close();
}

const manifestFile = path.join(output, "capture-manifest.json");
await fs.writeFile(manifestFile, `${JSON.stringify({ generatedAt: new Date().toISOString(), targets: manifest }, null, 2)}\n`);
console.log(JSON.stringify({ ok: true, output: path.relative(root, output), targets: manifest }, null, 2));
