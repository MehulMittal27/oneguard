import type { ReactNode } from 'react'
import { DEEP_LINK } from '../lib/deepLink'

/**
 * Presentational-only wrapper (D-065, refined D-066 through D-074) — no
 * screen inside it changes. Below `sm` (640px, comfortably above any
 * real phone viewport, so this never triggers on an actual phone)
 * children render exactly as before: full-bleed, `h-screen`. At `sm` and
 * up the same 390px shell is drawn as a literal phone — bezel, a
 * realistic height capped by both a fixed max and the viewport height so
 * it never overflows a short browser window — instead of stretching to
 * fill a desktop window.
 *
 * Uniform padding (`sm:p-7` = 16px, D-074 — a deliberately thinner
 * bezel than D-073's `p-8`/20px) on all four sides; the screen's own
 * radius is set by the standard concentric formula (outer − padding =
 * inner: 50 − 16 = 34) so the bezel's curve and the screen's curve
 * share a center and never visually mismatch. Remember this project
 * remaps `p-1..9`/`h-1..9` to DESIGN.md's own irregular scale
 * (`tokens.css`'s `--space-1..9`), not Tailwind's default — D-073's
 * whole fix was correcting a prior version of this file that assumed
 * otherwise, so if either the padding or either radius changes again,
 * recompute this formula against the *actual* `--space-N` value, not
 * the number in the class name. Nothing in this component adds height
 * to one side only — that asymmetry (a now-removed decorative notch
 * row) was the actual cause of a corner mismatch in an earlier version;
 * keeping this component simple is deliberate, not an oversight.
 *
 * The "content shouldn't touch the top edge while scrolling" fix lives
 * one level up, in each screen-shell wrapper (`App.tsx` ×2, `SignIn.tsx`)
 * — not here — because only those wrappers know whether the screen's own
 * background is light or dark; a spacer added inside this component has
 * no way to match that and would show as a mismatched colored bar
 * instead of a natural margin.
 *
 * `sm:transform-gpu` deliberately applies a CSS transform, which makes
 * that element the containing block for `position: fixed` descendants
 * (the CSS spec's own mechanism, not a hack) — so `BottomSheet` and
 * everything built on it (`RevokeSheet`, `CardPicker`) slide up within
 * the phone's own bounds at desktop width instead of covering the whole
 * browser window. **It belongs on the screen div, not the bezel div
 * (D-098).** On the bezel it made `fixed inset-0` resolve to the bezel's
 * *padding* box — 16px wider and taller than the screen on all four
 * sides — so a sheet was drawn over the phone's own border, with square
 * corners across the frame's rounded ones. On the screen div that same
 * `fixed inset-0` is exactly the screen, and because this div already
 * carries `overflow-hidden sm:rounded-[34px]` *and* is now in the
 * overlay's containing-block chain, the existing clip rounds the
 * overlay's corners to the screen's own curve for free. Below `sm` no
 * transform is applied, so `fixed` stays relative to the real viewport
 * exactly as it always has.
 *
 * Same reason the screen div carries `OVERLAY_HOST_ID`: an overlay
 * that portals to `document.body` — as `AccountMenu` did — lands outside
 * this subtree entirely, where no containing block can reach it, and so
 * floats over the whole browser window. Such an overlay portals into this
 * node instead (an id rather than context only because a second export
 * from this file would break Fast Refresh, and this is two lines of
 * plumbing, not app state). `DeviceFrame` always mounts before any
 * overlay inside it can open, so the lookup can't miss; below `sm` the
 * host and the viewport are the same box anyway.
 */
export const OVERLAY_HOST_ID = 'device-screen'

export function DeviceFrame({ children }: { children: ReactNode }) {
  // `?embed=1` (lib/deepLink.ts): the operator console draws the bezel around
  // its iframe, so the app fills that frame exactly, as on a real phone. The
  // screen div keeps its transform and id, so overlays stay inside it.
  if (DEEP_LINK.embed) {
    return (
      <div className="flex h-screen flex-col overflow-hidden bg-ground">
        <div id={OVERLAY_HOST_ID} className="flex min-h-0 flex-1 flex-col overflow-hidden transform-gpu">
          {children}
        </div>
      </div>
    )
  }
  return (
    <div className="flex min-h-screen justify-center bg-ground sm:items-center sm:bg-device-backdrop sm:p-10">
      <div className="relative flex h-screen w-full flex-col overflow-hidden sm:h-[min(844px,85vh)] sm:max-w-97.5 sm:rounded-[50px] sm:bg-device-bezel sm:p-7 sm:shadow-[0_30px_60px_-20px_rgba(0,0,0,0.45)]">
        <div
          id={OVERLAY_HOST_ID}
          className="flex min-h-0 flex-1 flex-col overflow-hidden sm:transform-gpu sm:rounded-[34px]"
        >
          {children}
        </div>
      </div>
    </div>
  )
}
