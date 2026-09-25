/**
 * Console-wide configuration. This app is an operator tool (`docs/api-contract.md`
 * §1.2, D1–D8): it exists to drive scenarios and inspect decisions while someone
 * else watches the customer PWA, so every screen polls rather than pushing —
 * same reasoning as `frontend/src/config.ts`'s `DECISIONS_POLL_SECONDS`.
 */

// Scenario catalogue (D8): changes rarely (a new pack version, a scenario
// getting its first run), so this can be slow.
export const SCENARIOS_POLL_SECONDS = 5

// A run in progress (D4/D7) and the offline replay (D1): the whole point of
// this screen is watching purchases land one by one, so this is fast.
export const RUN_POLL_SECONDS = 1.5

// A customer's decision feed (C6), read while a run panel is open.
export const DECISIONS_POLL_SECONDS = 2
