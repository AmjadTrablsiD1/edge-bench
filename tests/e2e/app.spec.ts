// Drives the real UI the way it is used: paste a path, read the table, change
// a setting, inspect a channel.  Runs once per theme (playwright.config.ts).
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import constants from "../../shared/constants.json" with { type: "json" };
import { clippedElements, foldProbe, LAPTOP_VIEWPORTS, pageScrolls } from "./foldProbe.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.resolve(HERE, "..", "fixtures");
const POWER = path.join(FIX, "power_up_sequence.csv");
const SHOTS = path.resolve(HERE, "..", "screenshots");

// One server serves the whole suite, so each test starts from an empty capture list.
async function clearServer(page: Page) {
  await page.evaluate(async () => {
    const { files } = await (await fetch("/api/files")).json();
    for (const f of files) await fetch(`/api/files/${f.id}`, { method: "DELETE" });
  });
}

const errors: string[] = [];
test.beforeEach(async ({ page, request }) => {
  // Clear through the API *before* any page exists, so no page can be polling
  // a capture that is being deleted.
  const { files } = await (await request.get("/api/files")).json();
  for (const f of files) await request.delete(`/api/files/${f.id}`);
  errors.length = 0;
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
});
test.afterEach(() => { expect(errors, errors.join("\n")).toEqual([]); });

async function openCapture(page: Page, p: string) {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.locator("[data-test=path-input]").fill(p);
  await page.locator("[data-test=path-input]").press("Enter");
  await expect(page.locator("[data-test=results]")).toBeVisible({ timeout: 20_000 });
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
}

test(".sal read directly: no Logic 2, every channel measured", async ({ page }, info) => {
  // A Logic 2 capture (Logic Pro 8, 250 MS/s) opened without Logic 2: the direct reader.
  await openCapture(page, path.join(FIX, "sal_corpus", "logic_pro_8_250000k_0.5s.sal"));
  await expect(page.locator(".doc .doc-meta")).toContainText("250 MS/s");
  await expect(page.locator("[data-test=dt-1]")).not.toHaveText("");
  await page.locator("[data-test=tab-coverage]").click();
  await expect(page.locator(".inspector")).toContainText("Read directly from the .sal (no Logic 2 export).");
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-13-sal-direct.png` });
  await axe(page);
});

test("updates: this source folder says git pull", async ({ page }) => {
  await page.goto("/");
  await page.locator("[data-test=tab-app]").click();
  const well = page.locator("[data-test=updates]");
  await expect(well.locator("[data-test=update-current]")).toHaveText(constants.app.version);
  await expect(well).toContainText("git pull");
  await expect(page.locator("[data-test=update-chip]")).toHaveCount(0);
  await axe(page);
});

test("updates: an update in the folder is offered, installed, and a failure is shown", async ({ page }, info) => {
  // The install itself is tested for real in tests/test_updater.py; here the UI around it.
  const next = { source: "S:/share/EdgeBench-9.1.0-windows-offline.zip", name: "EdgeBench-9.1.0-windows-offline.zip",
    version: "9.1.0", offline: true, newer: true,
    notes: [{ version: "9.1.0", date: "2026-12-01", items: ["Faster overview.", "Repeated cycles."] }] };
  await page.route("**/api/update", (r) => r.fulfill({ json: {
    current: constants.app.version, folder: "S:/share", available: next, error: null, source_checkout: false,
    previous: "0.2.0", last: { ok: false, kind: "update", from: constants.app.version, to: "9.0.0",
      error: "The new version did not pass its start-up check", time: Date.now() / 1000 } } }));
  let installed = "";
  await page.route("**/api/update/install", async (r) => {
    installed = JSON.parse(r.request().postData() ?? "{}").path;
    await r.fulfill({ json: { version: "9.1.0" } });
  });
  await page.goto("/");
  await expect(page.locator("[data-test=toast-error]")).toContainText("The update to 9.0.0 failed");
  await page.locator("[data-test=update-chip]").click();                       // header -> App tab
  const card = page.locator("[data-test=update-available]");
  await expect(card).toContainText("Edge Bench 9.1.0 is ready to install. Offline package");
  await expect(card).toContainText("Repeated cycles.");
  await expect(page.locator("[data-test=update-failed]")).toContainText(`start-up check. ${constants.app.version} is still installed.`);
  await expect(page.locator("[data-test=rollback]")).toHaveText("Roll back to 0.2.0");
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-14-update.png` });
  await axe(page);
  await page.locator("[data-test=update-install]").click();
  await expect(page.locator("[data-test=updating]")).toContainText("Updating to 9.1.0");
  expect(installed).toBe(next.source);
  errors.length = 0;                       // the page now waits for a restart; nothing else to check
});

test("heartbeat: a stopped server is shown as stopped, a busy one is not", async ({ page }, info) => {
  // From the Windows PC: when the memory guard ended the server, the page just went quiet.
  let mode: "busy" | "gone" | "back" = "busy";
  await page.route("**/api/whoami", (r) =>
    mode === "busy" ? r.fulfill({ status: 503, json: { detail: "busy" } })   // any reply = alive
    : mode === "gone" ? r.abort("connectionrefused")
    : r.continue());
  await page.goto("/");
  const stopped = page.locator("[data-test=server-stopped]");
  await page.waitForTimeout(constants.ui.heartbeat_ms * (constants.ui.heartbeat_misses + 1));
  await expect(stopped).toHaveCount(0);
  mode = "gone";
  await expect(stopped).toContainText("Edge Bench has stopped", { timeout: constants.ui.heartbeat_ms * (constants.ui.heartbeat_misses + 2) });
  await expect(page.locator("[data-test^=toast-]")).toHaveCount(0);           // no pile of error toasts
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-15-stopped.png` });
  await axe(page);
  mode = "back";
  await expect(page.locator("[data-test=empty]")).toBeVisible({ timeout: constants.ui.heartbeat_ms * 2 });
  // The browser logs each refused or 503 request; those are the test's doing.
  for (let i = errors.length - 1; i >= 0; i--) if (errors[i].includes("Failed to load resource")) errors.splice(i, 1);
});

test("empty state invites the first capture", async ({ page }, info) => {
  await page.goto("/");
  await expect(page.locator("[data-test=empty]")).toContainText("Open a Saleae capture");
  await expect(page.locator("[data-test=empty-add]")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-01-empty.png` });
  await axe(page);
});

test("power-up sequence: every Δt as built into the file", async ({ page }, info) => {
  await openCapture(page, POWER);
  await expect(page.locator("[data-test=ref-summary]")).toContainText("PWR_GOOD");
  // Ground truth from the generator: reset +2 ms, clock +2.15 ms, PLL +2.1884 ms, CS +2.6 ms
  await expect(page.locator("[data-test=dt-1]")).toHaveText("2 ms");
  await expect(page.locator("[data-test=dt-2]")).toHaveText("2.15 ms");
  await expect(page.locator("[data-test=dt-3]")).toHaveText("2.1884 ms");
  await expect(page.locator("[data-test=dt-4]")).toHaveText("2.6 ms");
  await expect(page.locator("[data-test=dt-5]")).toHaveText("2.6002 ms");
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-02-measured.png` });
  await axe(page);

  // Inspect a channel: the evidence panel traces the number to both edges.
  await page.locator("[data-test=row-3]").click();
  await expect(page.locator("[data-test=hero-dt]")).toContainText("2.1884");
  await expect(page.locator("[data-test=evidence]")).toContainText("PWR_GOOD rising #1 at 1.2 ms");
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-03-evidence.png` });
});

test("the reference line is drawn where the maths puts it", async ({ page }) => {
  await openCapture(page, POWER);
  await page.waitForTimeout(400);
  const vp = page.locator("[data-test=viewport]");
  const t0 = Number(await vp.getAttribute("data-t0"));
  const t1 = Number(await vp.getAttribute("data-t1"));
  const box = (await vp.boundingBox())!;
  // Same geometry as Waveform.tsx, recomputed here from constants.json.
  const k = Math.max(1, Math.min(2.2, box.width / 1150));
  const gutter = constants.waveform.label_gutter_px * k;
  const plotW = box.width - gutter - 18 * k;
  const tRef = 1.2e-3;
  const x = gutter + ((tRef - t0) / (t1 - t0)) * plotW;
  const hit = await page.evaluate(({ x }) => {
    const c = document.querySelector<HTMLCanvasElement>("[data-test=viewport] canvas")!;
    const dpr = window.devicePixelRatio || 1;
    const ctx = c.getContext("2d")!;
    const ref = getComputedStyle(document.documentElement).getPropertyValue("--q-ref").trim();
    const want = [1, 3, 5].map((i) => parseInt(ref.slice(i, i + 2), 16));
    let best = 1e9;
    for (let dx = -2; dx <= 2; dx++) {
      const col = ctx.getImageData(Math.round((x + dx) * dpr), 0, 1, c.height).data;
      for (let i = 0; i < col.length; i += 4) {
        const d = Math.abs(col[i] - want[0]) + Math.abs(col[i + 1] - want[1]) + Math.abs(col[i + 2] - want[2]);
        best = Math.min(best, d);
      }
    }
    return best;
  }, { x });
  expect(hit).toBeLessThan(40);   // the reference colour is found within ±2 px of the computed x
});

test("glitch filter, window and validation", async ({ page }) => {
  await openCapture(page, POWER);
  // LED bounces for 400 ns: unfiltered it is 3.1 ms, with a 500 ns filter it settles at 3.1004 ms
  await expect(page.locator("[data-test=dt-7]")).toHaveText("3.1 ms");
  await page.locator("[data-test=glitch]").fill("500");
  await expect(page.locator("[data-test=dt-7]")).toHaveText("3.1004 ms");
  // the 100 ns SPI clock pulses are glitches at that setting
  await expect(page.locator("[data-test=row-5]")).toContainText("No edge after reference");
  await page.locator("[data-test=glitch]").fill("");

  await page.locator("[data-test=window]").fill("2,5");           // German decimal comma
  await page.locator("#win + select").selectOption("ms");
  await expect(page.locator("[data-test=row-6]")).toContainText("No edge inside window");
  await expect(page.locator("[data-test=dt-1]")).toHaveText("2 ms");

  await page.locator("[data-test=window]").fill("abc");
  await expect(page.locator(".field-error")).toContainText("Not a number");
  await expect(page.locator("[data-test=export]")).toBeDisabled();
});

test("auto reference and excluded channels", async ({ page }) => {
  await openCapture(page, POWER);
  await page.locator("[data-test=ref-mode] button", { hasText: "First to change" }).click();
  await expect(page.locator("[data-test=ref-summary]")).toContainText("PWR_GOOD");
  await page.locator("[data-test=channel-chips] button", { hasText: "D0" }).click();      // exclude PWR_GOOD
  await expect(page.locator("[data-test=ref-summary]")).toContainText("RESET_N");
  await expect(page.locator("[data-test=dt-2]")).toHaveText("150 µs");
});

test("export through the server writes a workbook", async ({ page, request }) => {
  await openCapture(page, POWER);
  const files = (await (await request.get("/api/files")).json()).files;
  const id = files.find((f: { path: string }) => f.path.endsWith("power_up_sequence.csv")).id;
  const out = path.resolve(HERE, "..", "..", "test-results", "export dir", "power up.xlsx");
  const r = await request.post("/api/export", {
    data: { file_ids: [id], settings: { ref_channel: 0, ref_mode: "manual" }, unit: "µs", path: out },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  expect((await r.json()).path).toBe(out);
});

test("instrument shell: nothing scrolls, nothing clipped, controls above the fold", async ({ page }) => {
  test.setTimeout(120_000);
  const load = async (p: Page) => {
    await p.locator("[data-test=path-input]").fill(POWER);
    await p.locator("[data-test=path-input]").press("Enter");
    await expect(p.locator("[data-test=results]")).toBeVisible({ timeout: 20_000 });
  };
  const { failures } = await foldProbe(page, {
    url: "/",
    viewports: LAPTOP_VIEWPORTS,
    scrollers: [".inspector-scroll", ".file-list", ".table-wrap"],
    cases: [
      { name: "empty", load: async (p: Page) => { await clearServer(p); await p.reload(); }, must: ["[data-test=empty-add]", "[data-test=add-files]", "[data-test=path-input]"] },
      { name: "measured", load, must: ["[data-test=export]", "[data-test=ref-mode]", "[data-test=ref-channel]",
        "[data-test=target-edge]", "[data-test=window]", "[data-test=glitch]", "[data-test=results]", "[data-test=view-all]"] },
    ],
  });
  expect(failures, failures.join("\n")).toEqual([]);
  for (const vp of [...LAPTOP_VIEWPORTS, { width: 1920, height: 1080 }]) {
    await page.setViewportSize(vp);
    await page.waitForTimeout(100);
    expect(await pageScrolls(page)).toEqual({ x: false, y: false });
    expect(await clippedElements(page, [".app-header > *", ".toolbar > *", ".status-bar > *", ".inspector-tabs"])).toEqual([]);
  }
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.screenshot({ path: `${SHOTS}/laptop-1366.png` });
});

test("bad input says what is wrong", async ({ page }) => {
  await page.goto("/");
  await page.locator("[data-test=path-input]").fill(path.join(FIX, "does not exist.sal"));
  await page.locator("[data-test=path-input]").press("Enter");
  await expect(page.locator("[data-test=toast-error]")).toContainText("File not found");
  await expect(page.locator("[data-test=empty]")).toContainText("Could not open");
});

const NEW_FW = path.join(FIX, "power_up_new_fw.csv");

async function openPair(page: Page, b: string = NEW_FW) {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  for (const p of [POWER, b]) {
    await page.locator("[data-test=path-input]").fill(p);
    await page.locator("[data-test=path-input]").press("Enter");
    await expect(page.locator("[data-test=file-item]", { hasText: path.basename(p) })).toContainText("CSV", { timeout: 20_000 });
  }
  await page.locator("[data-test=mode] button", { hasText: "Compare" }).click();
  await expect(page.locator("[data-test=compare-results]")).toBeVisible({ timeout: 20_000 });
}

test("compare: the planted firmware differences are found", async ({ page }, info) => {
  await openPair(page);
  await expect(page.locator("[data-test=compare-summary]")).toHaveText(/2\s*critical\s*3\s*changed\s*3\s*same/);
  await expect(page.locator("[data-test=pick-a]")).toHaveValue(/\d+/);
  await expect(page.locator("[data-test=cmp-row-1]")).toContainText("Changed");
  await expect(page.locator("[data-test=cmp-delta-1]")).toHaveText("+500 µs");
  await expect(page.locator("[data-test=cmp-row-3]")).toContainText("Critical");
  await expect(page.locator("[data-test=cmp-row-6]")).toContainText("Critical");
  await expect(page.locator("[data-test=cmp-row-2]")).toContainText("Same");
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-04-compare.png` });
  await axe(page);

  await page.locator("[data-test=cmp-row-3]").click();
  await expect(page.locator("[data-test=reasons]")).toContainText("Edge disappeared");
  await expect(page.locator("[data-test=reasons]")).toContainText("Activity stopped");
  await page.locator("[data-test=cmp-row-7]").click();
  await expect(page.locator("[data-test=reasons]")).toContainText("Shorter pulses than baseline: shortest 40 ns → 10 ns");
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-05-compare-detail.png` });
});

test("compare: swap, tolerance and back to measure", async ({ page }) => {
  await openPair(page);
  await page.locator("[data-test=swap]").click();
  await expect(page.locator("[data-test=cmp-delta-1]")).toHaveText("-500 µs");
  await page.locator("[data-test=swap]").click();
  await expect(page.locator("[data-test=cmp-delta-1]")).toHaveText("+500 µs");

  // 500 us shift is inside a ±600 us delay tolerance
  await page.locator("[data-test=tol-delay]").fill("600");
  await expect(page.locator("[data-test=cmp-row-1]")).toContainText("Same");
  await expect(page.locator("[data-test=cmp-row-3]")).toContainText("Critical");   // structure still flagged
  await page.locator("[data-test=tol-delay]").fill("x");
  await expect(page.locator("[data-test=compare-settings] .field-error")).toContainText("Not a number");
  await expect(page.locator("[data-test=export]")).toBeDisabled();
  await page.locator("[data-test=tol-delay]").fill("1");

  await page.locator("[data-test=mode] button", { hasText: "Measure" }).click();
  await expect(page.locator("[data-test=results]")).toBeVisible();
});

test("compare: shell stays an instrument", async ({ page }) => {
  test.setTimeout(120_000);
  const { failures } = await foldProbe(page, {
    url: "/",
    viewports: LAPTOP_VIEWPORTS,
    scrollers: [".inspector-scroll", ".file-list", ".table-wrap"],
    cases: [{ name: "compare", load: async (p: Page) => { await clearServer(p); await openPair(p); },
      must: ["[data-test=mode]", "[data-test=pick-a]", "[data-test=pick-b]", "[data-test=swap]",
             "[data-test=export]", "[data-test=compare-results]", "[data-test=compare-summary]"] }],
  });
  expect(failures, failures.join("\n")).toEqual([]);
  expect(await pageScrolls(page)).toEqual({ x: false, y: false });
  expect(await clippedElements(page, [".app-header > *", ".toolbar > *", ".status-bar > *"])).toEqual([]);
  await page.screenshot({ path: `${SHOTS}/compare-laptop.png` });
});

test("compare: probes moved between captures, matched by name", async ({ page }, info) => {
  await openPair(page, path.join(FIX, "power_up_new_fw_rewired.csv"));
  // By number pairs the wrong signals and says so
  await expect(page.locator("[data-test=compare-evidence]")).toContainText("try matching by name");
  await page.locator("[data-test=match-by] button", { hasText: "By name" }).click();
  await expect(page.locator("[data-test=compare-summary]")).toHaveText(/2\s*critical\s*3\s*changed\s*3\s*same/);
  await expect(page.locator("[data-test=cmp-ch-1]")).toHaveText("D1 ↔ D3");             // RESET_N -> Reset_N on D3
  await expect(page.locator("[data-test=cmp-row-1]")).toContainText("Reset_N");
  await expect(page.locator("[data-test=cmp-delta-1]")).toHaveText("+500 µs");
  await expect(page.locator("[data-test=cmp-row-3]")).toContainText("Critical");         // PLL_LOCK on D7 in B
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-06-compare-by-name.png` });
  await axe(page);
  // the choice is remembered
  await page.reload();
  await expect(page.locator("[data-test=cmp-ch-1]")).toHaveText("D1 ↔ D3", { timeout: 20_000 });
});
