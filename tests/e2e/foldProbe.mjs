// The fold probe: a control the UI tells you to use must be on screen.
//
// Copy to `tests/foldProbe.mjs`. Adapted from ee-labs (packages/ui/verify),
// where it was written after a note said "drag R" while R sat 500 px below
// the bottom edge of a 1366x768 laptop.
//
// The Playwright suite runs at 1280x800 or 1920x1080, where everything fits.
// Amjad's laptop does not. This checks the case the suite cannot: for each
// screen, load it fresh, leave every scroller at the top the way a user finds
// it, and require the box of each named control to sit inside the viewport.
//
// A "named control" is anything the interface points at: the button an empty
// state offers, the field an error message blames, the input a tooltip or an
// onboarding step tells the user to change, the primary action of a form.
// If the app tells the user to touch it, it must be above the fold.
//
// Plain Node ESM, no framework imports, so it runs from any Playwright script.

/** The two laptop sizes that actually catch this. Add his real screen if it differs. */
export const LAPTOP_VIEWPORTS = [
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
]

export const NARROW_VIEWPORT = { width: 900, height: 800 } // rule: works from 900 px

/**
 * @param {import('playwright').Page} page
 * @param {object} opts
 * @param {{name:string, load:(page)=>Promise<void>, must:Array<string|((page)=>import('playwright').Locator)>}[]} opts.cases
 *   One entry per screen: `load` puts the app in that state from a fresh
 *   navigation (the probe navigates to `url` first); `must` lists the controls
 *   that screen names — CSS selectors or locator factories.
 * @param {string} opts.url  page to navigate to before each case
 * @param {{width:number,height:number}[]} [opts.viewports]
 * @param {string[]} [opts.scrollers]  selectors of internal scrollers to pin at top
 * @param {boolean} [opts.resetState]  clear localStorage/sessionStorage before each
 *   case. A fresh navigation is NOT a fresh state: an app that remembers a
 *   collapsed panel, a widened pane or a chosen tab carries it across the reload,
 *   so case 3 measures the layout case 2 left behind. Turn this on unless the
 *   case list is deliberately testing persistence.
 * @returns {Promise<{ok:boolean, failures:string[], measured:object[]}>}
 */
export async function foldProbe(page, { cases, url, viewports = LAPTOP_VIEWPORTS, scrollers = [], resetState = true }) {
  const failures = []
  const measured = []
  for (const vp of viewports) {
    await page.setViewportSize(vp)
    for (const c of cases) {
      await page.goto(url, { waitUntil: 'networkidle' })
      if (resetState) {
        await page.evaluate(() => {
          try { localStorage.clear(); sessionStorage.clear() } catch {}
        })
        await page.reload({ waitUntil: 'networkidle' })
      }
      await c.load(page)
      // A user arrives with every panel at the top. Pin them there so a
      // previous case's scroll position cannot flatter this one.
      await page.evaluate((sels) => {
        for (const sel of sels) {
          const el = document.querySelector(sel)
          if (el) el.scrollTop = 0
        }
        window.scrollTo(0, 0)
      }, scrollers)
      await page.waitForTimeout(60)
      for (const m of c.must) {
        const loc = typeof m === 'string' ? page.locator(m).first() : m(page).first()
        const label = typeof m === 'string' ? m : m.label || 'locator'
        const box = await loc.boundingBox().catch(() => null)
        measured.push({ viewport: `${vp.width}x${vp.height}`, screen: c.name, control: label, box })
        if (!box) {
          failures.push(`${vp.width}x${vp.height} · ${c.name} · ${label}: not rendered`)
          continue
        }
        const bottom = box.y + box.height
        const right = box.x + box.width
        if (box.y < 0 || bottom > vp.height) {
          failures.push(
            `${vp.width}x${vp.height} · ${c.name} · ${label}: bottom ${bottom.toFixed(0)} px > fold ${vp.height}`,
          )
        }
        if (box.x < 0 || right > vp.width) {
          failures.push(
            `${vp.width}x${vp.height} · ${c.name} · ${label}: right ${right.toFixed(0)} px > width ${vp.width}`,
          )
        }
      }
    }
  }
  return { ok: failures.length === 0, failures, measured }
}

/**
 * The other half of the same idea: nothing may be clipped by the shell.
 *
 * An app whose body does not scroll (the instrument shell in
 * references/theme.md) will happily render a toolbar wider than the screen and
 * simply hide its last buttons. Nothing raises, and no assertion notices.
 * Names any element in `selectors` whose box ends past the viewport.
 */
export async function clippedElements(page, selectors) {
  return page.evaluate((sels) => {
    const w = document.documentElement.clientWidth + 1
    const h = document.documentElement.clientHeight + 1
    const out = []
    for (const sel of sels) {
      for (const el of document.querySelectorAll(sel)) {
        const r = el.getBoundingClientRect()
        if (r.width === 0 && r.height === 0) continue
        if (r.right > w) out.push(`${sel} → right ${Math.round(r.right)} px > ${w - 1}`)
        else if (r.bottom > h) out.push(`${sel} → bottom ${Math.round(r.bottom)} px > ${h - 1}`)
      }
    }
    return out
  }, selectors)
}

/** True if the page itself scrolls. In the instrument shell, both must be false. */
export async function pageScrolls(page) {
  return page.evaluate(() => ({
    y: document.documentElement.scrollHeight > document.documentElement.clientHeight + 1,
    x: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }))
}
