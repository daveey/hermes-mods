# PR owner cards: find, census, restore

A PR's owner is the card that published it and lands or closes it. Review, decision and integration cards that
name the PR are not owners. SKILL.md step 2 (first row): an owner of an open core PR (base `main`) is never
archived or left spend-triaged.

## Find a PR's owner

- Start from the review card the notice names: the owner is usually its child link (`cross_task read <review id>`,
  or the Mac's `kanban/sync-shadow/*.db`).
- Otherwise search card bodies and comments on every host for `pull/<n>`, the branch name (`headRefName`) and the
  card ids in the PR body. Searching only for `PR<n>`/`#<n>` misses owners that published the PR after their body
  was written. A branch ending in an id prefix (`<slug>-t_<8 hex>`, `<slug>-t<8 hex>`) names a card: look it up with
  `id like 't_<8 hex>%'` and confirm by title, since a short prefix can match several cards.
- Owner or mention: count each candidate's comments that pair `pull/<n>` with a publication phrase (`Published PR`,
  `canonical ship`, `opened PR`, `published https`). The owner has the most; decision and review cards only quote it.

## Owner census (sweep Done means)

1. `token-broker exec --scope user/github --reason "<why>" -- gh pr list -R <repo> --base main --state open --limit 100`
   (the default limit is 30).
2. One read-only sqlite script over `kanban.db` and `kanban/boards/*/kanban.db`, copied to the hub and run there, on
   every worker (`~/.hermes/bin/hub-worker-exec all --timeout 60 -- /tmp/<script>`), on each Mac over ssh and
   locally, printing each card whose body or comments contain `pull/<n>`, with its status. Keep archived and done
   rows: filtering them out hides exactly the owners an earlier cull removed.
3. Per PR: a live owner gets spared (SKILL.md step 2, first row). A PR whose referencing cards are all archived or
   done is orphaned: restore its owner (below). Bot PRs (a `dependabot/...` branch) have no owner card; skip them.

## Restore an archived owner

There is no unarchive verb (`hermes kanban --help` lists none), so restoring means a successor card:
- `kanban_create` with `idempotency_key='spare-core-pr-owner:<n>'`. Body: the user's words as provenance; the PR's
  URL, branch, head and last verdict; the decision the archived owner waited on and the option taken; a first step
  that stops if the PR is MERGED or CLOSED or another live card owns it; landing through the repo's route with a
  fresh exact-head review; an IF FAIL that leaves the PR open and blocks with a land-or-close choice.
- From a Mac chat the create is queued for the hub and returns `task_id: null`: poll the hub for up to a minute
  (`select id,status from tasks where idempotency_key='<key>'`) to get the id.
- Comment the successor's id on each archived owner and its archived decision card, so their history points forward.
