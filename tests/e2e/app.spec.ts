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

const UPDATE_EXTRA = {
  github: { enabled: true, repo: "AmjadTrablsiD1/edge-bench", url: "https://github.com/AmjadTrablsiD1/edge-bench/releases",
            state: "done", error: null, checked: Date.now() / 1000 - 120 },
  download: { state: "idle", version: null, done: 0, total: null, error: null },
};

test("updates: a GitHub release is offered, downloaded with progress, and installed", async ({ page }, info) => {
  // The download, checksum and install are tested for real in tests/test_updater.py; here the UI around them.
  const remote = { source: "github:v9.2.0", name: "GitHub release v9.2.0", version: "9.2.0", offline: false, newer: true,
    remote: true, url: "https://github.com/AmjadTrablsiD1/edge-bench/releases/tag/v9.2.0",
    notes: [{ version: "9.2.0", date: "2026-12-10", items: ["Updates straight from GitHub."] }] };
  let state: any = { ...UPDATE_EXTRA, current: constants.app.version, folder: null, available: remote, error: null,
    source_checkout: false, previous: null, last: null };
  let steps = 0, asked = "", checked = 0;
  await page.route("**/api/update", (r) => {
    if (state.download.state === "downloading" && ++steps > 3) {      // the server moves on by itself
      const done = Math.min(state.download.done + 60e6, 161e6);
      state = { ...state, download: { ...state.download, done, state: done >= 161e6 ? "installing" : "downloading" } };
    }
    return r.fulfill({ json: state });
  });
  await page.route("**/api/update/check", (r) => { checked++; return r.fulfill({ json: state }); });
  await page.route("**/api/update/download", (r) => {
    asked = JSON.parse(r.request().postData() ?? "{}").version;
    state = { ...state, download: { state: "downloading", version: asked, done: 0, total: 161e6, error: null } };
    return r.fulfill({ json: state });
  });
  await page.goto("/");
  await expect(page.locator("[data-test=update-chip]")).toHaveText(/Update 9\.2\.0/);
  await expect(page.locator("[data-test=update-chip]")).toHaveAttribute("title", /on GitHub/);
  await page.locator("[data-test=update-chip]").click();
  const well = page.locator("[data-test=updates]");
  await expect(well.locator("[data-test=update-github]")).toHaveText("AmjadTrablsiD1/edge-bench · checked 2 min ago");
  await expect(well.locator("[data-test=update-available]")).toContainText("Edge Bench 9.2.0 is available on GitHub.");
  await expect(well.locator("[data-test=update-available]")).toContainText("Updates straight from GitHub.");
  await well.locator("[data-test=update-check]").click();
  expect(checked).toBe(1);
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-19-github.png` });
  await axe(page);
  await well.locator("[data-test=update-install]").click();
  expect(asked).toBe("9.2.0");
  await expect(well.locator("[data-test=update-progress]")).toContainText("Downloading 9.2.0 — 0.0 MB of 161 MB");
  await expect(well.locator("[data-test=update-progress] [role=progressbar]")).toBeVisible();
  await axe(page);
  await expect(page.locator("[data-test=updating]")).toContainText("Updating to 9.2.0", { timeout: 15_000 });
  errors.length = 0;                       // the page now waits for a restart
});

test("updates: GitHub not reachable, a failed download, and switching GitHub off", async ({ page }) => {
  let state: any = { ...UPDATE_EXTRA, current: constants.app.version, folder: null, available: null, error: null,
    source_checkout: false, previous: null, last: null,
    github: { ...UPDATE_EXTRA.github, state: "error", error: "Cannot reach GitHub (timed out). Check the internet connection or the proxy; an update folder or a downloaded zip works without it." },
    download: { state: "error", version: "9.2.0", done: 0, total: null, error: "EdgeBench-9.2.0-windows.zip did not arrive intact (its checksum does not match); nothing was installed" } };
  let switched: boolean | null = null;
  await page.route("**/api/update", (r) => r.fulfill({ json: state }));
  await page.route("**/api/update/github", (r) => {
    switched = JSON.parse(r.request().postData() ?? "{}").enabled;
    state = { ...state, github: { ...state.github, enabled: false, state: "off", error: null } };
    return r.fulfill({ json: state });
  });
  await page.goto("/");
  await page.locator("[data-test=tab-app]").click();
  const well = page.locator("[data-test=updates]");
  await expect(well.locator("[data-test=update-github]")).toHaveText("AmjadTrablsiD1/edge-bench · not reachable");
  await expect(well.locator("[data-test=update-github-error]")).toContainText("Cannot reach GitHub");
  await expect(well.locator("[data-test=update-download-error]")).toContainText("Downloading 9.2.0 failed: EdgeBench-9.2.0-windows.zip did not arrive intact");
  await expect(well.locator("[data-test=update-download-error]")).toContainText("Nothing was changed.");
  await axe(page);
  await well.locator("[data-test=update-github-switch] button", { hasText: "Off" }).click();
  expect(switched).toBe(false);
  await expect(well.locator("[data-test=update-github]")).toHaveText("off");
  await expect(well.locator("[data-test=update-check]")).toHaveCount(0);
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
    ...UPDATE_EXTRA,
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

test("fast indexing: status, switch off and on, install when it is missing", async ({ page }, info) => {
  await page.goto("/");
  await page.locator("[data-test=tab-app]").click();
  const status = page.locator("[data-test=fast-status]");
  await expect(status).toHaveText(/^On — compiled for this PC \(numba [\d.]+\)$/, { timeout: 30_000 });   // loads in the background
  await page.locator("[data-test=fast-switch] button", { hasText: "Off" }).click();
  await expect(status).toHaveText("Off");
  expect((await (await page.request.get("/api/status")).json()).fast.active).toBe(false);
  await page.locator("[data-test=fast-switch] button", { hasText: "On" }).click();
  await expect(status).toHaveText(/^On — compiled/);
  await expect(page.locator("[data-test=fast-install]")).toHaveCount(0);
  await page.locator("[data-test=fast]").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-17-fast.png` });
  await axe(page);

  // a PC where the optional package is not there: said plainly, with a way to get it
  let installed = false;
  const missing = { wanted: true, ready: true, active: false, installed: false, load_s: 0.1, text: "Not active — not installed (No module named 'numba')" };
  await page.route("**/api/status", async (r) => {
    const body = await (await r.fetch()).json();
    await r.fulfill({ json: installed ? body : { ...body, fast: missing } });
  });
  await page.route("**/api/fast/install", async (r) => { installed = true; await r.fulfill({ json: { ...missing, active: true, installed: true, text: "On — compiled for this PC (numba 0.68.1)" } }); });
  await page.reload();
  await page.locator("[data-test=tab-app]").click();
  await expect(status).toContainText("not installed");
  await page.locator("[data-test=fast-install]").click();
  await expect(page.locator("[data-test=toast-ok]")).toContainText("Fast indexing is on.");
  await expect(status).toHaveText(/^On — compiled/);
  await expect(page.locator("[data-test=fast-install]")).toHaveCount(0);
});

// Where time t sits on the waveform overlay (the same geometry as Waveform.tsx).
async function xOf(page: Page, t: number) {
  const ov = page.locator("[data-test=wave-overlay]");
  const box = (await ov.boundingBox())!;
  const vp = page.locator("[data-test=viewport]");
  const t0 = Number(await vp.getAttribute("data-t0")), t1 = Number(await vp.getAttribute("data-t1"));
  const k = Math.max(1, Math.min(2.2, box.width / 1150));
  const gutter = constants.waveform.label_gutter_px * k, plotW = box.width - gutter - 18 * k;
  return { x: box.x + gutter + ((t - t0) / (t1 - t0)) * plotW, y: box.y + box.height * 0.4, t0, t1, box };
}

const cursorSeconds = async (page: Page, which = "a") => {
  const box = page.locator(`[data-test=cursor-${which}]`);
  const unit = await box.locator("xpath=following-sibling::select").inputValue();
  const scale = constants.units.time.find((u: { label: string }) => u.label === unit)!.scale;
  return Number(await box.inputValue()) * scale;
};

test("cursor reference: double-click places it, drag moves it, typing sets it", async ({ page }, info) => {
  await openCapture(page, POWER);
  const vp = page.locator("[data-test=viewport]");
  const before = [await vp.getAttribute("data-t0"), await vp.getAttribute("data-t1")];
  const { box, t0, t1 } = await xOf(page, 0);
  const k = Math.max(1, Math.min(2.2, box.width / 1150));
  const gutter = constants.waveform.label_gutter_px * k, plotW = box.width - gutter - 18 * k;

  // 1. double-click a third of the way in: the reference becomes that time
  await page.mouse.dblclick(box.x + gutter + plotW / 3, box.y + box.height * 0.4);
  await expect(page.locator("[data-test=ref-mode] button[aria-checked=true]")).toHaveText("Cursor");
  await expect(page.locator("[data-test=ref-summary]")).toContainText("Reference Cursor at");
  const placed = await cursorSeconds(page);
  expect(Math.abs(placed - (t0 + (t1 - t0) / 3))).toBeLessThan((t1 - t0) / plotW * 2);    // where it was clicked
  expect([await vp.getAttribute("data-t0"), await vp.getAttribute("data-t1")]).toEqual(before);   // no jump
  // every Δt is the server's answer for that time
  const ask = async (t: number) => (await (await page.request.post("/api/measure", { data: {
    file_id: (await (await page.request.get("/api/files")).json()).files[0].id,
    settings: { ref_mode: "cursor", cursor_s: t, target_edge: "any" } } })).json());
  const want = await ask(placed);
  const ok = want.channels.filter((c: { status: string }) => c.status === "ok");
  expect(ok.length).toBeGreaterThan(0);
  for (const c of ok) await expect(page.locator(`[data-test=dt-${c.index}]`)).not.toHaveText("—");
  await expect(page.locator("[data-test=ref-edge]")).toHaveCount(0);           // no edge or occurrence to set
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-18-cursor.png` });
  await axe(page);

  // 2. drag the cursor line 120 px to the right: it moves, the view does not pan
  const at = await xOf(page, placed);
  await page.mouse.move(at.x, at.y);
  await page.mouse.down();
  await page.mouse.move(at.x + 60, at.y, { steps: 4 });
  await page.mouse.move(at.x + 120, at.y, { steps: 4 });
  await page.mouse.up();
  await expect.poll(() => cursorSeconds(page)).toBeGreaterThan(placed + (t1 - t0) / plotW * 100);
  expect([await vp.getAttribute("data-t0"), await vp.getAttribute("data-t1")]).toEqual(before);

  // 3. type it: 0 = the capture start, every channel's first edge
  await page.locator("[data-test=cursor-a]").fill("0");
  await expect(page.locator("[data-test=ref-summary]")).toContainText("Reference Cursor at 0");
  // and back to a channel: that setting was kept
  await page.locator("[data-test=ref-mode] button", { hasText: "Channel" }).click();
  await expect(page.locator("[data-test=ref-summary]")).not.toContainText("Cursor");
  await page.locator("[data-test=cursor-a]").waitFor({ state: "detached" });
});

test("cursor reference in Compare: a cursor in each capture", async ({ page }) => {
  await openPair(page);
  await expect(page.locator("[data-test=compare-summary]")).toHaveText(/2\s*critical\s*3\s*changed\s*3\s*same/);
  await page.locator("[data-test=ref-mode] button", { hasText: "Cursor" }).click();
  await expect(page.locator("[data-test=compare-settings]")).toBeVisible();
  await page.locator("[data-test=cursor-a]").fill("0");
  await expect(page.locator("[data-test=cursor-b]")).toHaveAttribute("placeholder", "not placed");
  await expect(page.locator("[data-test=compare-evidence]")).toContainText("no cursor of its own");
  await page.locator("[data-test=cursor-b]").fill("0");
  await expect(page.locator("[data-test=compare-evidence]")).not.toContainText("no cursor of its own");
  await expect(page.locator("[data-test=compare-results]")).toBeVisible();
  // double-click in the compare view: both cursors move by the same time after their references
  const ov = page.locator("[data-test=compare-viewport] canvas.overlay");
  const box = (await ov.boundingBox())!;
  await page.mouse.dblclick(box.x + box.width * 0.6, box.y + box.height * 0.3);
  await expect.poll(() => cursorSeconds(page, "a")).toBeGreaterThan(0);
  expect(await cursorSeconds(page, "b")).toBeCloseTo(await cursorSeconds(page, "a"), 9);   // both were at 0
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

test("compare: two .sal captures, by their own channel names", async ({ page }, info) => {
  // Old and new firmware of one board, both read directly from the .sal.
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  for (const name of ["board_a.sal", "board_b.sal"]) {
    await page.locator("[data-test=path-input]").fill(path.join(FIX, name));
    await page.locator("[data-test=path-input]").press("Enter");
    await expect(page.locator("[data-test=file-item]", { hasText: name })).toContainText("100 MS/s", { timeout: 20_000 });
  }
  await page.locator("[data-test=mode] button", { hasText: "Compare" }).click();
  await page.locator("[data-test=ref-channel]").selectOption({ label: "D4 · PWR_GOOD" });
  await expect(page.locator("[data-test=compare-summary]")).toHaveText(/2\s*critical\s*1\s*changed\s*5\s*same/, { timeout: 20_000 });
  await expect(page.locator("[data-test=cmp-row-6]")).toContainText("PLL_LOCK");
  await expect(page.locator("[data-test=cmp-row-6]")).toContainText("Edge disappeared");
  await expect(page.locator("[data-test=cmp-row-3]")).toContainText("PWM_FAN");
  await expect(page.locator("[data-test=cmp-row-3]")).toContainText("Edge count 9600 → 7799");
  await expect(page.locator("[data-test=cmp-delta-5]")).toHaveText("+20 µs");          // RESET_N: moved, inside tolerance
  await expect(page.locator("[data-test=cmp-row-5]")).toContainText("Same");
  await expect(page.locator("[data-test=compare-busy]")).toHaveCount(0);
  await expect(page.locator("[data-test=compare-status]")).toHaveCount(0);
  await page.waitForTimeout(400);                                              // both waveforms drawn
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-16-compare-sal.png` });
  await axe(page);
});

test("compare: a slow answer shows the measured edges first, and says what is still coming", async ({ page }) => {
  // The first full comparison of a very large binary export reads it through once.
  let release!: () => void;
  const held = new Promise<void>((r) => { release = r; });
  await page.route("**/api/compare", async (r) => {
    if (JSON.parse(r.request().postData() ?? "{}").activity !== false) await held;
    await r.continue();
  });
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  for (const p of [POWER, NEW_FW]) {
    await page.locator("[data-test=path-input]").fill(p);
    await page.locator("[data-test=path-input]").press("Enter");
    await expect(page.locator("[data-test=file-item]", { hasText: path.basename(p) })).toContainText("CSV", { timeout: 20_000 });
  }
  await page.locator("[data-test=mode] button", { hasText: "Compare" }).click();
  await expect(page.locator("[data-test=compare-busy]")).toContainText("Comparing A and B");
  await expect(page.locator("[data-test=compare-status]")).toHaveText("Comparing…");
  // the quick answer: the edges are compared, activity is not there yet
  await expect(page.locator("[data-test=compare-pending]")).toContainText("Only the measured edges are compared so far");
  await expect(page.locator("[data-test=compare-status]")).toContainText("still checking activity");
  await expect(page.locator("[data-test=compare-busy]")).toHaveCount(0);
  await expect(page.locator("[data-test=cmp-delta-1]")).toHaveText("+500 µs");
  await axe(page);
  await page.locator("[data-test=cmp-row-3]").click();
  await expect(page.locator("[data-test=reasons]")).toContainText("Edge disappeared");
  await expect(page.locator("[data-test=reasons]")).not.toContainText("Activity stopped");
  await expect(page.locator("[data-test=compare-evidence]")).toContainText("Edges in span——");   // not counted yet
  release();
  await expect(page.locator("[data-test=reasons]")).toContainText("Activity stopped");            // the full answer
  await expect(page.locator("[data-test=compare-status]")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.locator("[data-test=compare-pending]")).toHaveCount(0);
  await expect(page.locator("[data-test=compare-summary]")).toHaveText(/2\s*critical\s*3\s*changed\s*3\s*same/);
});

test("compare: an error is said, not a spinner forever", async ({ page }) => {
  await page.route("**/api/compare", (r) => r.fulfill({ status: 422, json: { detail: "The reference channel is not in capture B." } }));
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  for (const p of [POWER, NEW_FW]) {
    await page.locator("[data-test=path-input]").fill(p);
    await page.locator("[data-test=path-input]").press("Enter");
    await expect(page.locator("[data-test=file-item]", { hasText: path.basename(p) })).toContainText("CSV", { timeout: 20_000 });
  }
  await page.locator("[data-test=mode] button", { hasText: "Compare" }).click();
  await expect(page.locator("[data-test=compare-error]")).toContainText("The reference channel is not in capture B.");
  await expect(page.locator("[data-test=compare-busy]")).toHaveCount(0);
  for (let i = errors.length - 1; i >= 0; i--) if (errors[i].includes("Failed to load resource")) errors.splice(i, 1);
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
