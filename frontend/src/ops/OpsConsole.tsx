import { useCallback, useEffect, useMemo, useState } from 'react'
import { getDecisions } from '../api/decisions'
import { getCurrentRun, getSoftSignals, setSoftSignals, type CurrentRun } from '../api/operator'
import {
  ApiRefusal,
  getCatalogue,
  getHealth,
  getLiveRun,
  getScenarios,
  restartReplay,
  startJudgingRun,
  type Health,
} from '../api/ops'
import { getPassport, passportQrUrl, verifyDocument } from '../api/passport'
import type { CatalogueScenario, Decision, LiveRun, ScenarioSummary, SoftSignalsState } from '../api/types'
import {
  arrivalOrder,
  completedRun,
  consoleRun,
  isWaiting,
  judgingRunGuard,
  liveRunIds,
  passportSummary,
  readVerification,
  runDecisions,
  signInLine,
  type Verification,
} from '../lib/opsConsole'
import { nextSoftSignals } from '../lib/softSignals'
import { mergeDecisions } from '../state/mergeDecisions'
import { CustomerPhone } from './CustomerPhone'
import { DecisionLog } from './DecisionLog'
import { HealthPanel } from './HealthPanel'
import { JudgingRunDialog } from './JudgingRunDialog'
import { OpsFooter, OpsHeader } from './OpsHeader'
import { OpsTabs, type OpsTab } from './OpsTabs'
import { RawDrawer } from './RawDrawer'
import { RecentDecisions } from './RecentDecisions'
import { RunHeader, type PassportLine } from './RunHeader'
import { ScenarioPanel } from './ScenarioPanel'
import { TEXT_M } from './style'
import { usePoll } from './usePoll'

const HEALTH_POLL_MS = 5000
const RUN_POLL_MS = 1500
const OFFLINE = 'Nothing was approved while we were offline.'
const ROBOTO = 'https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap'

/** Roboto for the console page only (the phone UI keeps its own type); a system font until it loads. */
function useRoboto() {
  useEffect(() => {
    if (document.querySelector(`link[href="${ROBOTO}"]`)) return
    for (const [rel, href, cross] of [
      ['preconnect', 'https://fonts.googleapis.com', false],
      ['preconnect', 'https://fonts.gstatic.com', true],
      ['stylesheet', ROBOTO, false],
    ] as const) {
      const link = document.createElement('link')
      link.rel = rel
      link.href = href
      if (cross) link.crossOrigin = 'anonymous'
      document.head.appendChild(link)
    }
  }, [])
}

function refusalText(error: unknown): string {
  return error instanceof ApiRefusal ? error.message : `OneGuard did not answer. ${OFFLINE}`
}

/**
 * The operator console (`/ops`, docs/api-contract.md §6 item 19), for the
 * projector: pick a scenario, start it, and watch the engine decide while the
 * customer answers on the phone beside it.
 *
 * The current run (D7) is the source of truth. Whatever started last (this
 * console, `make demo-offline`, `make demo-live`, a judge with curl), the
 * console switches its scenario, customer, passport line, stream and embedded
 * phone to that run within one poll. It never decides, never answers a step-up
 * and never touches a device: those are the engine's and the customer's.
 */
export default function OpsConsole() {
  useEffect(() => {
    document.title = 'Viseca · Agent control console'
  }, [])
  useRoboto()
  const [tab, setTab] = useState<OpsTab>('overview')

  // Health (/healthz) and the chaos toggle (D5), every 5 s --------------------------
  const [health, setHealth] = useState<Health | null | undefined>(undefined)
  const [healthFailed, setHealthFailed] = useState(false)
  const [signals, setSignals] = useState<SoftSignalsState | null>(null)
  const [healthRead, setHealthRead] = useState(0)
  usePoll(
    async () => {
      try {
        setHealth(await getHealth())
        setHealthFailed(false)
        setSignals(await getSoftSignals())
      } catch {
        setHealthFailed(true)
        // Unknown while nobody answers: the toggle never shows a state it has not read.
        setSignals(null)
      }
    },
    HEALTH_POLL_MS,
    healthRead,
  )

  // The current run (D7), every 1.5 s ------------------------------------------------
  const [current, setCurrent] = useState<CurrentRun | null | undefined>(undefined)
  const [runFailed, setRunFailed] = useState(false)
  const [runRead, setRunRead] = useState(0)
  usePoll(
    async () => {
      try {
        setCurrent(await getCurrentRun())
        setRunFailed(false)
      } catch {
        setRunFailed(true)
      }
    },
    RUN_POLL_MS,
    runRead,
  )
  const run = useMemo(() => (current === undefined ? undefined : consoleRun(current)), [current])
  const runKey = run?.key ?? null
  const cardId = run?.cardId ?? null
  const customerId = run?.customerId ?? null
  const ledgerRunId = run?.ledgerRunId ?? null

  // The picker follows each new run; in between, the operator may look at others.
  const [followed, setFollowed] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  // The last start this console asked for and the backend refused, verbatim;
  // a new run, from anywhere, clears it.
  const [refusal, setRefusal] = useState<string | null>(null)
  if (run && run.key !== followed) {
    setFollowed(run.key)
    setSelectedId(run.scenarioId)
    setRefusal(null)
  }
  // Whom to sign in as on the phone for the current run, whoever started it.
  const signIn = run?.customerId ? signInLine(run.customerName ?? run.customerId, run.customerId, run.cardId) : null

  // Scenarios (D9), re-read when a new run starts (it may be one served since).
  const [scenarios, setScenarios] = useState<ScenarioSummary[] | null>(null)
  useEffect(() => {
    let cancelled = false
    getScenarios()
      .then((list) => {
        if (!cancelled) setScenarios(list)
      })
      .catch(() => {
        // Keep the list on screen; the offline banner says why nothing moves.
      })
    return () => {
      cancelled = true
    }
  }, [runKey])
  const selected =
    scenarios?.find((s) => s.scenario_id === selectedId) ?? (selectedId === null && run === null ? scenarios?.[0] : null) ?? null

  // What the platform serves now (D8), re-read with D9. Undefined while reading;
  // null when D8 did not answer, which the judging button treats as "not known".
  const [catalogue, setCatalogue] = useState<CatalogueScenario[] | null | undefined>(undefined)
  useEffect(() => {
    let cancelled = false
    getCatalogue()
      .then((list) => {
        if (!cancelled) setCatalogue(list)
      })
      .catch(() => {
        if (!cancelled) setCatalogue((held) => held ?? null)
      })
    return () => {
      cancelled = true
    }
  }, [runKey])
  const listed = selected ? catalogue?.find((c) => c.scenario_id === selected.scenario_id) : undefined
  // A scenario D9 lists and D8 does not is not served now.
  const served = catalogue === undefined || catalogue === null ? catalogue : Boolean(listed?.served)
  const recordCard = listed?.profile?.card_id ?? selected?.card_id ?? null
  const recordCustomer = listed?.profile?.customer_id ?? selected?.customer_id ?? null

  // The selected served scenario's newest finished live run: its card's live runs
  // named in C6, each read with D4 for its scenario and state. Re-read when the
  // selection changes or the current run moves on (one may just have finished).
  const selectedScenarioId = selected?.scenario_id ?? null
  const recordKey =
    selectedScenarioId && served && recordCard && recordCustomer
      ? `${selectedScenarioId}|${recordCard}|${recordCustomer}|${runKey}|${run?.state}`
      : null
  const [onRecord, setOnRecord] = useState<{ key: string; run: LiveRun | null | 'failed' } | null>(null)
  useEffect(() => {
    if (!recordKey || !selectedScenarioId || !recordCard || !recordCustomer) return
    let cancelled = false
    getDecisions(recordCustomer, { operator: true })
      .then((decisions) => Promise.all(liveRunIds(decisions, recordCard).map(getLiveRun)))
      .then((runs) => completedRun(runs, selectedScenarioId))
      .catch(() => 'failed' as const)
      .then((found) => {
        if (!cancelled) setOnRecord({ key: recordKey, run: found })
      })
    return () => {
      cancelled = true
    }
  }, [recordKey, selectedScenarioId, recordCard, recordCustomer])
  // No card or customer named yet: nothing of it can be on record (nor can it start).
  const record = !recordCard || !recordCustomer ? null : onRecord?.key === recordKey ? onRecord.run : undefined

  // The run's decisions (C6 with ?operator=1: operator-only evidence included),
  // every 1.5 s, merged so a row changes in place ------------------------------------
  const [stream, setStream] = useState<{ key: string | null; rows: Decision[] | null }>({ key: null, rows: null })
  usePoll(
    async () => {
      if (!runKey || !customerId || !cardId) return
      try {
        const mine = runDecisions(await getDecisions(customerId, { operator: true }), { ledgerRunId, cardId })
        setStream((prev) => {
          const held = prev.key === runKey ? (prev.rows ?? []) : []
          const merged = mergeDecisions(held, mine)
          if (merged === held && prev.key === runKey) return prev
          const byId = new Map(merged.map((d) => [d.authorization_id, d]))
          const order = arrivalOrder(held.map((d) => d.authorization_id), mine)
          return { key: runKey, rows: order.flatMap((id) => byId.get(id) ?? []) }
        })
      } catch {
        // A failed refresh keeps what is on screen: those decisions are still true.
      }
    },
    RUN_POLL_MS,
    `${runKey}|${customerId}`,
  )
  const rows = run === null ? [] : run && !customerId ? [] : stream.key === runKey ? stream.rows : null
  const waiting = (rows ?? []).filter(isWaiting).length

  // The card's passport, once per run --------------------------------------------------
  const [passport, setPassport] = useState<{ key: string; line: PassportLine } | null>(null)
  useEffect(() => {
    if (!runKey || !cardId) return
    let cancelled = false
    // The card's passport (P2) and OneGuard's check of its signature (P5). A backend
    // without them, or any failure, leaves "Passport —".
    getPassport(cardId)
      .catch(() => null)
      .then(async (found) => {
        const summary = found ? passportSummary(found.document) : null
        const verification =
          found && summary
            ? readVerification(
                await verifyDocument({ document: found.document, signature: found.signature, key_id: found.key_id }).catch(
                  () => null,
                ),
              )
            : null
        const qrUrl = found && summary ? passportQrUrl(cardId, found.version) : null
        if (!cancelled) setPassport({ key: runKey, line: { summary, verification, qrUrl } })
      })
    return () => {
      cancelled = true
    }
  }, [runKey, cardId])
  const passportLine = passport?.key === runKey ? passport.line : null

  // Starting runs ------------------------------------------------------------------------
  const [busy, setBusy] = useState(false)
  const [judgingOpen, setJudgingOpen] = useState(false)
  const [judgingRefusal, setJudgingRefusal] = useState<string | null>(null)

  async function replay(speedMs: number) {
    if (!selected?.card_id) return
    setBusy(true)
    setRefusal(null)
    try {
      await restartReplay(selected.scenario_id, selected.card_id, speedMs)
      // Read D7 at once: the new run, and with it the sign-in line, shows now.
      setRunRead((n) => n + 1)
    } catch (error) {
      setRefusal(refusalText(error))
    } finally {
      setBusy(false)
    }
  }

  async function judgingRun() {
    if (!selected?.card_id) return
    setBusy(true)
    setJudgingRefusal(null)
    try {
      await startJudgingRun(selected.scenario_id, selected.card_id)
      setJudgingOpen(false)
      setRunRead((n) => n + 1)
    } catch (error) {
      setJudgingRefusal(refusalText(error))
    } finally {
      setBusy(false)
    }
  }

  async function toggleSignals() {
    const previous = signals
    const next = nextSoftSignals(signals)
    setSignals({ live: next, replay: next })
    try {
      const enabled = await setSoftSignals(next)
      setSignals({ live: enabled, replay: enabled })
    } catch {
      setSignals(previous)
    }
  }

  // Raw receipt drawer and the phone ------------------------------------------------------
  const [raw, setRaw] = useState<{ decision: Decision; verification: Verification | null } | null>(null)
  // Open rows in the decision log, kept here so Overview's strip can open one there.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const toggleRow = useCallback((id: string) => {
    setExpanded((open) => {
      const next = new Set(open)
      if (!next.delete(id)) next.add(id)
      return next
    })
  }, [])
  function openInLog(id: string) {
    setExpanded((open) => new Set(open).add(id))
    setTab('log')
  }
  const openRaw = useCallback(
    (decision: Decision, verification: Verification | null) => setRaw({ decision, verification }),
    [],
  )
  const closeRaw = useCallback(() => setRaw(null), [])
  const closeJudging = useCallback(() => {
    setJudgingOpen(false)
    setJudgingRefusal(null)
  }, [])
  const [showPhone, setShowPhone] = useState(true)
  const phoneCustomer = run ? customerId : (selected?.customer_id ?? null)
  const phoneName = run ? run.customerName : (selected?.customer_name ?? null)
  const offline = healthFailed || runFailed
  // D3 needs a served scenario (D8) and the worker polling (/healthz); the button and
  // the dialog both say why not, and both repeat a finished run already on record.
  const judging = judgingRunGuard({ health, healthFailed, served, record })
  const judgingBlocked = judging.blocked

  return (
    <div className="ops-viseca flex h-screen flex-col overflow-hidden bg-ground">
      <OpsHeader health={health} offline={healthFailed} />
      {offline && (
        <p role="alert" className={`${TEXT_M} shrink-0 border-b border-stopped-border bg-stopped-tint px-9 py-3 font-semibold text-stopped`}>
          OneGuard is not answering; retrying. {OFFLINE}
        </p>
      )}

      <main
        className={`grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)] gap-8 p-8 ${
          showPhone ? 'grid-cols-[minmax(0,7fr)_minmax(0,3fr)]' : 'grid-cols-[minmax(0,1fr)]'
        }`}
      >
        <div className="flex min-h-0 min-w-0 flex-col gap-6">
          <OpsTabs
            active={tab}
            onSelect={setTab}
            logCount={rows === null ? null : rows.length}
            waiting={waiting}
            healthBad={healthFailed || health?.status === 'degraded'}
          />
          {tab === 'overview' && (
            <div
              role="tabpanel"
              id="ops-panel-overview"
              aria-labelledby="ops-tab-overview"
              className="scrollbar-none flex min-h-0 flex-col gap-6 overflow-y-auto"
            >
              <ScenarioPanel
                scenarios={scenarios}
                selected={selected}
                onSelect={setSelectedId}
                run={run ?? null}
                busy={busy}
                onReplay={replay}
                onJudgingRun={() => setJudgingOpen(true)}
                judgingBlocked={judgingBlocked}
                judgingWarning={judging.warning}
                signIn={signIn}
                refusal={refusal}
                signals={signals}
                onToggleSignals={toggleSignals}
                onRefreshHealth={() => setHealthRead((n) => n + 1)}
                showPhone={showPhone}
                onShowPhone={setShowPhone}
              />
              <RunHeader run={run} waiting={waiting} passport={passportLine} />
              <RecentDecisions decisions={rows} onOpen={openInLog} onOpenLog={() => setTab('log')} />
            </div>
          )}
          {tab === 'log' && (
            <div role="tabpanel" id="ops-panel-log" aria-labelledby="ops-tab-log" className="flex min-h-0 flex-1 flex-col">
              <DecisionLog
                decisions={rows}
                expanded={expanded}
                onToggle={toggleRow}
                onOpenRaw={openRaw}
                scenarioId={run?.scenarioId ?? null}
                runKind={run?.kind ?? null}
              />
            </div>
          )}
          {tab === 'health' && (
            <div role="tabpanel" id="ops-panel-health" aria-labelledby="ops-tab-health" className="flex min-h-0 flex-1 flex-col">
              <HealthPanel health={health} failed={healthFailed} onRefresh={() => setHealthRead((n) => n + 1)} />
            </div>
          )}
        </div>

        {showPhone && run !== undefined && (
          <CustomerPhone
            customerId={phoneCustomer}
            customerLabel={phoneCustomer ? `${phoneName ?? phoneCustomer} (${phoneCustomer})` : null}
          />
        )}
      </main>

      {judgingOpen && selected && (
        <JudgingRunDialog
          scenario={selected}
          busy={busy || judgingBlocked !== null}
          refusal={judgingRefusal ?? judgingBlocked}
          warning={judging.warning}
          onStart={judgingRun}
          onClose={closeJudging}
        />
      )}
      {raw && <RawDrawer decision={raw.decision} verification={raw.verification} onClose={closeRaw} />}
      <OpsFooter />
    </div>
  )
}
