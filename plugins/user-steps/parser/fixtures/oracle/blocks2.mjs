// Targeted block-splitting cases around fuzz/3913 (the one case in 100k fuzz
// where the per-block checks change the verdict).
const PLAN = '## Plan\n\n' + Array(26).fill('implementation').join(' ') + ' \n'
const LIST = '- Inspect the existing implementation carefully.\n- Run the relevant targeted tests.\n'
const ASK = '::user-step{t="Proceed with this?"}'
const mids = {
  'bracket-img-delim': '\\[\n<img src="a.png"> \n|---|---|\n',
  'img-delim': '<img src="a.png"> \n|---|---|\n',
  'text-delim': 'word \n|---|---|\n',
  'bracket-delim': '\\[\n|---|---|\n',
  'br-delim': '<br> \n|---|---|\n',
  'img-delim-colon': '<img src="a.png">\n|:-|-:|\n',
  'img-setext': '<img src="a.png">\n---\n',
  'img-table': '<img src="a.png"> | a |\n|---|---|\n',
}
const cases = []
for (const [name, mid] of Object.entries(mids)) {
  for (const [pn, plan] of [['prose', PLAN], ['list', LIST]]) {
    for (const [sn, sep] of [['b2', '\n\n'], ['b1', '\n']]) {
      cases.push({ id: `blocks2/${name}/${pn}/${sn}`, class: 'blocks2', reply: plan + mid + sep + ASK })
    }
  }
}
export default cases
