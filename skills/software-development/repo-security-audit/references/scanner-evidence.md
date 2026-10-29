# Scanner execution and evidence

Use when collecting SAST, dependency, secret, configuration or network scanner evidence. Keep scanner observations separate from validated exploit paths.

## 1. Preserve a reproducible baseline

Record source SHA, fetched refs, scanner versions, exact commands, exit codes and output paths. Use a full clone for historical secret scanning; a successful scan of a shallow clone covers only its retained history. Keep raw artifacts private and emit bounded summaries rather than entire scanner JSON: source snippets and matched values can contain secrets.

For an independent Gitleaks baseline, use a separate config containing `[extend]` and `useDefault = true`, plus an empty ignore file, so repository suppressions do not silently define coverage:

```sh
gitleaks git --config audit-default.toml --gitleaks-ignore-path empty.gitleaksignore \
  --redact=100 --log-opts=--all --report-format json --report-path history.json SOURCE
gitleaks dir --config audit-default.toml --gitleaks-ignore-path empty.gitleaksignore \
  --redact=100 --report-format json --report-path current.json SOURCE
```

Compare historical occurrences with current-tree matches. Classify public identifiers, fixtures and credential candidates separately; repeated commits inflate occurrence counts, and a historical credential is not proof that it still authenticates.

## 2. Audit Python versions without executing package builds

For fully pinned requirements, use:

```sh
pip-audit -r requirements.txt --format json --progress-spinner off --disable-pip --no-deps
```

Pair `--disable-pip` with `--no-deps` for unhashed pinned inputs; without it, pip-audit rejects the command rather than auditing. Label this result direct-only: skipping resolution does not establish transitive coverage.

Then enumerate installed distributions in each relevant runtime from `lib/python*/site-packages/*.dist-info/METADATA`, extracting only Name and Version. Save a separate `name==version` snapshot per runtime and audit it with the same command. Reading metadata avoids importing the application or executing package setup code and captures installed transitive versions that source manifests omit. An empty metadata directory is not a clean native/Go dependency scan.

Keep manifest-range results distinct from installed-version results. Inspect OSV matches against ranges such as `package>=VERSION` before treating the lower bound as the deployed version. Separate maintenance advisories from exploitable defects, and deduplicate advisory aliases before counting.

## 3. Recover partial SAST coverage

Parse Semgrep's JSON `errors` even when its exit code is zero. Extract unique paths whose error type is `Timeout`, then rerun those paths with the same rule packs and a larger per-rule budget:

```sh
semgrep scan --config p/security-audit --config p/secrets \
  --metrics off --disable-version-check --timeout 60 --timeout-threshold 0 --jobs 3 \
  --json --output retry.json PATHS...
```

Retain the initial output and inspect retry errors. Deduplicate matches by rule, path and start/end line. Do not erase syntax or partial-parsing gaps merely because the retry resolved timeouts; they need separate analysis and remain coverage limitations.

## 4. Validate host exposure and proxy boundaries

On macOS, collect both process-attributed listeners and the kernel socket table:

```sh
lsof -nP -iTCP -sTCP:LISTEN -Fpcn
netstat -an -p tcp
```

Extract `LISTEN` rows from the second command and compare bind addresses. Non-root `lsof` can omit root-owned services; an empty match is not proof that SSH or Screen Sharing is closed. Retain unmatched kernel listeners with process ownership unknown rather than escalating permissions just to name them. Separate a bind address, reachability from the tested origin, firewall/ACL enforcement, and public-internet exposure.

When a proxy can reach a host's loopback, test the combined path with one inert request to a known owned service, not only the proxy handshake. A SOCKS CONNECT followed by a browser debugger's `/json/version` can establish that the loopback boundary is crossed without opening its websocket, issuing browser commands, listing pages or reading cookies. Record only status and capability presence; do not retain session URLs. Attribute reachability only to the tested origin until ACL evidence supports a broader claim.

## 5. Bound parser reproductions outside the parser process

Test malformed authentication/header inputs against the exact deployed source in an isolated subprocess before trying production data. Pair each malformed case with a valid control, capture the source SHA256, and enforce an external deadline with `subprocess.run(..., timeout=2)`. A lexer that never advances cannot enforce its own timeout; an in-process regression test can hang the entire suite. For delimiter cases, inspect whether every lexer branch advances the cursor or rejects input. Record a timeout as a bounded hang reproduction, not proof of production exploitation.

For subsequent metadata-only checks of real messages, use a separate timeout-bounded subprocess per file and emit only allowlisted verdicts, attribution booleans and file hashes. Suppress raw stdout/stderr and exception messages on failure; report exception type or timeout instead. Count available files explicitly rather than implying the requested sample size existed.

## 6. Freeze completed evidence before comparison

After every intended scan and recovery attempt finishes, write a manifest of artifact paths and SHA256 hashes; verify it on recovery before rerunning work. For a fresh-first audit, combine this manifest with all manual and supplementary lane manifests before opening prior reports. Execution complete with documented gaps is a valid scanner handoff, not a claim that the system is safe or that remediation is complete.
