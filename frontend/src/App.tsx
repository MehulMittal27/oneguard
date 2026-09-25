import { useEffect, useRef, useState } from 'react'
import { CustomerProvider } from './state/CustomerProvider'
import { useCustomer } from './state/CustomerContext'
import { PolicyProvider } from './state/PolicyProvider'
import { usePolicy } from './state/PolicyContext'
import { DecisionsProvider } from './state/DecisionsProvider'
import { useDecisions } from './state/DecisionsContext'
import { SignIn } from './screens/SignIn/SignIn'
import { Home } from './screens/Home/Home'
import { Accounts } from './screens/Accounts/Accounts'
import { Activity, type FilterId } from './screens/Activity/Activity'
import { Approvals } from './screens/Approvals/Approvals'
import { CardDetail } from './screens/CardDetail/CardDetail'
import { NewPolicyFlow } from './screens/NewPolicy/NewPolicyFlow'
import { TabBar, type TabId } from './components/TabBar'
import { DeviceFrame } from './components/DeviceFrame'
import { OperatorStrip } from './components/OperatorStrip'
import { StatusBar } from './components/StatusBar'
import { DeviceProvider } from './state/DeviceProvider'
import { Verify } from './screens/Verify/Verify'

/**
 * P3-2: the operator strip is opt-in by `?demo=1` and read once, at module
 * scope. Nothing in the app can turn it on, so a customer never reaches it by
 * tapping — only by being handed a URL that asks for it.
 */
const SHOW_OPERATOR_STRIP =
  typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('demo') === '1'


function SignedInShell() {
  const [tab, setTab] = useState<TabId>('home')
  // Set while the new-policy flow (no tab bar, DESIGN.md #7/#13-16) is open
  // for this card; null shows the normal tabbed shell.
  const [flowCardId, setFlowCardId] = useState<string | null>(null)
  // Card detail (DESIGN.md #6) is reachable from Accounts *and* cross-tab
  // from any decision's "Policy applied" link (D-041) — one App-level
  // piece of state instead of Accounts owning a copy no other tab can reach.
  // Keeps the tab bar visible (unlike the New Policy flow above).
  const [cardDetailId, setCardDetailId] = useState<string | null>(null)
  // What Activity should open pre-filtered to, when Home's OverviewHero or
  // "See all" is what sent us there — read once by Activity on mount, not
  // a controlled prop (see Activity.tsx). Reset to 'all' on any other way
  // of reaching the tab so a stale filter never lingers.
  const [activityFilter, setActivityFilter] = useState<FilterId>('all')
  const { setPolicyForCard } = usePolicy()
  const { pending } = useDecisions()

  // The tab content and Card detail all render into one persistent
  // scrollable div below (it's never unmounted, only its children swap),
  // so its scroll offset otherwise carries over silently — leave Approvals
  // scrolled down, switch tabs, come back, and it's still scrolled down.
  // Reset it on every logical view change instead.
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0
  }, [tab, cardDetailId])

  function goToApprovals() {
    setCardDetailId(null)
    setTab('approvals')
  }

  // DecisionDetail's manipulated-purchase CTA (D-058) — "back to this
  // persona's Home" from wherever the screen was reached.
  function goHome() {
    setCardDetailId(null)
    setActivityFilter('all')
    setTab('home')
  }

  function openActivity(filter: FilterId) {
    setCardDetailId(null)
    setActivityFilter(filter)
    setTab('activity')
  }

  function selectTab(next: TabId) {
    setCardDetailId(null)
    setActivityFilter('all')
    setTab(next)
  }

  if (flowCardId) {
    return (
      <DeviceFrame>
        <div className="flex min-h-0 flex-1 flex-col bg-ground">
          {SHOW_OPERATOR_STRIP && <OperatorStrip />}
          <StatusBar tone="ink" />
          <NewPolicyFlow
            cardId={flowCardId}
            onClose={() => setFlowCardId(null)}
            onConfirmed={(mandate) => {
              // Not necessarily flowCardId — the "Applies to" picker (D-050)
              // lets the customer switch to a sibling card before compiling,
              // so the mandate's own card_id is the one that's actually true.
              setPolicyForCard(mandate.card_id, mandate)
              setFlowCardId(null)
            }}
          />
        </div>
      </DeviceFrame>
    )
  }

  return (
    <DeviceFrame>
      <div className="flex min-h-0 flex-1 flex-col bg-ground">
        {SHOW_OPERATOR_STRIP && <OperatorStrip />}
        <StatusBar tone="ink" />
        <div ref={scrollRef} className="scrollbar-none flex-1 overflow-y-auto">
          {cardDetailId ? (
            <CardDetail
              cardId={cardDetailId}
              onBack={() => setCardDetailId(null)}
              onGoToApprovals={goToApprovals}
              onAddPolicy={setFlowCardId}
              onGoHome={goHome}
            />
          ) : (
            <>
              {tab === 'home' && (
                <Home
                  onOpenActivity={openActivity}
                  onGoToApprovals={goToApprovals}
                  onViewPolicy={setCardDetailId}
                  onAddPolicy={setFlowCardId}
                />
              )}
              {tab === 'accounts' && (
                <Accounts onAddPolicy={setFlowCardId} onViewCard={setCardDetailId} />
              )}
              {tab === 'activity' && (
                <Activity
                  onViewPolicy={setCardDetailId}
                  onGoToApprovals={goToApprovals}
                  onGoHome={goHome}
                  initialFilter={activityFilter}
                />
              )}
              {tab === 'approvals' && <Approvals onViewPolicy={setCardDetailId} onGoHome={goHome} />}
            </>
          )}
        </div>
        <TabBar active={tab} onSelect={selectTab} approvalsCount={pending.length} />
      </div>
    </DeviceFrame>
  )
}

function Shell() {
  const { signedInAs } = useCustomer()
  return signedInAs ? <SignedInShell /> : <SignIn />
}

// `/verify` is where a passport's QR code lands, on any phone and without
// signing in (docs/passport.md); everything else is the signed-in app.
const IS_VERIFY_PAGE = typeof window !== 'undefined' && window.location.pathname === '/verify'

function App() {
  if (IS_VERIFY_PAGE) return <Verify />
  return (
    <CustomerProvider>
      <DeviceProvider>
        <PolicyProvider>
          <DecisionsProvider>
            <Shell />
          </DecisionsProvider>
        </PolicyProvider>
      </DeviceProvider>
    </CustomerProvider>
  )
}

export default App
