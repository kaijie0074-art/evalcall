import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.join(root, "design-reference", "product-ui");
const deployOutput = path.join(root, "site-deploy", "reference-assets");

const references = [
  {
    id: "linear-my-issues",
    url: "https://webassets.linear.app/images/ornj730p/production/70c22a56e776bfbffa920091b64a28845ca8eaeb-1864x842.png?auto=format&dpr=2&q=95&w=1440",
  },
  {
    id: "vanta-risk-register",
    url: "https://cdn.prod.website-files.com/64009032676f244c7bf002fd/6491d0f725fa6525807bac1b_Customize%20Risk%20Register.png",
  },
  {
    id: "retool-admin-panel",
    url: "https://dqpcjghenxt8u.cloudfront.net/contentful-data/images/3zhMDz9ruOgiVlWUOwyqfZ/8602169a8890bc819dbdea30193e6665/Frame_520.png",
  },
];

await fs.mkdir(output, { recursive: true });
await fs.mkdir(deployOutput, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1200 }, deviceScaleFactor: 1 });

try {
  const manifest = [];
  for (const reference of references) {
    const page = await context.newPage();
    await page.goto(reference.url, { waitUntil: "networkidle", timeout: 60_000 });
    const image = page.locator("img").first();
    await image.waitFor({ state: "visible" });
    const dimensions = await image.evaluate(node => ({ width: node.naturalWidth, height: node.naturalHeight }));
    const file = path.join(output, `${reference.id}.png`);
    await image.screenshot({ path: file });
    await fs.copyFile(file, path.join(deployOutput, `${reference.id}.png`));
    manifest.push({ ...reference, file: path.relative(root, file), ...dimensions });
    await page.close();
  }
  console.log(JSON.stringify(manifest, null, 2));
} finally {
  await context.close();
  await browser.close();
}
