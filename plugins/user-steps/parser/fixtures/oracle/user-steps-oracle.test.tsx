// @vitest-environment jsdom
// Differential ORACLE for plugins/user-steps/parser. Runs inside an unmodified
// git-archive of hermes-agent apps/desktop @ the pinned core commit, with the
// Desktop's own pnpm-locked node_modules (pnpm install --frozen-lockfile
// --ignore-scripts) and its own vitest/jsdom project. It renders each reply
// through the REAL exported MarkdownTextContent (StreamdownTextPrimitive, the
// real preprocess + tailBoundedRemend, marked block splitting, remark-math,
// directive lifting, React paragraph resolution). The only test seam is the
// contribution registry: a `user-step` claim is registered exactly as a plugin
// would, rendering a marker element that records the directive source.
import { cleanup, render } from '@testing-library/react'
import { readFileSync, writeFileSync } from 'node:fs'
import { afterAll, expect, it } from 'vitest'

import { registry } from '@/contrib/registry'
import { TRANSCRIPT_DIRECTIVE_AREA, type TranscriptDirectiveContribution } from '@/lib/transcript-directives'

import { MarkdownTextContent } from './markdown-text'

const input = process.env.USER_STEPS_ORACLE_IN as string
const output = process.env.USER_STEPS_ORACLE_OUT as string
const cases: { id: string; reply: string }[] = JSON.parse(readFileSync(input, 'utf8'))
const results: Record<string, unknown>[] = []

const dispose = registry.register({
  id: 'oracle:user-step',
  area: TRANSCRIPT_DIRECTIVE_AREA,
  source: 'plugin:oracle',
  data: {
    name: 'user-step',
    render: ({ source }) => <div data-oracle-card="" data-source={source} />
  } satisfies TranscriptDirectiveContribution
})

afterAll(() => {
  dispose()
  writeFileSync(output, JSON.stringify(results, null, 1) + '\n')
})

it('renders every case through the real Desktop markdown surface', () => {
  for (const { id, reply } of cases) {
    let entry: Record<string, unknown>

    try {
      const { container } = render(<MarkdownTextContent isRunning={false} text={reply} />)
      const cards = [...container.querySelectorAll('[data-oracle-card]')]
      const sources = cards.map(card => card.getAttribute('data-source'))
      let last = false

      if (cards.length === 1) {
        // Nothing visible may follow the card in document order.
        const range = document.createRange()

        range.setStartAfter(cards[0])
        range.setEndAfter(container.lastChild as Node)
        last = range.toString().trim() === '' && !range.cloneContents().querySelector('img,svg,video,audio,iframe,input,button')
      }

      entry = { id, cards: sources, card_last: last, accept: cards.length === 1 && last, text: container.textContent }
    } catch (error) {
      entry = { id, error: String(error) }
    }

    results.push(entry)
    cleanup()
  }

  expect(results.length).toBe(cases.length)
}, 900_000)
