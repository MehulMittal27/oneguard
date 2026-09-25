import { useEffect, useState } from 'react'
import { getDevices, thisDevice } from '../api/passport'
import type { Device } from '../api/types'
import { settle, viewFor, type CardRead } from '../lib/cardRead'
import { isController } from '../lib/passportDevices'
import { useDevice } from '../state/DeviceContext'
import { BackChevronIcon, DeviceIcon } from './icons/lucide'

const POLL_MS = 5000

/**
 * Home's "Devices waiting for your approval" (`../../docs/api-contract.md` §6
 * item 22): every pending device on the customer's cards this browser controls,
 * each opening its card, where the controller approves or removes it. Only the
 * controller sees it: another device could not approve anyway. Hidden when none
 * waits; a failed read keeps what was shown (it is a hint, not a state). What
 * is shown is held with the cards it was read for, so another customer's cards
 * never show the previous one's waiting devices.
 */
export function PendingDevicesCard({ cardIds, onOpenCard }: { cardIds: string[]; onOpenCard: (cardId: string) => void }) {
  const { version } = useDevice()
  const key = cardIds.join(',')
  const [held, setHeld] = useState<CardRead<Device[]> | null>(null)
  const view = viewFor(held, key)
  const pending = view.status === 'ready' ? view.value : []

  useEffect(() => {
    const cards = key ? key.split(',') : []
    if (cards.length === 0) return
    let cancelled = false
    async function read() {
      try {
        const lists = await Promise.all(
          cards.map(async (card) => {
            const list = await getDevices(card)
            return isController(list, await thisDevice(card, list)) ? list : []
          }),
        )
        const waiting = lists.flat().filter((d) => d.status === 'pending')
        if (!cancelled) setHeld((current) => settle(current, { cardId: key, status: 'ready', value: waiting }, key))
      } catch {
        // Keep what is shown for these cards; the next poll tries again.
        if (!cancelled) setHeld((current) => settle(current, { cardId: key, status: 'error' }, key))
      }
    }
    void read()
    const interval = setInterval(read, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [key, version])

  if (pending.length === 0) return null

  return (
    <section className="flex flex-col gap-3 rounded-card border-2 border-asked-border bg-surface p-5">
      <p className="flex items-center gap-2 text-[15px] font-semibold text-asked">
        <DeviceIcon size={18} strokeWidth={2.2} />
        {pending.length === 1 ? 'A device is waiting for your approval' : 'Devices waiting for your approval'}
      </p>
      <ul className="flex flex-col gap-1">
        {pending.map((device) => (
          <li key={device.device_id}>
            <button
              type="button"
              onClick={() => onOpenCard(device.card_id)}
              className="flex min-h-12 w-full items-center gap-3 rounded-row text-left"
            >
              <span className="min-w-0 flex-1">
                {/* The customer's own name for the device — plain text. */}
                <span className="block truncate text-[15px] font-semibold text-ink">{device.label}</span>
                <span className="block truncate text-[13px] text-ink-muted">Wants to control card {device.card_id}</span>
              </span>
              <span className="rotate-180 shrink-0 text-ink-muted">
                <BackChevronIcon size={18} strokeWidth={2} />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
