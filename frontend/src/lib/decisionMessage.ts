/**
 * DecisionDetail shows `message` and then `counterfactual` on its own line
 * (`../../docs/api-contract.md` §6 item 3). A declined template message already
 * ends with that same suggestion ("... Would approve at CHF 20.00 or less."), so
 * rendering both would say it twice. The detail screen shows the message without
 * the trailing suggestion and keeps the counterfactual line; lists keep the full
 * message, since they show no counterfactual line.
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
