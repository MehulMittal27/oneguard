import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { enrolThisDevice, getDevices, thisDevice } from '../api/passport'
import { DeviceEnrolSheet, type EnrolMode } from '../components/DeviceEnrolSheet'
import { OVERLAY_HOST_ID } from '../components/DeviceFrame'
import { DeviceNotEnrolledError, getOrCreateDeviceKey, rememberDeviceId } from '../lib/deviceKey'
import { DeviceContext, DeviceGateCancelled, type DeviceContextValue } from './DeviceContext'

const MOCKS = import.meta.env.VITE_USE_MOCKS === 'true'

interface Sheet {
  cardId: string
  mode: EnrolMode
}

interface Settle {
  resolve: () => void
  reject: (error: Error) => void
}

/**
 * Owns this browser's enrolment on the customer's cards (`../lib/deviceKey.ts`)
 * and the one sheet that asks for it. Screens never sign or enrol themselves:
 * they hand a device-bound change to `withDevice`.
 */
export function DeviceProvider({ children }: { children: ReactNode }) {
  const [sheet, setSheet] = useState<Sheet | null>(null)
  const [version, setVersion] = useState(0)
  // The change waiting on the open sheet, if one is: settled when it closes.
  const settleRef = useRef<Settle | null>(null)

  const bump = useCallback(() => setVersion((n) => n + 1), [])

  /** Opens the sheet and settles once this device is enrolled, or rejects when it is closed. */
  const waitForApproval = useCallback(
    (cardId: string, mode: EnrolMode) =>
      new Promise<void>((resolve, reject) => {
        settleRef.current?.reject(new DeviceGateCancelled()) // one sheet at a time
        settleRef.current = { resolve, reject }
        setSheet({ cardId, mode })
      }),
    [],
  )

  /** True when this browser may sign for the card now; enrols silently as the card's first device. */
  const ready = useCallback(
    async (cardId: string): Promise<{ ok: true } | { ok: false; mode: EnrolMode }> => {
      const key = await getOrCreateDeviceKey()
      const devices = await getDevices(cardId)
      const mine = key.deviceIds[cardId] ? await thisDevice(cardId, devices) : null
      if (mine?.status === 'enrolled') return { ok: true }
      if (mine?.status === 'pending') return { ok: false, mode: 'pending' }
      if (mine) await rememberDeviceId(cardId, null) // removed: this browser starts again
      if (devices.some((d) => d.status === 'enrolled')) return { ok: false, mode: 'offer' }
      const status = await enrolThisDevice(cardId) // the card's first device: no approval needed
      bump()
      return status === 'enrolled' ? { ok: true } : { ok: false, mode: 'pending' }
    },
    [bump],
  )

  const withDevice = useCallback(
    async <T,>(cardId: string, action: () => Promise<T>): Promise<T> => {
      if (MOCKS) return action()
      const state = await ready(cardId)
      if (!state.ok) await waitForApproval(cardId, state.mode)
      try {
        return await action()
      } catch (error) {
        if (!(error instanceof DeviceNotEnrolledError)) throw error
        // Removed or still waiting since the check above: ask again, then retry once.
        const again = await ready(cardId)
        if (!again.ok) await waitForApproval(cardId, again.mode)
        return action()
      }
    },
    [ready, waitForApproval],
  )

  const requestEnrol = useCallback(
    (cardId: string) => {
      void ready(cardId).then(
        (state) => {
          if (!state.ok) setSheet({ cardId, mode: state.mode })
        },
        () => setSheet({ cardId, mode: 'offer' }),
      )
    },
    [ready],
  )

  const close = useCallback((enrolled: boolean) => {
    const settle = settleRef.current
    settleRef.current = null
    setSheet(null)
    if (enrolled) settle?.resolve()
    else settle?.reject(new DeviceGateCancelled())
  }, [])

  const value = useMemo<DeviceContextValue>(
    () => ({ withDevice, requestEnrol, version }),
    [withDevice, requestEnrol, version],
  )

  return (
    <DeviceContext.Provider value={value}>
      {children}
      {/* Into the phone screen, like AccountMenu: a sheet opened from here sits
          outside DeviceFrame, where it would cover the whole browser window. */}
      {sheet &&
        createPortal(
          <DeviceEnrolSheet
            key={`${sheet.cardId}-${sheet.mode}`}
            cardId={sheet.cardId}
            initialMode={sheet.mode}
            onChanged={bump}
            onEnrolled={() => close(true)}
            onClose={() => close(false)}
          />,
          document.getElementById(OVERLAY_HOST_ID) ?? document.body,
        )}
    </DeviceContext.Provider>
  )
}
