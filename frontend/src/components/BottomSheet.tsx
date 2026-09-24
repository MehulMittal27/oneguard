import { useEffect, type ReactNode } from 'react'

/**
 * Bottom sheet over a scrim (DESIGN.md §Screen 12, AccountPicker): drag
 * handle, title, scrollable body, sticky footer for the primary action.
 *
 * Height cap is `max-h-[60%]`, a percentage of this `fixed` layer, **not**
 * `vh` (D-097). Both halves matter: a long list (SignIn's "Select other"
 * has 16 customers) grows until it hits the cap, so the cap is what the
 * customer actually sees; and at `sm` and up `DeviceFrame` makes itself
 * the containing block for `fixed` children, so a `vh` cap is measured
 * against the browser window while the sheet is drawn inside a phone only
 * `min(844px, 85vh)` tall — the old `75vh` covered ~92% of that phone
 * screen. A percentage tracks whichever box the sheet slides up inside.
 * Keep it a percentage if this number is ever retuned.
 */
export function BottomSheet({
  title,
  onClose,
  children,
  footer,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center">
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-scrim"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative z-10 flex max-h-[60%] w-full flex-col rounded-t-sheet bg-surface pt-3 sm:max-w-97.5"
      >
        <div
          className="mx-auto mb-4 h-1 w-9 shrink-0 rounded-pill bg-border-quiet"
          aria-hidden="true"
        />
        <h2 className="shrink-0 px-8 pb-4 font-display text-[20px] font-bold text-ink">
          {title}
        </h2>
        <div className="scrollbar-none flex-1 overflow-y-auto px-8 pb-4">{children}</div>
        {footer && (
          <div className="shrink-0 border-t border-hairline px-8 pt-4 pb-8">{footer}</div>
        )}
      </div>
    </div>
  )
}
