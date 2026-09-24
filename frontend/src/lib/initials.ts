/**
 * First letter of the first word plus first letter of the last word,
 * uppercase. A one-word name gives one letter. Empty/whitespace input
 * gives an empty string.
 */
export function getInitials(fullName: string): string {
  const words = fullName.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return ''

  const first = words[0][0]
  const last = words[words.length - 1][0]
  const initials = words.length === 1 ? first : `${first}${last}`
  return initials.toUpperCase()
}
