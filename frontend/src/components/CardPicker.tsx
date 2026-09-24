import type { Card } from '../api/types'
import { usePolicy } from '../state/PolicyContext'
import { BottomSheet } from './BottomSheet'

/**
 * Bottom-sheet radio list for choosing which of the account's cards a
 * policy applies to — same pattern as SignIn's customer picker and
 * DESIGN.md's `AccountPicker` (#12).
 */
export function CardPicker({
  cards,
  selectedCardId,
  onSelect,
  onClose,
}: {
  cards: Card[]
  selectedCardId: string
  onSelect: (cardId: string) => void
  onClose: () => void
}) {
  const { policiesByCard } = usePolicy()

  return (
    <BottomSheet
      title="Applies to"
      onClose={onClose}
      footer={
        <button
          type="button"
          onClick={onClose}
          className="h-14 w-full rounded-row bg-ink font-sans text-[16px] font-semibold text-on-ink"
        >
          Done
        </button>
      }
    >
      <div role="radiogroup" aria-label="Choose a card" className="flex flex-col gap-3">
        {cards.map((card) => {
          const isSelected = card.card_id === selectedCardId
          // A silent overwrite of an already-active policy would be a bad
          // surprise at confirm time — say so here instead, before the
          // customer picks it (D-050).
          const mandate = policiesByCard[card.card_id]
          const isActive = mandate?.status === 'active'
          const isRevoked = mandate?.status === 'revoked'
          return (
            <label
              key={card.card_id}
              className={`flex min-h-16 cursor-pointer items-center gap-4 rounded-row px-5 py-3 transition-colors ${
                isSelected ? 'border-2 border-ink' : 'border border-hairline'
              }`}
            >
              <input
                type="radio"
                name="card"
                value={card.card_id}
                checked={isSelected}
                onChange={() => onSelect(card.card_id)}
                className="size-5 accent-ink"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[15px] font-semibold text-ink capitalize">
                  Card {card.card_id} · {card.card_purpose}
                </span>
                <span className="block truncate text-[13px] text-ink-muted">
                  {isActive
                    ? 'Already has an active policy — confirming here replaces it'
                    : isRevoked
                      ? 'Policy revoked'
                      : 'No policy yet'}
                </span>
              </span>
            </label>
          )
        })}
      </div>
    </BottomSheet>
  )
}
