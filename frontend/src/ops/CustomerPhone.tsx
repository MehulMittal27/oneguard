import { useLayoutEffect, useRef, useState } from 'react'
import { TEXT_M } from './style'

const SCREEN_W = 390
const SCREEN_H = 844
// The bezel mirrors DeviceFrame's (16px, outer 50 = inner 34 + 16), so the
// embedded phone reads as the same device the phone UI draws on a desktop.
const BEZEL = 16
const PHONE_W = SCREEN_W + 2 * BEZEL
const PHONE_H = SCREEN_H + 2 * BEZEL

/**
 * The customer's phone UI, same origin, in an iframe at 390×844, signed in as
 * the run's customer (`/?customer=<id>&embed=1`, api-contract §6 item 19). It is
 * the real app: step-ups are answered and policies revoked there, by the
 * customer, never by the console. A new customer reloads it (`key`).
 *
 * The phone scales down, never up, to fit its column.
 */
export function CustomerPhone({ customerId, customerLabel }: { customerId: string | null; customerLabel: string | null }) {
  const boxRef = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)

  useLayoutEffect(() => {
    const box = boxRef.current
    if (!box) return
    const fit = () => setScale(Math.min(1, box.clientWidth / PHONE_W, box.clientHeight / PHONE_H))
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(box)
    return () => observer.disconnect()
  }, [])

  const query = customerId ? `?customer=${encodeURIComponent(customerId)}` : ''
  const embedSrc = `/${query}${query ? '&' : '?'}embed=1`

  return (
    <aside aria-label="Customer phone" className="flex min-h-0 min-w-0 flex-col items-center gap-4">
      <div className={`${TEXT_M} flex w-full items-center justify-between gap-4`} style={{ maxWidth: PHONE_W }}>
        <span className="min-w-0 truncate font-semibold text-ink-muted">{customerLabel ?? 'No customer yet'}</span>
        <a
          href={`/${query}`}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 font-semibold text-ink underline decoration-border-quiet underline-offset-4 hover:decoration-ink"
        >
          Open in new tab
        </a>
      </div>
      <div ref={boxRef} className="flex min-h-0 w-full flex-1 items-start justify-center">
        <div style={{ width: PHONE_W * scale, height: PHONE_H * scale }}>
          <div
            className="origin-top-left rounded-[50px] bg-device-bezel shadow-[0_30px_60px_-20px_rgba(0,0,0,0.45)]"
            style={{ width: PHONE_W, height: PHONE_H, padding: BEZEL, transform: `scale(${scale})` }}
          >
            <iframe
              key={customerId ?? 'picker'}
              title="Customer phone"
              src={embedSrc}
              width={SCREEN_W}
              height={SCREEN_H}
              className="block rounded-[34px] bg-ground"
            />
          </div>
        </div>
      </div>
    </aside>
  )
}
