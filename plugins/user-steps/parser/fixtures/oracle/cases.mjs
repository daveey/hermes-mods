// Differential case corpus for the user-steps Desktop scanner (synthetic).
// Deterministic: same file -> same cases. Each case: {id, reply, class}.
const LIST = '# Plan\n\n- Inspect the existing implementation carefully.\n- Run the relevant targeted tests.'
const PROSE = '# Plan\n\n' + Array(26).fill('implementation').join(' ')
const ASK = '::user-step{t="Ready to proceed?"}'
const ask = body => `::user-step{${body}}`
const cases = []
const add = (cls, id, reply) => cases.push({ id: `${cls}/${id}`, class: cls, reply })

// Healthy ordinary controls (must stay accepted where they were).
add('control', 'list', LIST + '\n\n' + ASK)
add('control', 'prose', PROSE + '\n\n' + ASK)
add('control', 'prefix', 'Introduction must survive.\n\n' + LIST + '\n\n' + ASK)
add('control', 'id-s-t', LIST + '\n\n' + ask('id="a1b2" s="sess-1" t="Approve the plan?"'))
add('control', 'url', LIST + '\n\n' + ask('url="https://example.com/a_b?x=1&y=2"'))
add('control', 'url-tilde', LIST + '\n\n' + ask('url="https://example.com/~user/a_b~c"'))
add('control', 'www', LIST + '\n\n' + ask('t="see www.example.com/a_b now"'))
add('control', 'email', LIST + '\n\n' + ask('t="mail you@example.com please"'))
add('control', 'angle-url', LIST + '\n\n' + ask('url="<https://example.com/x>"'))
add('control', 'ordered', '1. Inspect the existing implementation carefully.\n2. Run the relevant targeted tests.\n\n' + ASK)
add('control', 'crlf', (LIST + '\n\n' + ASK).replace(/\n/g, '\r\n'))
add('control', 'trailing-ws', LIST + '\n\n' + ASK + '\n\n')
add('control', 'single-quote', LIST + '\n\n' + ask("t='Ready to proceed?'"))
add('control', 'stars-in-attr', LIST + '\n\n' + ask('t="run **all** the _tests_ now"'))
add('control', 'backtick-in-attr', LIST + '\n\n' + ask('t="run `pytest` now"'))
add('control', 'brackets-in-attr', LIST + '\n\n' + ask('t="see [docs](https://example.com) now"'))
add('control', 'amp-lt', LIST + '\n\n' + ask('t="a &amp; b < c > d"'))
add('control', 'plan-bold', '# Plan\n\n- Inspect the **existing** implementation carefully.\n- Run the relevant `targeted` tests.\n\n' + ASK)
add('control', 'plan-link', '# Plan\n\n- Inspect https://example.com/a_b implementation carefully.\n- Run the relevant targeted tests.\n\n' + ASK)
add('control', 'plan-code-fence', '```sh\necho hi\n```\n\n' + LIST + '\n\n' + ASK)
add('control', 'nested-list-plan', '- Inspect the existing implementation carefully.\n  - sub item with several words here\n- Run the relevant targeted tests.\n\n' + ASK)

// Math on/off: single-dollar math is ON in Desktop (singleDollarTextMath: true).
for (const [id, body] of [
  ['dollar-attr', 't="costs $5 and $6 today"'],
  ['dollar-pair-attr', 't="solve $x^2$ first"'],
  ['double-dollar-attr', 't="block $$x$$ here"'],
  ['latex-paren-attr', 't="solve \\(x^2\\) first"'],
  ['latex-bracket-attr', 't="solve \\[x^2\\] first"'],
  ['math-tag-attr', 't="solve [/math]x[/math] first"'],
  ['inline-tag-attr', 't="solve [/inline]x[/inline] first"'],
  ['escaped-dollar-attr', 't="costs \\$5 and \\$6"'],
  ['lone-dollar-attr', 't="costs $5"'],
  ['dollar-url-attr', 'url="https://example.com/?a=$1&b=$2"']
]) add('math', id, LIST + '\n\n' + ask(body))
for (const [id, plan] of [
  ['inline-in-plan', '# Plan\n\n- Compute $x^2 + y^2$ for each input value.\n- Run the relevant targeted tests.'],
  ['currency-in-plan', '# Plan\n\n- Spend $5 on the first item today.\n- Spend $6 on the second item today.'],
  ['display-in-plan', '# Plan\n\n$$\nx^2\n$$\n\n- Inspect the existing implementation carefully.\n- Run the relevant targeted tests.'],
  ['display-in-item', '# Plan\n\n- Inspect the existing implementation $$x$$ carefully.\n- Run the relevant targeted tests.'],
  ['unclosed-display-in-item', '# Plan\n\n- Inspect the existing implementation carefully.\n- $$ Run the relevant targeted tests.'],
  ['odd-double-dollar-plan', '# Plan\n\n- Inspect the $$ existing implementation carefully.\n- Run the relevant targeted tests.'],
  ['latex-display-plan', '# Plan\n\n\\[\nx^2\n\\]\n\n- Inspect the existing implementation carefully.\n- Run the relevant targeted tests.'],
  ['math-fence', '```math\nx^2\n```\n\n' + LIST]
]) add('math', id, plan + '\n\n' + ASK)
add('math', 'unclosed-display-before-ask', LIST + '\n\n$$\n\n' + ASK)
add('math', 'unclosed-display-line-before-ask', LIST + '\n$$\n\n' + ASK)
add('math', 'ask-inside-display', LIST + '\n\n$$\n' + ASK + '\n$$')
add('math', 'ask-inside-unclosed-display', LIST + '\n\n$$\n' + ASK)
add('math', 'inline-wraps-ask', LIST + '\n\n$x ' + ASK + ' y$')
add('math', 'dollar-before-ask', LIST + '\n\n$ ' + ASK)
add('math', 'ask-then-display', LIST + '\n\n' + ASK + '\n\n$$\nx\n$$')
add('math', 'ask-then-dollar', LIST + '\n\n' + ASK + ' $5')
add('math', 'display-hug-ask', LIST + '\n\n$$' + ASK + '$$')
// Math display directly after the list: without remark-math it is a lazy
// paragraph continuation of the last item; Desktop renders a math block.
add('math', 'display-after-list-lazy', LIST + '\n$$\nx^2\n$$\n\n' + ASK)
add('math', 'display-after-list-blank', LIST + '\n\n$$\nx^2\n$$\n\n' + ASK)
add('math', 'display-after-prose', '# Plan\n\n' + Array(26).fill('implementation').join(' ') + '\n$$\nx\n$$\n\n' + ASK)
add('math', 'inline-only-item', '# Plan\n\n- $a b c d$\n- Run the relevant targeted tests.\n\n' + ASK)
add('math', 'display-heading-plan', '# $plan$\n\n' + Array(26).fill('implementation').join(' ') + '\n\n' + ASK)
add('math', 'four-dollar-fence', LIST + '\n\n$$$$\n\n' + ASK)
add('math', 'four-dollar-fence-tight', LIST + '\n\n$$$$\n' + ASK)
add('math', 'item-display', '# Plan\n\n- Inspect the existing implementation carefully.\n- Run the relevant targeted tests.\n  $$\n  x\n  $$\n\n' + ASK)

// Incomplete constructs (remend territory).
for (const [id, tail] of [
  ['bold', '**'], ['italic', '*'], ['underscore', '_'], ['backtick', '`'], ['strike', '~~'],
  ['link', '['], ['link-url', '[x](https://example.com'], ['image', '!['], ['fence', '```'], ['dollars', '$$'],
  ['think', '<think>'], ['html', '<div>'], ['comment', '<!--']
]) {
  add('incomplete', `tail-${id}`, LIST + '\n\n' + ASK + ' ' + tail)
  add('incomplete', `tail-line-${id}`, LIST + '\n\n' + ASK + '\n' + tail)
  add('incomplete', `tail-block-${id}`, LIST + '\n\n' + ASK + '\n\n' + tail)
  add('incomplete', `plan-${id}`, '# Plan\n\n- Inspect the existing ' + tail + 'implementation carefully.\n- Run the relevant targeted tests.\n\n' + ASK)
  add('incomplete', `lead-${id}`, LIST + '\n\n' + tail + ASK)
  add('incomplete', `attr-${id}`, LIST + '\n\n' + ask('t="before ' + tail + ' after"'))
  add('incomplete', `unclosed-brace-${id}`, LIST + '\n\n::user-step{t="x ' + tail)
}
add('incomplete', 'unclosed-brace', LIST + '\n\n::user-step{t="Ready?"')
add('incomplete', 'unclosed-quote', LIST + '\n\n::user-step{t="Ready?}')
add('incomplete', 'bold-spans-blank', '# Plan\n\n- Inspect the **existing implementation carefully.\n- Run the relevant targeted tests.\n\n' + ASK + '**')
add('incomplete', 'odd-dollars-window', 'Total $$ cost\n\n' + LIST + '\n\n' + ASK)
add('incomplete', 'odd-dollars-window-bold', 'Total $$ cost **bold\n\n' + LIST + '\n\n' + ASK)
add('incomplete', 'open-fence-window', '```\ncode\n\n' + LIST + '\n\n' + ASK)
add('incomplete', 'open-think-before', '<think>\nreasoning ' + ASK + '\n\n' + LIST + '\n\n' + ASK)

// Hidden / nested / nonfinal directives.
add('hidden', 'think-other', '<think>::other{}</think>\n' + LIST + '\n\n' + ASK)
add('hidden', 'think-ask', '<think>' + ASK + '</think>\n\n' + LIST + '\n\n' + ASK)
add('hidden', 'think-only', LIST + '\n\n<think>' + ASK + '</think>')
add('hidden', 'unclosed-think-ask', LIST + '\n\n<think>\n' + ASK)
add('hidden', 'comment-ask', '<!-- ' + ASK + ' -->\n\n' + LIST + '\n\n' + ASK)
add('hidden', 'comment-after', LIST + '\n\n' + ASK + '\n\n<!-- hidden -->')
add('hidden', 'details', LIST + '\n\n<details>\n\n' + ASK + '\n\n</details>')
add('hidden', 'entity-colon', LIST + '\n\n&#58;:user-step{t="x"}')
add('hidden', 'escaped-colon', LIST + '\n\n\\::user-step{t="x"}')
add('hidden', 'entity-plus-think', '<think>::other{}</think>\n' + LIST + '\n\n&#58;:user-step{t="x"}')
add('nested', 'quote', LIST + '\n\n> ' + ASK)
add('nested', 'item', LIST + '\n- ' + ASK)
add('nested', 'item-para', LIST + '\n\n  ' + ASK)
add('nested', 'sub-item', LIST + '\n  - ' + ASK)
add('nested', 'indented-code', LIST + '\n\n    ' + ASK)
add('nested', 'heading', LIST + '\n\n# ' + ASK)
add('nested', 'setext', LIST + '\n\n' + ASK + '\n---')
add('nested', 'table', LIST + '\n\n| a |\n|---|\n| ' + ASK + ' |')
add('nested', 'emphasis', LIST + '\n\n*' + ASK + '*')
add('nested', 'link-label', LIST + '\n\n[' + ASK + '](https://example.com)')
add('nested', 'lazy', LIST + '\n' + ASK)
add('nested', 'html-block', LIST + '\n\n<div>\n' + ASK + '\n</div>')
add('nested', 'html-inline', LIST + '\n\n<span>' + ASK + '</span>')
add('nonfinal', 'trailing-text', LIST + '\n\n' + ASK + '\n\nLater')
add('nonfinal', 'two-asks', LIST + '\n\n' + ASK + '\n\n' + ASK)
add('nonfinal', 'two-asks-one-para', LIST + '\n\n' + ASK + ' ' + ASK)
add('nonfinal', 'inline-prefix', LIST + '\n\nPlease ' + ASK)
add('nonfinal', 'inline-suffix', LIST + '\n\n' + ASK + ' thanks')
add('nonfinal', 'unknown-then-ask', LIST + '\n\n::other{}\n\n' + ASK)
add('nonfinal', 'ask-then-def', LIST + '\n\n' + ASK + '\n\n[x]: https://example.com')
add('nonfinal', 'ask-then-footnote', LIST + '\n\n' + ASK + '\n\n[^1]: note')
add('nonfinal', 'ask-then-rule', LIST + '\n\n' + ASK + '\n\n---')
add('nonfinal', 'ask-hardbreak', LIST + '\n\n' + ASK + '\\')
add('nonfinal', 'ask-two-space', LIST + '\n\n' + ASK + '  ')
add('nonfinal', 'code-ask', LIST + '\n\n```\n' + ASK + '\n```')
add('nonfinal', 'inline-code-ask', LIST + '\n\n`' + ASK + '`')
add('nonfinal', 'ipv6', LIST + '\n\nConnect to [::1] then ' + ASK)
add('nonfinal', 'cpp', LIST + '\n\nUse std::vector\n\n' + ASK)

// Plan gating.
add('plan', 'short-list', '- A\n- B\n\n' + ASK)
add('plan', 'one-item', '- Inspect the existing implementation carefully.\n\n' + ASK)
add('plan', 'no-heading', Array(26).fill('implementation').join(' ') + '\n\n' + ASK)
add('plan', 'none', ASK)
add('plan', 'confirmation-text', LIST + '\n\nShould I proceed?')

// Block splitting (marked) vs whole-document micromark.
add('blocks', 'no-blank', LIST + '\n' + ASK)
add('blocks', 'after-html-no-blank', LIST + '\n\n<div>x</div>\n' + ASK)
add('blocks', 'after-html-blank', LIST + '\n\n<div>x</div>\n\n' + ASK)
add('blocks', 'after-comment-blank', LIST + '\n\n<!-- c -->\n\n' + ASK)
add('blocks', 'after-unclosed-comment', LIST + '\n\n<!-- c\n\n' + ASK)
add('blocks', 'after-indented', LIST + '\n\n    code\n\n' + ASK)
add('blocks', 'list-then-indented-ask', LIST + '\n\n    ' + ASK)
add('blocks', 'after-table', LIST + '\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n' + ASK)
add('blocks', 'table-plan', '| a | b |\n|---|---|\n| 1 | 2 |\n' + ASK)
add('blocks', 'after-def', LIST + '\n\n[x]: https://example.com\n\n' + ASK)
add('blocks', 'ref-link-ask', LIST + '\n\n[x]: https://example.com\n\n' + ask('t="see [x] now"'))
add('blocks', 'quote-lazy', '> quoted plan line\n' + ASK)
add('blocks', 'loose-list', '- Inspect the existing implementation carefully.\n\n- Run the relevant targeted tests.\n\n' + ASK)
add('blocks', 'list-blank-lines', LIST + '\n\n\n\n' + ASK)
add('blocks', 'tabs', LIST + '\n\n\t' + ASK)
add('blocks', 'space3', LIST + '\n\n   ' + ASK)
add('blocks', 'nbsp', LIST + '\n\n\u00a0' + ASK)
add('blocks', 'cr-only', (LIST + '\n\n' + ASK).replace(/\n/g, '\r'))
add('blocks', 'u2028', LIST + '\u2028' + ASK)
add('blocks', 'fence-tilde-plan', '~~~\nx\n~~~\n\n' + LIST + '\n\n' + ASK)
add('blocks', 'setext-plan', 'Plan\n====\n\n' + Array(26).fill('implementation').join(' ') + '\n\n' + ASK)
add('blocks', 'setext-merge', '#e\n5\n-\n\n' + LIST + '\n\n' + ASK)
add('blocks', 'html-plan-unclosed', '<div>\n\n' + LIST + '\n\n' + ASK)
add('blocks', 'pre-plan-unclosed', '<pre>\n\n' + LIST + '\n\n' + ASK)
add('blocks', 'script-unclosed', '<script>\n\n' + LIST + '\n\n' + ASK)
add('blocks', 'style-unclosed', '<style>\n\n' + LIST + '\n\n' + ASK)
add('blocks', 'textarea-unclosed', '<textarea>\n\n' + LIST + '\n\n' + ASK)
add('blocks', 'cdata-unclosed', '<![CDATA[\n\n' + LIST + '\n\n' + ASK)
add('blocks', 'pi-unclosed', '<?php\n\n' + LIST + '\n\n' + ASK)
add('blocks', 'decl-unclosed', '<!DOCTYPE\n\n' + LIST + '\n\n' + ASK)

// Long plan: crosses the 2048-char streaming append threshold in markdown-blocks.
add('blocks', 'long-plan', '# Plan\n\n' + Array.from({ length: 80 }, (_, i) => `- Step ${i} inspects the existing implementation carefully.`).join('\n') + '\n\n' + ASK)

// Sentinels / malformed.
add('malformed', 'sentinel', LIST + '\n\n' + ask('t="a\uE000b"'))
add('malformed', 'dup-attr', LIST + '\n\n' + ask('t="x" t="y"'))
add('malformed', 'dup-case', LIST + '\n\n' + ask('t="x" T="y"'))
add('malformed', 'junk', LIST + '\n\n' + ask('t="x" garbage'))
add('malformed', 'empty-brace', LIST + '\n\n::user-step{}')
add('malformed', 'no-brace', LIST + '\n\n::user-step')
add('malformed', 'name-upper', LIST + '\n\n::User-step{t="x"}')
add('malformed', 'multiline-attr', LIST + '\n\n::user-step{t="x\ny"}')
add('malformed', 'long-body', LIST + '\n\n' + ask('t="' + 'x'.repeat(2100) + '"'))

export default cases
