import test from 'node:test'
import assert from 'node:assert/strict'
import { React, host, route, snapshot, fixtures, loadPlugin, mount, button } from './sdk-mock.mjs'
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
        rest: async (path) => (path.includes('/runners/') ? d : s),
        os: { openExternal: async (url) => links.push(url) }
      }
    })
  )
  try {
    await ui.click(button('Show runners'))
    assert.match(document.body.textContent, /Busy — job not identified/)
    await ui.click(document.querySelector('[data-runner-row]'))
    await ui.click(button('Open current job 456'))
    assert.deepEqual(links, ['https://github.com/example/project/actions/runs/123/job/456'])
    assert.doesNotMatch(document.body.textContent, /Busy — job not identified/)
  } finally {
    await ui.close()
  }
})
