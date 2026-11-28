// Block-splitting cases (generated from blocks2 shapes + the 15 replies that
// fuzz-fence.mjs found where marked and micromark disagree about a fence).
// Desktop rendered no card for every fence/* case; the baseline accepted all.
import { readFileSync } from 'node:fs'
import blocks2 from './blocks2.mjs'
const ids = 'fence/05920 fence/08130 fence/09641 fence/12696 fence/13588 fence/25257 fence/25383 fence/27057 fence/28417 fence/31358 fence/33160 fence/35133 fence/36874 fence/39272 fence/39960'.split(' ')
const replies = JSON.parse(readFileSync(new URL('./b3-replies.json', import.meta.url), 'utf8'))
if (replies.length !== ids.length) throw new Error('b3 mismatch')
export default [...blocks2, ...ids.map((id, i) => ({ id: `blocks3/${id}`, class: 'blocks3', reply: replies[i] }))]
