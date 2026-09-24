import type { Customer } from '../api/types'

export interface SignInCustomers {
  // The rows on the sign-in screen itself.
  visible: Customer[]
  // Everyone else, in the "more customers" sheet. Together with `visible`
  // this covers every customer the API returned, so nobody is unreachable.
  others: Customer[]
}

/**
 * Highest scenario id a customer backs, or null when they back none. Scenario
 * ids are zero-padded ("SCEN0101"), compared numerically so a longer id still
 * sorts after a shorter one.
 */
function latestScenarioId(customer: Customer): string | null {
  let latest: string | null = null
  for (const id of customer.scenario_ids) {
    if (latest === null || compareScenarioIds(id, latest) > 0) latest = id
  }
  return latest
}

function compareScenarioIds(a: string, b: string): number {
  return a.localeCompare(b, 'en', { numeric: true })
}

/**
 * Splits the C12 customer list for the sign-in screen. Live customers come
 * first, ordered by the most recent scenario they back (descending, stable),
 * so the customer behind the scenarios being served now leads the list rather
 * than whoever the API happened to list first. The first `maxVisible` of them
 * are shown on the screen; every other customer, live or not, goes in the
 * sheet. A customer picked from the sheet is lifted onto the screen so the
 * selection stays visible.
 */
export function splitSignInCustomers(
  customers: Customer[],
  selectedId: string | null,
  maxVisible: number,
): SignInCustomers {
  const live = customers
    .filter((c) => c.live)
    .sort((a, b) => {
      const latestA = latestScenarioId(a)
      const latestB = latestScenarioId(b)
      if (latestA === latestB) return 0
      if (latestA === null) return 1
      if (latestB === null) return -1
      return compareScenarioIds(latestB, latestA)
    })
  const shown = live.slice(0, maxVisible)
  const others = [...live.slice(maxVisible), ...customers.filter((c) => !c.live)]

  const selectedOther = others.find((c) => c.customer_id === selectedId)
  const visible = selectedOther
    ? [selectedOther, ...shown].slice(0, maxVisible)
    : shown

  return { visible, others }
}
