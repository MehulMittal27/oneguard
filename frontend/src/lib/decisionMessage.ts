/**
 * DecisionDetail and the Approvals pending card show `message` and then
 * `counterfactual` on its own line (`../../docs/api-contract.md` §6 item 3). An
 * older template message already ends with that same suggestion ("... Would
 * approve at CHF 20.00 or less."), so rendering both would say it twice. Those
 * screens show the message without the trailing suggestion and keep the
 * counterfactual line; lists keep the full message, since they show no
 * counterfactual line.
 *
 * Only an exact trailing match is dropped: a message that does not end with the
 * counterfactual (a refined explanation, an approval) is shown unchanged, so no
 * reason is ever lost.
 */
export function messageWithoutCounterfactual(message: string, counterfactual: string | null | undefined): string {
  const suggestion = counterfactual?.trim()
  const text = message.trim()
  if (!suggestion || text === suggestion || !text.endsWith(suggestion)) return message
  const rest = text.slice(0, -suggestion.length)
  // A sentence boundary only: never cut a suggestion out of the middle of a word.
  if (!/\s$/.test(rest)) return message
  return rest.trimEnd()
}

/**
 * Whether a step-up's `uncertainty.note` says something the message above it
 * does not. The engine takes the note from the uncertain evidence row's detail
 * (backend `pipeline._uncertainty_note`), which usually restates the message's
 * clause with an aside: "Same shop and items as the CHF 289.00 order 25 min
 * earlier (CHF 289.00 then, CHF 289.00 now)." under a message that already says
 * "Same shop and items as the CHF 289.00 order 25 min earlier." Shown as is, the
 * box reads the same sentence twice.
 *
 * A note adds something when at least a fifth of its words, asides in brackets
 * left out, are not in the message. Hiding it loses nothing: the same detail is
 * the evidence row listed right under the box.
 */
export function noteAddsToMessage(message: string, note: string | null | undefined): boolean {
  const core = words(note?.replace(/\([^)]*\)/g, ' ') ?? '')
  if (core.size === 0) return false
  const said = words(message)
  let fresh = 0
  for (const word of core) if (!said.has(word)) fresh += 1
  return fresh / core.size >= 0.2
}

function words(text: string): Set<string> {
  return new Set(text.toLowerCase().match(/[\p{L}\p{N}]+(?:['’.][\p{L}\p{N}]+)*/gu) ?? [])
}
