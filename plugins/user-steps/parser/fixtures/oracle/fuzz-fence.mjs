// fuzz-fence.mjs: fence/indent/container disagreements (marked vs micromark)
// aimed at the last-block agreement guard. Deterministic.
const N = Number(process.env.FUZZ_N ?? 3000)
let seed = 0xfe11ce5
const rnd = () => { seed ^= seed << 13; seed >>>= 0; seed ^= seed >>> 17; seed ^= seed << 5; seed >>>= 0; return seed / 2 ** 32 }
const pick = a => a[Math.floor(rnd() * a.length)]
const FENCES = ['```', '````', '~~~', '~~~~', ' ```', '  ```', '   ```', '    ```', '\t```', '``` js', '```js x', '```\t', '``` ```', '~~~ `', '```~', '`` `', '> ```', '- ```', '1. ```', '  ~~~', '```` ```']
const BODY = ['code line', '', '- not a list', '# not heading', '<div>', '$$', '>', '    deep', 'text']
const LEAD = ['', '\n', ' \n', '\t\n', '\n\n']
const PLAN = ['- Inspect the existing implementation carefully.\n- Run the relevant targeted tests.', '## Plan\n\n' + Array(26).fill('implementation').join(' ')]
const ASK = '::user-step{t="Proceed?"}'
const cases = []
for (let i = 0; i < N; i++) {
  const parts = []
  const k = 1 + Math.floor(rnd() * 4)
  for (let j = 0; j < k; j++) parts.push(rnd() < 0.6 ? pick(FENCES) : pick(BODY))
  const block = parts.join(pick(['\n', '\n', '\n\n', '\r\n']))
  const where = rnd()
  const reply = where < 0.5
    ? block + pick(LEAD) + '\n' + pick(PLAN) + pick(['\n\n', '\n']) + pick(FENCES) + pick(['\n', '\n\n']) + ASK
    : pick(PLAN) + '\n\n' + block + pick(['\n', '\n\n', '\n\n\n']) + ASK
  cases.push({ id: `fence/${String(i).padStart(5, '0')}`, class: 'fence', reply })
}
export default cases
