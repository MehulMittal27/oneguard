# OneGuard UI — Handoff to Claude Code

Source of truth: the "Agent on a Leash — Mobile UI" design canvas
(`Main.dc.html`, `Home.dc.html`, `Activity.dc.html`, `Approvals.dc.html`,
`Accounts.dc.html`, `Card.dc.html`, `Decision.dc.html`, `NewPolicy*.dc.html`,
`Manipulated.dc.html`, `HomeMulti.dc.html`, `AccountsMulti.dc.html`,
`AccountPicker.dc.html`). This doc is the diff between that canvas and the
current app (as described in the app's own generated reference, "Ink and
Signal") — apply these changes to the real component files, don't rebuild
screens from scratch. Where a filename below doesn't match the repo exactly,
map it to the closest existing component (`Home.tsx`, `Activity.tsx`,
`Approvals.tsx`, `CardDetail.tsx`, `Accounts.tsx`, `DecisionDetail.tsx`, the
sign-in screen, and the policy-creation wizard steps).

Theme stays **Ink and signal** — don't restructure layout, routing, or state
management. This is a content/visual pass on top of the existing screens.

---

## 0. Logo asset

The current mark is an inline placeholder: an outline shield SVG
(`stroke:#E0561B`, viewBox `0 0 24 24`, paths `M12 3l7 3v5.5c0 4.2-2.9
7.6-7 9.5-4.1-1.9-7-5.3-7-9.5V6z` + `m9 12 2.2 2.2L15.5 10`) sitting next to
a hand-set `VISECA` wordmark (Instrument Sans, 800 weight, letter-spacing
.03em).

**The real Viseca logo file will be dropped into the project directory.**
When it lands:
- Replace the inline shield SVG + wordmark span with the provided asset
  (`<img>` or inline SVG, whichever the file is).
- Size it to sit comfortably at the same visual weight as the current
  placeholder — roughly 20px tall in the sign-in header, matching the
  `One Guard` label's line-height next to it.
- Keep the current position (see §1 below) — placing the asset is a drop-in
  replacement, not a redesign of the lockup.
- **Do not add the logo anywhere it isn't already used.** Its only current
  placement is the sign-in screen header. It was deliberately removed from
  Home, Activity, and every step of the policy-creation wizard — leave those
  screens without it.

---

## 1. Sign-in screen (`Main.dc.html` → sign-in component)

Header row, `justify-content: space-between`:
- **Left:** shield icon (`stroke:#E0561B`, 20×20) + `ONE GUARD` label
  (12px, weight 700, letter-spacing .08em, uppercase, `color:#34424A`), gap 7px.
- **Right:** `VISECA` wordmark (→ becomes the provided logo asset per §0).

No "Agent on a Leash" badge anywhere on this screen — confirm it isn't
reintroduced.

Everything else on this screen (persona list, the 3-step "Agent proposes →
Your rules decide → Money moves, or not" strip, demo sign-in copy) is
unchanged.

---

## 2. Home (`Home.tsx`)

- **No metadata/summary card.** There is no "processed by your rules" total
  or Approved/Stopped/Uncertain count block on this screen anymore.
- **Top widget, above the fold:** a dashed-border "Add a spending policy"
  card, full width, linking into the policy-creation flow (Describe step).
  Icon: black circle with a plus. Copy: "Add a spending policy" / "Set
  limits, allowed shops, and what needs your OK."
- The step-up "Needs your OK" card, "Latest by your control" list, and
  "Policies by card" section below are otherwise unchanged **except**:
- **"Policies by card" → per-policy action row: only `Revoke` remains.**
  `Manage` has been removed. Don't leave a two-column grid with an empty
  slot — the row is now a single full-width `Revoke` button. (Managing/
  tightening a policy isn't wired up yet; revoke is the only exposed action
  at the listing level. Full policy detail — where `Revoke` also lives — is
  reached by tapping into the card.)
- No VISECA/shield branding on this screen (removed; see §0).

---

## 3. Activity (`Activity.tsx`)

- **Metadata card lives here now** (moved from Home), two-column split:
  - **Left column: "Amount safeguarded"** — the emphasized figure,
    `font-size:32px`, `color:#1B7A4B`, with a small shield icon next to the
    label. Sub-line: count of stopped + expired-unpaid purchases.
  - **Right column, with a left divider: "Amount processed"** —
    de-emphasized relative to safeguarded, `font-size:24px`, default text
    color. Sub-line: count of approved purchases.
  - Order matters: **safeguarded is left/first, processed is right/second.**
    Safeguarded is also visually larger (32px vs 24px) — it's the number
    the screen wants to lead with.
- **Every activity row has a status/reason tag chip**, not just plain
  description text. Chip sits directly under the row's description line,
  small pill (`border-radius:999px`, `font-size:11px`, weight 600),
  colored to match the row's outcome tint. Observed chip copy: "Within
  limits", "Over per-order limit", "At per-order limit", "Item outside
  policy", "Possible duplicate".
  - **Data note:** these map to `reason_codes` on the decision object your
    engine already returns via the decisions endpoint — this is not new
    data, just new rendering. Derive the chip text/color from
    `reason_codes` (fall back to a neutral "Reviewed" chip if a decision
    has none).
- No VISECA/shield branding on this screen (removed; see §0).

---

## 4. Approvals (`Approvals.tsx`)

- **"Why your rules are unsure" is a structured label/value list**, not a
  paragraph: rows for `Policy`, `Per-order limit`, `7-day limit`, `Item
  category`, each right-aligned value colored green (OK) or amber
  (borderline/flagged) as appropriate. A single short freeform sentence can
  follow underneath for the one genuinely open question, but the structured
  facts come first.
- **The expired entry below the pending card carries a matching tag chip**
  (e.g. "Possible duplicate"), same chip component as Activity.
- **Removed: the "If you approve" spend-projection block** (the bar showing
  current week's spend sliding toward the projected total, "This week: CHF
  X → CHF Y / of Z"). That section is gone — the card now goes straight
  from the basket/evidence into the Approve/Reject buttons.
- This screen has intentionally been left otherwise untouched across
  several rounds — don't restructure it further without a specific ask.

---

## 5. Accounts (`Accounts.tsx`)

- **Removed the green "Active" status badge next to the account name**
  ("Daily spending" header row). The account name and its subtitle
  (type · account id · cards-guarded count) stand alone now, no badge.
- Card list and "no policy" states below are unchanged.

---

## 6. Card detail (`CardDetail.tsx`)

- **Action row is `Revoke` only.** `Tighten` has been removed — full width
  single button, red outline (`border:#E7C4BF`, `color:#B42318`).
- **Helper copy below the button updated to match:** "You can remove this
  policy at any time. Revoking also declines anything still waiting." (No
  longer references adding rules or writing a new policy to loosen one,
  since that path isn't exposed here right now.)
- **Removed the "This week: CHF X of CHF Y" spend-meter block** that sat
  between the action buttons and the "Card activity" list. That whole
  progress-bar section is gone.
- Rest of the screen (policy instruction text, "What your rules do" list,
  card activity feed below) unchanged.

---

## 7. Decision detail (`DecisionDetail.tsx`)

- Bottom "Decided by your rules" explanation block stays **minimal**: one
  bold line ("Decided by your rules") + one short sentence of context. Do
  **not** reintroduce longer framing like "— not by us, and not by the
  shop." That phrasing was deliberately cut.
- Everything else (red result banner, "What was checked" evidence rows)
  unchanged.

---

## 8. Policy-creation wizard

Theme-only pass — **do not restructure step order, the AI/form toggle, or
navigation**. Applies to whichever component files back the "Describe →
Check → Confirm" flow (Describe step, the AI-processing/loading state, and
the AI-unavailable/fallback state).

### AI unavailable / fallback state
Previously: clock icon, "The AI took too long", "We stopped reading and
saved nothing…", with `Try again` / `Use the form instead` buttons.

Now:
- Headline: **"Something went wrong"**
- Icon: a centered **cross (X)** in a neutral gray circle (not red/alarm —
  this is a graceful fallback, not a hard failure), replacing the clock.
- Message: **"AI reading unavailable — rule-based reading used."**
- **Removed the "Your words" recap card** that used to sit below the error
  card on this screen.
- Footer: **one black button, "Back"**, returning to the Describe step.
  **No second Cancel button below it** — it was removed. (The screen still
  has its own top-left Cancel nav link, unrelated to this footer button;
  leave that where it is.)

### AI reading / loading state
- No functional changes to the spinner/progress UI itself.
- **Removed the bottom Cancel button** — this screen now has no footer
  action at all; only the top-left Cancel nav link remains.

### VISECA/shield branding
Removed from every wizard step's header (Describe, Check, Form, Reading,
Timed-out). Only the Cancel nav link remains at top-left on each — don't
reintroduce the mark here (see §0).

---

## 9. Color/size reference used above

| Token | Hex | Used for |
|---|---|---|
| Ink | `#121214` | primary text, black buttons |
| Muted text | `#56636B` | secondary copy |
| Soft text | `#34424A` | labels like "One Guard" |
| Hairline | `#DCE3E4` | dividers, card borders |
| Approved green | `#1B7A4B` on `#E3F3EA` | approved state, safeguarded figure |
| Stopped red | `#B42318` on `#FBE9E7` | stopped state, revoke outline `#E7C4BF` |
| Uncertain amber | `#8A5300` on `#FDF0D5`, border `#E6C46F` | uncertain/step-up state |
| Neutral chip | `#56636B` on `#E6ECED` | expired/duplicate/neutral chips |
| Accent orange | `#E0561B` (→ `#F2A73B` gradient in old lockup, now solid on the shield stroke) | shield icon, progress accents |

Typography: Instrument Sans throughout (400/500/600/700/800 weights used
above); numerals use `font-variant-numeric: tabular-nums` wherever an
amount appears.

---

## 10. Out of scope for this pass

Not covered by the changes above — leave as currently implemented:
- `Accounts.tsx`'s multi-account / account-picker variants beyond the badge
  removal in §5.
- Any backend/API contract changes — this is purely a rendering pass over
  data the app already receives (see the `reason_codes` note in §3).
- The manipulated-purchase / duplicate-detection demo screen and the
  two-account "Oliver" home variant were kept structurally aligned with
  Home/Card's changes above but aren't separate app routes — fold their
  intent into the real `Home`/`CardDetail` components, not new screens.
