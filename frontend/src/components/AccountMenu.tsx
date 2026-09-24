import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { getInitials } from '../lib/initials'
import { OVERLAY_HOST_ID } from './DeviceFrame'
import { LogOutIcon } from './icons/lucide'

/**
 * The signed-in customer's own profile menu (DESIGN.md "AccountMenu",
 * D-021) — not to be confused with AccountPicker (#12), which switches
 * between the customer's bank accounts.
 */
export function AccountMenu({
  customerName,
  onLogout,
}: {
  customerName: string
  onLogout: () => void
}) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const logoutRef = useRef<HTMLButtonElement>(null)
  const initials = getInitials(customerName)

  useEffect(() => {
    if (!open) return

    logoutRef.current?.focus()

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        close()
        return
      }
      if (event.key === 'Tab') {
        // The Log out button is the only focusable element in the popup —
        // keep Tab from leaving it either direction.
        event.preventDefault()
        logoutRef.current?.focus()
      }
    }

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  function close() {
    setOpen(false)
    triggerRef.current?.focus()
  }

  function handleLogout() {
    setOpen(false)
    onLogout()
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Account menu"
        aria-haspopup="dialog"
        aria-expanded={open}
        className="flex size-11 shrink-0 items-center justify-center rounded-full bg-ink font-sans text-[15px] font-semibold text-on-ink"
      >
        {initials}
      </button>

      {open &&
        createPortal(
          <div className="fixed inset-0 z-50">
            <button
              type="button"
              tabIndex={-1}
              aria-label="Close menu"
              onClick={close}
              className="absolute inset-0 bg-scrim"
            />
            <div
              role="dialog"
              aria-modal="true"
              aria-label="Account menu"
              className="absolute top-35 left-1/2 w-62.5 -translate-x-1/2 rounded-hero bg-surface px-8 pt-16 pb-5.5 shadow-[0_12px_32px_rgba(20,33,61,0.28)]"
            >
              <div
                aria-hidden="true"
                className="absolute -top-11.5 left-1/2 flex size-23 -translate-x-1/2 items-center justify-center rounded-full border-[5px] border-surface bg-ink font-display text-[32px] font-bold text-on-ink"
              >
                {initials}
              </div>

              <button
                ref={logoutRef}
                type="button"
                onClick={handleLogout}
                className="flex h-13 w-full items-center justify-center gap-3 rounded-button border-2 border-destructive-border text-[16px] font-semibold text-destructive"
              >
                <LogOutIcon />
                Log out
              </button>
            </div>
          </div>,
          // The phone's screen, not `document.body` (D-098) — a body portal
          // escapes `DeviceFrame`, so at desktop width this menu and its
          // scrim covered the whole browser window instead of the phone.
          document.getElementById(OVERLAY_HOST_ID) ?? document.body,
        )}
    </>
  )
}
