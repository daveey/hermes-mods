import test from 'node:test'
import assert from 'node:assert/strict'
import { React, host, route, snapshot, fixtures, loadPlugin, mount, button, enrollment } from './sdk-mock.mjs'
const p = await loadPlugin()
test('job links require exact provider, scope, assignment, owner and numeric IDs', () => {
  const s = snapshot(),
    row = p.projectFleet([{ route, snapshot: s }]).rows[0]
  const job = {
    ...fixtures.Job,
    owner: s.meta.owner,
    assigned_runner_id: row.id,
    provider_run_id: '123',
    provider_job_id: '456'
  }
  assert.equal(p.jobURL(row, job), 'https://github.com/example/project/actions/runs/123/job/456')
  for (const invalid of [
    { ...job, owner: null },
    { ...job, assigned_runner_id: 'other' },
    { ...job, provider_run_id: '../evil' },
    { ...job, owner: { ...job.owner, gateway_id: 'other' } }
  ])
    assert.equal(p.jobURL(row, invalid), null)
  assert.equal(p.jobURL({ ...row, endpoint: 'https://evil.example' }, job), null)
})
test('external observed job links validate assignment scope freshness and canonical URL', () => {
  const s = snapshot(), row = p.projectFleet([{ route, snapshot: s }]).rows[0], now = Date.now()
  const job = { ...fixtures.ObservedJob, repository: row.scope.name, runner_id: row.provider_runner_id,
    run_id: '123', job_id: '456', status: 'in_progress', observed_at: new Date(now - 1000).toISOString(), expires_at: new Date(now + 10000).toISOString(), url: 'https://github.com/example/project/actions/runs/123/job/456' }
  assert.equal(p.observedJobURL(row, job, now), job.url)
  for (const change of [{runner_id:'other'}, {repository:'other/project'}, {expires_at:new Date(now - 1).toISOString()}, {url:'https://evil.example/'}, {run_id:'../x'}, {observed_at:new Date(now + 1).toISOString()}])
    assert.equal(p.observedJobURL(row, {...job, ...change}, now), null)
  assert.equal(p.observedJobURL({...row, stale:true}, job, now), null)
  assert.equal(p.observedJobURL({...row, scope:{...row.scope, kind:'organization', name:'example'}}, job, now), job.url)
})
test('empty job data does not claim idle or unconfigured monitoring', async () => {
  const s = snapshot()
  s.data.runners[0].activity = 'busy'
  const d = { ...s, data: { ...fixtures.RunnerDetail, runner: s.data.runners[0], jobs: [], job_monitoring: undefined } }
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: {
    rest: async path => path.includes('/enrollment') ? enrollment() : path.includes('/runners/') ? d : s
  } }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    await ui.click(document.querySelector('[data-runner-row]'))
    const detail = document.querySelector('.hermes-runners-detail')
    assert.match(detail.textContent, /Job attribution unavailable/)
    assert.doesNotMatch(detail.textContent, /not configured|Current job not identified|idle/i)
  } finally { await ui.close() }
})
for (const [state, expected] of [
  ['not_configured', 'Job details not configured'],
  ['forbidden', 'Job details permission denied'],
  ['unavailable', 'Job collector unavailable'],
  ['unknown', 'Job attribution unavailable'],
  ['configured', 'Current job not identified']
]) test(`explicit job monitoring ${state} stays distinct from activity`, async () => {
  const s = snapshot()
  s.data.runners[0].activity = 'busy'
  const d = { ...s, data: { ...fixtures.RunnerDetail, runner: s.data.runners[0], jobs: [], observed_jobs: [], job_monitoring: { state, reason_code: null, observed_at: s.meta.observed_at, expires_at: new Date(Date.now() + 60000).toISOString() } } }
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : path.includes('/runners/') ? d : s } }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    await ui.click(document.querySelector('[data-runner-row]'))
    assert.ok(document.querySelector('.hermes-runners-detail').textContent.includes(expected))
  } finally { await ui.close() }
})
test('provider observations render separately and supply a lazy current-job link', async () => {
  const s = snapshot()
  s.data.runners[0].activity = 'busy'
  const job = { ...fixtures.ObservedJob, repository: 'example/project', runner_id: s.data.runners[0].provider_runner_id, run_id: '123', job_id: '456', status: 'in_progress', observed_at: new Date().toISOString(), expires_at: new Date(Date.now()+60000).toISOString(), url: 'https://github.com/example/project/actions/runs/123/job/456' }
  const d = { ...s, data: { ...fixtures.RunnerDetail, runner: s.data.runners[0], jobs: [], observed_jobs: [job] } }
  const links = []
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : path.includes('/runners/') ? d : s, os: { openExternal: async url => links.push(url) } } }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    assert.equal(button('Open current job 456'), undefined)
    await ui.click(document.querySelector('[data-runner-row]'))
    assert.match(document.querySelector('.hermes-runners-detail').textContent, /Provider job observations: 1.*example\/project.*456.*in_progress.*current/)
    assert.ok(button('Open current job 456'))
    await ui.click(button('Open current job 456'))
    assert.deepEqual(links, [job.url])
  } finally { await ui.close() }
})
test('known job appears only after bounded detail read and SDK opens its exact URL', async () => {
  const s = snapshot()
  s.data.runners[0].activity = 'busy'
  const j = {
    ...fixtures.Job,
    owner: s.meta.owner,
    assigned_runner_id: s.data.runners[0].id,
    status: 'running',
    provider_run_id: '123',
    provider_job_id: '456'
  }
  const d = { ...s, data: { ...fixtures.RunnerDetail, runner: s.data.runners[0], jobs: [j] } }
  const links = []
  host.profileRoutes = async () => [route]
  const ui = await mount(
    React.createElement(p.Runners, {
      ctx: {
        rest: async (path) => path.includes('/enrollment') ? enrollment() : (path.includes('/runners/') ? d : s),
        os: { openExternal: async (url) => links.push(url) }
      }
    })
  )
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    assert.match(document.body.textContent, /Running — job not identified/)
    await ui.click(document.querySelector('[data-runner-row]'))
    await ui.click(button('Open current job 456'))
    assert.deepEqual(links, ['https://github.com/example/project/actions/runs/123/job/456'])
    assert.match(document.body.textContent, /Running — job 456/)
    assert.doesNotMatch(document.querySelector('[data-runner-row]').textContent, /job not identified/)
  } finally {
    await ui.close()
  }
})
