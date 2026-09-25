import { useEffect, useRef } from 'react'

/**
 * Calls `read` now and then every `intervalMs` while mounted, and again whenever
 * `key` changes. The latest `read` is always the one called, so it may close
 * over fresh state without restarting the timer. A read still in flight is not
 * started twice.
 */
export function usePoll(read: () => Promise<void>, intervalMs: number, key: unknown = null): void {
  const latest = useRef(read)
  useEffect(() => {
    latest.current = read
  })

  useEffect(() => {
    let busy = false
    const tick = () => {
      if (busy) return
      busy = true
      latest.current().finally(() => {
        busy = false
      })
    }
    tick()
    const id = setInterval(tick, intervalMs)
    return () => clearInterval(id)
  }, [intervalMs, key])
}
