import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = process.argv[2] || path.join(root, "submission", "EvalCall决赛提交包-20260715", "09_EvalCall应急演示录屏.webm");
const token = (await fs.readFile(path.join(process.env.HOME, ".evalcall", "public-access-token"), "utf8")).trim();
const endpoint = await fetch("https://kaijie0074-art.github.io/evalcall/live-endpoint.json", { cache: "no-store" }).then((response) => response.json());
const gateway = String(endpoint.live_url || "").replace(/\/$/, "") + "/?access=" + encodeURIComponent(token);
const videoDir = path.join(path.dirname(output), ".video-tmp");
await fs.rm(videoDir, { recursive: true, force: true });
await fs.mkdir(videoDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: videoDir, size: { width: 1440, height: 900 } },
});
const page = await context.newPage();
page.setDefaultTimeout(30000);
const pause = (ms) => page.waitForTimeout(ms);

async function executeStep(step, hold = 1500) {
  await page.click("#execute");
  await page.waitForFunction(
    (index) => document.querySelector(`[data-step="${index}"]`)?.classList.contains("done"),
    step,
    { timeout: 30000 },
  );
  await pause(hold);
}

try {
  await page.goto(gateway, { waitUntil: "networkidle" });
  await pause(3500);
  await Promise.all([page.waitForNavigation({ waitUntil: "domcontentloaded" }), page.click("#live")]);
  await page.waitForSelector("#execute");
  await page.waitForFunction(() => document.querySelector("#truthBadge")?.textContent.includes("禁止缓存"), null, { timeout: 90000 });
  await pause(2200);

  await page.click("#liveMode");
  await page.click('[data-preset="t02"]');
  for (let step = 1; step <= 3; step += 1) {
    await executeStep(step, step === 3 ? 3200 : 1600);
    if (step < 3) await page.click("#next");
  }

  await page.click("#cacheMode");
  await pause(1200);
  for (let step = 1; step <= 6; step += 1) {
    const hold = step === 4 ? 4200 : step >= 5 ? 3000 : 1300;
    await executeStep(step, hold);
    if (step < 6) await page.click("#next");
  }
  await pause(2500);
} finally {
  const video = page.video();
  await context.close();
  await browser.close();
  const recorded = await video.path();
  await fs.copyFile(recorded, output);
  await fs.rm(videoDir, { recursive: true, force: true });
}

console.log(output);
