/**
 * Frontend-wide configuration. Values here would come from `GET
 * /v1/bootstrap` once a backend exists (`vendor/viseca-2026/technical_details.md`
 * §4) — until
 * then they're the platform's documented defaults, read from this one
 * place rather than inlined in components (`docs/api-contract.md` §2:
 * "Human window: 120 s. Read it from configuration, never hard-code it").
 */

// Time the customer has to answer a step_up before it expires unanswered.
export const HUMAN_WINDOW_SECONDS = 120

// How often to re-read the decision feed, in seconds (fractions allowed: every
// reader multiplies by 1000 for setInterval). The agent proposes purchases while
// the customer is looking at the app, so the feed has to catch up on its own —
// a screen that only updates on sign-in would quietly go stale. 1.5 s keeps a
// live demo's decisions appearing about as fast as the agent makes them.
//
// Polling is the first transport, not the final one: a Server-Sent Events
// stream replaces this as the live path once the worker talks to the real
// platform, with this poll kept as the initial read and the fallback if the
// stream drops. `DecisionsProvider` merges either through the same function,
// so that swap does not touch any screen.
export const DECISIONS_POLL_SECONDS = 1.5
