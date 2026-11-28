// Deterministic structural fuzz corpus aimed at context-sensitive block
// splitting (marked blocks vs one micromark document). Exports N cases.
const N = Number(process.env.FUZZ_N ?? 3000)
let seed = 0x5eed1234
const rnd = () => { seed ^= seed << 13; seed >>>= 0; seed ^= seed >>> 17; seed ^= seed << 5; seed >>>= 0; return seed / 2 ** 32 }
const pick = a => a[Math.floor(rnd() * a.length)]
const PIECES = [
  '<div>', '</div>', '<div>\ntext inside', '<!-- note', '-->', '<pre>', '</pre>', '<details>\n<summary>More</summary>', '</details>',
  '```', '```js\nconst a = 1', '~~~', '    indented code line', '> quoted line', '>', '> - quoted item one two three',
  '- item alpha bravo charlie delta', '1. numbered alpha bravo charlie', '  - nested echo foxtrot golf hotel', '* star item india juliet kilo',
  '| a | b |\n|---|---|\n| 1 | 2 |', '| a | b |', '|---|---|', '[ref]: https://example.com/a', '[^1]: footnote text', 'See [^1] here.',
  'Title line\n===', 'Title line\n---', '---', '***', '$$', '$$\nx^2\n$$', '\\[', '\\]', '$x$ inline', 'Plain prose sentence here.',
  '<br>', '<img src="a.png">', '<script>', '</script>', '<style>', '<textarea>', '<?php', '<![CDATA[', '<!DOCTYPE html>',
  '# Heading plan', '## Plan', '\t- tab item lima mike november oscar', '+ plus item papa quebec romeo sierra', '- [ ] task tango uniform victor whiskey',
  'Term\n: definition', '<https://example.com>', 'https://example.com/path', '**bold** and _em_', '`code`', '``', '<a href="x">', '</a>',
]
const SEPS = ['\n', '\n\n', '\r\n', '\r\n\r\n', '\n\n\n', ' \n', '\r', '\n \n']
const PLANS = [
  '# Plan\n\n- Inspect the existing implementation carefully.\n- Run the relevant targeted tests.',
  '- Inspect the existing implementation carefully.\n- Run the relevant targeted tests.',
  '1. Inspect the existing implementation carefully.\n2. Run the relevant targeted tests.',
  '## Plan\n\n' + Array(26).fill('implementation').join(' '),
]
const ASKS = ['::user-step{t="Proceed with this?"}', ' ::user-step{t="Proceed?"}', '   ::user-step{t="Go?"}', '\t::user-step{t="Go?"}', '> ::user-step{t="Go?"}', '    ::user-step{t="Go?"}', '- ::user-step{t="Go?"}']
const cases = []
for (let i = 0; i < N; i++) {
  const parts = []
  const k = Math.floor(rnd() * 4)
  for (let j = 0; j < k; j++) parts.push(pick(PIECES) + pick(SEPS))
  const where = rnd()
  let reply
  if (where < 0.4) reply = parts.join('') + pick(PLANS) + pick(SEPS) + pick(ASKS)
  else if (where < 0.7) reply = pick(PLANS) + pick(SEPS) + parts.join('') + pick(ASKS)
  else reply = parts.join('') + pick(PLANS) + pick(SEPS) + pick(PIECES) + pick(SEPS) + pick(ASKS)
  if (rnd() < 0.15) reply += pick(['\n', '\n\n', ' ', '\r\n', '\n' + pick(PIECES)])
  cases.push({ id: `fuzz/${String(i).padStart(4, '0')}`, class: 'fuzz', reply })
}
export default cases
