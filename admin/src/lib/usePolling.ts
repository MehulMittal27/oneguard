import { useEffect, useRef, useState } from 'react'

interface PollingState<T> {
  data: T | null
  error: unknown
  loading: boolean
  /** Read on demand (e.g. right after an action), without waiting for the interval. */
  refresh: () => void
}

/**
 * Polls `fetcher` every `intervalSeconds`, starting immediately. A failed
 * read keeps the last good `data` on screen rather than blanking it — same
 * reasoning as `frontend/.claude/CLAUDE.md`'s live-decisions rule: a stale
 * number is still true, an error page is not. `deps` restarts the poll (new
 * interval, cleared timers) when the thing being polled changes — e.g.
 * switching which run or customer is selected.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalSeconds: number,
  deps: readonly unknown[],
): PollingState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)

  // Kept current in an effect (not written during render) so the interval
  // below always calls the latest closure without needing to be torn down
  // and restarted every time the caller passes a new function identity.
  const fetcherRef = useRef(fetcher)
  useEffect(() => {
    fetcherRef.current = fetcher
  })

  const readRef = useRef<() => void>(() => {})

  useEffect(() => {
    let cancelled = false
    // Deliberate: a new poll target (deps changed) means the in-flight read
    // is for the wrong thing, so its result must not appear to be current.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    const read = () => {
      fetcherRef
        .current()
        .then((next) => {
          if (cancelled) return
          setData(next)
          setError(null)
          setLoading(false)
        })
        .catch((err: unknown) => {
          if (cancelled) return
          setError(err)
          setLoading(false)
        })
    }
    readRef.current = read
    read()
    const id = setInterval(read, intervalSeconds * 1000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalSeconds, ...deps])

  return { data, error, loading, refresh: () => readRef.current() }
}
