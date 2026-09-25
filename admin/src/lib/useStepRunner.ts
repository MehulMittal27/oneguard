import { useState } from 'react'

export type StepStatus = 'pending' | 'active' | 'done' | 'error'

export interface Step {
  label: string
  status: StepStatus
  note?: string
}

interface StepDef {
  label: string
  action: () => Promise<string | void>
}

/**
 * Runs a sequence of named, awaited steps and tracks each one's status, so the
 * console can show *exactly* which calls it made to get a scenario running —
 * "define correctly the steps to start a new scenario" is a transparency
 * requirement, not just a plumbing one. Stops at the first failure; later
 * steps stay `pending`.
 */
export function useStepRunner() {
  const [steps, setSteps] = useState<Step[]>([])
  const [error, setError] = useState<unknown>(null)
  const [running, setRunning] = useState(false)

  async function run(defs: StepDef[]) {
    setError(null)
    setRunning(true)
    setSteps(defs.map((d) => ({ label: d.label, status: 'pending' })))
    for (let i = 0; i < defs.length; i++) {
      setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, status: 'active' } : s)))
      try {
        const note = (await defs[i].action()) ?? undefined
        setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, status: 'done', note } : s)))
      } catch (err) {
        setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, status: 'error' } : s)))
        setError(err)
        setRunning(false)
        return
      }
    }
    setRunning(false)
  }

  return { steps, error, running, run }
}
