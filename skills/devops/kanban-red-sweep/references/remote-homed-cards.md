# Cards homed on another gateway

`no such task ... not imported here; last pickup skip: ...` on the hub means the card lives on the gateway that
created it (a worker's run filed it pinned to the hub). `hermes kanban edit` here fails with "unknown id" and a
plain twin `kanban_create` is refused `create_overlap`. Reach the origin box (`cross_task` read names the
gateway; `aws ssm send-command` with `sudo -u ubuntu -i bash -lc "hermes kanban ..."`, instance role,
us-west-2). A priority raise there is not enough when the skip count keeps climbing (top priority still sat
unimported for an hour): replace the card. Create a hub-local twin (`placement='local'`,
`distinct_from=[<id>]`, `distinct_reason` = it cannot import and is archived this turn, body = run the
original's body with all its limits, first step = stop if the original started), then comment and
`hermes kanban archive <id>` on the origin box and read back `archived`, so one owner remains.

A hub card parented on such an unimported card sits `todo` forever (`parent_missing` in its events, parent
absent from the hub's `tasks`): `kanban_unlink` it once its own acceptance is met or the twin exists.
