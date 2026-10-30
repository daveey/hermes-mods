# Account selection: multi-home execution seams

A process can load this plugin for more than one Hermes home (for example, the launch home and several profile scopes during plugin discovery). All instances use the canonical `account_usage_selection` module. Its core adapters are request-local, not home-specific: the owning turn's middleware resolves its own home/store and binds a credential snapshot in a ContextVar. A different home's middleware passes that turn through.

Instances adopt the existing audited adapter set as a unit. Foreign wrappers and partial installations are still refused. The adapter owner retains every adopting instance's snapshots, so unloading either home cannot strip the adapters from an outstanding worker. Install, unload and snapshot release share one reentrant lifecycle lock. A restoration epoch detects same-thread finalizers that restore adapters between owner inspection and adoption, then retries against the current seams.

## Failure analysis

- Observed: a second home's install raised `SelectionRejected` at `_create_request_anthropic_client`.
- Why: the seam was wrapped by this plugin for another home, but owner recognition required the same home.
- Why: the adoption design conflated ownership of process-global adapters with ownership of per-home account state. The wrappers already read request-local dispatch, so adapter ownership need not constrain home ownership.
- Why the earlier fix did not catch it: same-home repeated/concurrent installation tests covered only one home. They did not exercise discovery of multiple homes in a single process.
- Countermeasure: adopt the shared adapters while retaining per-home middleware and selection stores; test disjoint accounts across two complete home authorities. Preserve outstanding adopter snapshots and serialize lifetime changes. Test finalizer reentrancy, not just separate threads.

Missing-core imports from bare/stale interpreters remain a separate informational inactive state. This change does not repair those interpreters.

## Tests

`tests/test_install_cause.py` covers second-home adoption and foreign-home middleware pass-through, along with foreign-wrapper refusal and repeated/concurrent install checks. On the previous code, the second-home cases fail with the same `SelectionRejected` seam as the observed failure.

`tests/test_multi_home.py` exercises:

- two disjoint homes' actual pending stores and authoritative leases through both middleware instances and the shared Anthropic factory, verifying each uses its selected token;
- late adopter snapshots surviving both unload orders and restoring the original factory after release;
- reload over an inactive owner with a retained snapshot;
- a real unload thread attempting to interleave with adoption;
- same-thread snapshot release during the third and seventh owner checks;
- repeated install of the same owner without a self-adoption cycle.

The isolation fixture stubs the final SDK builder and sends no network traffic. It is not a paid-provider live-send test. Existing execution tests separately exercise mocked HTTP transport in streaming and nonstreaming modes.
