// Cross-language contract probe: consume the actual Service.inventory response.
import { readFileSync } from 'node:fs'
import { loadPlugin, route } from './sdk-mock.mjs'
const { snapshot, now } = JSON.parse(readFileSync(0, 'utf8'))
const plugin = await loadPlugin()
const result = plugin.projectFleet([{ route, snapshot }], now * 1000)
console.log(JSON.stringify(result.counts))
