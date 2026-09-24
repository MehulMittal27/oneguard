# Judging pack: the served scenario catalogue

The scenarios the Viseca sandbox serves to team14 during judging, as
`GET /v1/reference-data` returned them under `tables.scenario_catalogue` on 24 Sep 2026
(`pack_version` `saw26-hackaton-api`). `GET /v1/bootstrap` lists the same 10 scenarios. Fetched
read-only (two GETs; no run started, no reset). The instructions are verbatim.

- The local pack's SCEN0000 to SCEN0004 are not served any more. The ids run from
  SCEN0101 to SCEN0136 with gaps: 10 scenarios, 111 events in all.
- The catalogue names no card and no customer. Each scenario runs on its own fixture
  profile (customer and card); the bootstrap `profile` names SCEN0101's only, and a run's
  `fixture_profiles` and its authorizations name the others. The worker stores every
  binding it sees (`scenario_profiles`, api-contract.md §1.2 "Scenario bindings"); D8
  lists them and C12 marks their customers live.
- At start the worker syncs these rows into `scenario_catalogue` (`seed.sync_served`),
  so D3 and `make demo-live SCEN=<id>` accept these ids. The store keeps the local rows too.

## Scenario → customer and card

As the platform named them by 24 Sep 2026 (bootstrap profile, and `GET /v1/authorizations`
for the runs so far). The served reference data adds ten customers, one card each (CU1016,
CU1052, CU1217, CU1308, CU1363, CU1373, CU1376, CU1415, CU1448, CU1475); a scenario not run
yet is bound to one of them by its first run, and nothing names which before that.

| Scenario | Customer | Card | Profile | Named by |
|---|---|---|---|---|
| SCEN0101 | CU1217 Omar Chen | CA1331 | PROFILE_AUTH0101 | bootstrap, run |
| SCEN0104 | CU1415 Hannah Chen | CA1643 | PROFILE_AUTH0104 | run |
| SCEN0106 | CU1376 Elias Egli | CA1576 | PROFILE_AUTH0106 | run |
| SCEN0113, SCEN0117, SCEN0122, SCEN0124, SCEN0130, SCEN0135, SCEN0136 | not run yet | | | |

| Scenario | Name | Events | Cardholder instruction |
|---|---|---:|---|
| SCEN0101 | Connection check | 2 | Buy one ordinary grocery item for CHF 20 or less from a shop I use regularly. Ask me when uncertain. |
| SCEN0104 | Cross-border purchase | 10 | Order hiking boots, size 46, from the Austrian outdoor retailer I already know. Pay no more than EUR 200 and only if they can be returned. Ask me if anything is unclear. |
| SCEN0106 | Session integrity | 12 | Allow small electronics and accessories purchases up to CHF 300 each at retailers I already use. If the session looks unusual — a new device, a burst of orders, unfamiliar shops abroad — stop and ask me. |
| SCEN0113 | Weeknight meal delivery | 12 | Weeknight dinners only: one delivery a day, CHF 40 maximum including the delivery fee, from my usual services. Never at the weekend. Ask me if something doesn't fit. |
| SCEN0117 | Category exclusions | 13 | Groceries and everyday household items only, maximum CHF 100 per order, from the shops I use. No alcohol, no gift cards, no cosmetics. When in doubt, ask. |
| SCEN0122 | Manipulated agent | 13 | Buy the camera lens I chose, from a seller I have bought from before, for CHF 900 or less. Do not add anything I did not ask for. Ask me when uncertain. |
| SCEN0124 | Travel booking | 12 | Book me a hotel in Munich for 3 nights from 10 September to 13 September, at most CHF 200 per night, refundable rate only. No flights, no insurance. Ask me when uncertain. |
| SCEN0130 | Requested item and order terms | 13 | I need new hiking boots, size 42, from a proper sports shop. Maximum CHF 180, and I must be able to return them within at least 14 days. Nothing else in the basket. If anything is unclear, ask me. |
| SCEN0135 | Household budget | 12 | Do the weekly grocery shopping online at supermarkets I already use. Never spend more than CHF 100 per order or CHF 250 in any 7-day window; groceries and household basics only. If unsure, ask. |
| SCEN0136 | Subscription control | 12 | Keep my current subscriptions running. Total per month must stay under CHF 80. No new services, no premium tiers, no annual prepayments. If a price changes, ask me. |
