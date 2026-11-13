# A chat refuses every turn on a far quota wall

A default (router-picked) pin whose subscription wall resets 6 h or more away is rerouted within its
class by the agent build; the gateway's pre-build lookup (`route_runtime`) defers to it. When it does
not, the gateway logs once per wall episode and reason:

`routing: walled pin not deferred (reason=<r>) <provider>/<model> until <reset PT>`

| reason | meaning | action |
|---|---|---|
| `explicit` | David's switch, a model he named, or a Kanban card pin | holds by design; `/model` to switch |
| `key` | the session was launched with an API key | holds by design |
| `endpoint` | the creation request names a foreign base_url | holds by design |
| `no-target` | no other route in the class can serve now | check the other pools |
| `no-session` | lookup ran with no routing session | core bug: file a card |

A Desktop chat's own provider URL (`https://chatgpt.com/backend-api/codex` for openai-codex) is not
`endpoint`: it overrides nothing (`overrides_endpoint`). Core fix 78c49a510ba on main;
check `git -C ~/.hermes/hermes-agent merge-base --is-ancestor 78c49a510ba HEAD`. Repeated `Model resolution failed ... Subscription
quota unavailable` with no such WARNING means the installed core predates that fix.
