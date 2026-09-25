import { useState } from 'react'
import type { ScreenId } from './components/Layout'
import { Layout } from './components/Layout'
import { listCustomers } from './api/customers'
import { usePolling } from './lib/usePolling'
import { SCENARIOS_POLL_SECONDS } from './config'
import { ScenariosScreen } from './screens/ScenariosScreen'
import { DecisionsScreen } from './screens/DecisionsScreen'
import { LedgerScreen } from './screens/LedgerScreen'
import { CustomersScreen } from './screens/CustomersScreen'
import { SettingsScreen } from './screens/SettingsScreen'

/**
 * The customer list (C12) is read once here and handed down: nearly every
 * screen needs it (to resolve a card to its holder, to default a picker), so
 * one poll beats each screen re-fetching it.
 */
function App() {
  const [screen, setScreen] = useState<ScreenId>('scenarios')
  const { data: customers, error, loading } = usePolling(listCustomers, SCENARIOS_POLL_SECONDS, [])

  return (
    <Layout screen={screen} onSelect={setScreen}>
      {screen === 'scenarios' && <ScenariosScreen customers={customers ?? []} />}
      {screen === 'decisions' && <DecisionsScreen customers={customers ?? []} />}
      {screen === 'ledger' && <LedgerScreen customers={customers ?? []} />}
      {screen === 'customers' && <CustomersScreen customers={customers ?? []} loading={loading} error={error} />}
      {screen === 'settings' && <SettingsScreen />}
    </Layout>
  )
}

export default App
