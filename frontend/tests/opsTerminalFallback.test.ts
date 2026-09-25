import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

// No component test setup (README §1): these read ScenarioPanel's source.
const panel = readFileSync(new URL('../src/ops/ScenarioPanel.tsx', import.meta.url), 'utf8')

/** The text a JSX element shows: lines trimmed, a line break next to a tag dropped, one
 * between two text lines read as a space, tags and `{...}` children left out. */
function jsxText(jsx: string): string {
  const lines = jsx
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  let text = ''
  lines.forEach((line, i) => {
    const joinsText = i > 0 && !lines[i - 1].endsWith('>') && !line.startsWith('<')
    text += (joinsText ? ' ' : '') + line
  })
  return text
    .replace(/<[^>]*>/g, '')
    .replace(/\{[^}]*\}/g, '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .trim()
}

test('the console says live runs start from the terminal, in the captain’s words', () => {
  const note = panel.match(/<p id="ops-live-from-terminal"[^>]*>([\s\S]*?)<\/p>/)
  assert.ok(note, 'the note is rendered')
  assert.equal(
    jsxText(note[1]),
    'Live runs are started from the operator terminal (make demo-live SCEN=<id>); this console follows the run automatically.',
  )
  assert.match(panel, /\{LIVE_RUNS_FROM_TERMINAL && \(\s*<p id="ops-live-from-terminal"/)
})

test('"Judging run (live)" is hidden while live runs start from the terminal; Replay stays', () => {
  assert.match(panel, /^const LIVE_RUNS_FROM_TERMINAL = true$/m)
  const hidden = panel.match(/\{!LIVE_RUNS_FROM_TERMINAL && \(([\s\S]*?)\)\}/)
  assert.ok(hidden, 'the judging button sits behind the flag')
  assert.match(hidden[1], /Judging run \(live\)/)
  assert.equal(panel.split('Judging run (live)').length - 1, 2) // the comment and the hidden button only
  assert.match(panel, /Replay \{ms \/ 1000\} s/)
  assert.match(panel, /\[3000, 15000\]\.map/)
})
