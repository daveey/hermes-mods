# macOS: run Hermes on a Developer-ID-signed Python (patches 0017, 0027, 0123, 0142, 0148)

Four fork patches make Hermes on macOS run on a code-signed copy of its own Python interpreter. With
that copy, privacy grants (Full Disk Access, Accessibility, Automation, Photos, Contacts, Calendar…)
survive Python upgrades.

| Patch | Files | What it does |
|---|---|---|
| `0017-…re-exec-into-Developer-ID-signed-Hermes-Pytho.patch` | `hermes_cli/_launchers.py`, `hermes_cli/venv_sync.py`, `tests/hermes_cli/test_signed_python_app.py` | Re-exec into `~/Applications/Hermes Python.app` when its Python version matches the store Python |
| `0027-…gateway-status-quote-spaced-argv-so-a-Hermes-.patch` | `gateway/status.py`, `tests/gateway/test_status_cmdline_spaced_exe.py` | Quote argv elements that contain whitespace, so a gateway running from that bundle still counts as a running gateway |
| `0123-fix-tcc-children-s-bare-python3-resolves-to-signed-H.patch` | `hermes_cli/_launchers.py`, `tools/environments/local.py`, `tests/hermes_cli/test_signed_python_path.py` | Put the bundle's `Contents/MacOS` ahead of the matching store `bin` on every child PATH, so a bare `python3` in a terminal command or cron script runs the signed bundle |
| `0142-fix-gateway-signed-Hermes-Python.app-owns-the-launch.patch` | `hermes_cli/gateway_launchd.py`, `gateway/status.py`, `tests/hermes_cli/test_gateway_service.py` | The launchd gateway job is started by the signed bundle instead of `/usr/bin/osascript`, so children's Accessibility/Automation prompts name Hermes Python, not "osascript" |
| `0148-fix-tests-daveey-flush-pgrp-print-before-SIGTERM-in-.patch` | `tests/hermes_cli/test_gateway_service.py` | Test-only fix-up for 0142: the wrapper tests' children flush their process-group print, so the SIGTERM case no longer loses it |

All four patches do nothing unless the bundle exists. On Linux and Windows, and on Macs without
`~/Applications/Hermes Python.app`, Hermes behaves exactly like upstream.

## Behavior

- No config keys, flags or UI. The only switch is the presence of
  `~/Applications/Hermes Python.app` (fixed path, fixed name, under the user's home).
- Bundle present, `CFBundleShortVersionString` == store Python version → every self-managed Hermes
  process (CLI, gateway, TUI backend, cron children) re-execs into the bundle's executable at launch.
  `sys.executable` is then the bundle path, and TCC attributes privacy access to the bundle.
- Bundle missing, unreadable plist, version mismatch, or executable missing → silent fallback to the
  store Python (`~/.hermes/tools/python-X.Y.Z+…/bin/python3`). No warning is printed.
- Not macOS → `signed_python_app()` always returns `None`.
- Children's bare `python3` (0123): every command run through the terminal tool's local backend and every cron
  script gets a PATH where the bundle's `Contents/MacOS` sits directly ahead of the store Python's `bin` (only when
  the versions match). `python3 foo.py` in a Kanban worker's terminal or a cron script then runs the signed bundle
  and uses its grants, instead of the ad-hoc store binary that triggers a generic "python3.14 wants to control …"
  prompt. Directories elsewhere on PATH keep their order; the rewrite is idempotent and never duplicates an entry.
- launchd gateway job (0142): `hermes gateway install` / any plist rewrite makes the bundle's executable the job's
  first program (`<bundle>/Contents/MacOS/python3.X -I -c <wrapper> "exec <gateway argv> >> gateway.log 2>>
  gateway.error.log"`) instead of upstream's `/usr/bin/osascript -l JavaScript` wrapper. macOS treats the job's
  first process as responsible for every child, so a terminal-tool `osascript -e 'tell application "System
  Events"…'` now prompts for (and uses the grant of) Hermes Python rather than "osascript", a grant that would
  also cover every unrelated script on the Mac. Without the bundle the osascript wrapper is unchanged.
- `hermes gateway status` / restart / duplicate-gateway detection recognise a gateway whose argv[0]
  contains spaces (patch 0027). Applies on every OS where psutil supplies the cmdline (macOS,
  Windows; Linux reads `/proc/<pid>/cmdline` first and is unaffected).
- The bundle itself is **not** built by Hermes. You build it (section below). The 0017 docstring
  refers to a personal `~/.hermes/bin/hermes-python-app` build script that is not part of the patch.

## How it works

### The problem

macOS TCC (the privacy database behind System Settings → Privacy & Security) identifies the process
that holds a grant by its **code identity**:

- A loose, ad-hoc-signed binary, such as the python-build-standalone interpreter that Hermes installs
  under `~/.hermes/tools/python-3.X.Y+…/bin/`, is identified by **path plus code hash (cdhash)**.
  Every Python upgrade, reinstall or venv rebuild changes one of the two, and TCC silently drops the
  grant. The gateway then can't read `~/Library/Messages`, drive `osascript`, read Photos, and so on,
  and nothing reports why.
- An app bundle signed with a **Developer ID** is identified by its **designated requirement**
  (bundle id + Team ID). Rebuild it, upgrade the Python inside it, or copy it to another Mac signed
  with the same certificate: the requirement still matches, so the grant stays.

TCC usually treats the *responsible* process (the app that launched the chain) as the one asking. A
launchd-started gateway whose launchd wrapper is a platform binary (for example `/usr/bin/osascript`) has no
responsible app to inherit from, so TCC attributes each child's request to the binary that makes it. A child that
runs a bare `python3` from the store `bin` is therefore the ad-hoc store binary in TCC's eyes, whatever the gateway
itself runs on. Patch 0123 fixes that for children by PATH ordering.

### Patch 0017: re-exec into the signed twin

Launch path: `hermes_bootstrap.py` calls `hermes_cli.venv_sync.prepare_launch(root, argv)`, which
returns an interpreter to re-exec into (or `None` to keep running). Upstream it returns the store
Python from `resolve_store_python(root)` whenever `sys.executable` differs from it. 0017 swaps in
the bundle executable right after that lookup, so the existing `same` check (lexical compare of
`python` vs `sys.executable`) now compares against the bundle path: a process started on the store
Python re-execs once into the bundle; a process already in the bundle sees `same == True` and
doesn't loop.

`hermes_cli._launchers.signed_python_app(store_python)` returns
`~/Applications/Hermes Python.app/Contents/MacOS/<CFBundleExecutable>` when all of these are true:

1. `sys.platform == "darwin"`;
2. `Contents/Info.plist` exists and parses;
3. its `CFBundleShortVersionString` (for example `3.14.7`) appears in the store path as
   `python-3.14.7+`;
4. the executable it names exists.

In every other case it returns `None`. `venv_sync.prepare_launch()` (which picks the interpreter that
a self-managed install re-execs into) then does:

```python
python = signed_python_app(python) or python
```

Design rules:

- **Exact version match only.** When Hermes upgrades the store Python before the bundle is rebuilt,
  Hermes falls back to the store interpreter. A stale bundle never runs against dependencies built for
  a newer Python. Grants appear lost until you rebuild the bundle; they come back after the rebuild
  because the bundle identity hasn't changed.
- **Running processes re-exec; persisted launchers don't.** Launchers written to disk keep the store
  path, because the bundle is optional and can be deleted at any time.
- **The store is never modified.** The package manager hashes the store and would reinstall it if
  it changed, so the bundle is a separate copy.

### Patch 0027: `hermes gateway status` with a spaced interpreter path

`gateway/status.py::_read_process_cmdline()` built the process's command line with
`" ".join(psutil.Process(pid).cmdline())`. The bundle path contains a space (`Hermes Python.app`), so
the quote-aware tokenizer in `_gateway_command_subcommand` split it into two tokens. The command line
then didn't match `… main.py gateway run`, and status, restart and dedupe logic treated a live gateway
as not running.

The fix wraps each argv element that contains whitespace in double quotes before joining. It skips
elements that already contain a `"` (for example an inline `-c` source), so those are left
unchanged. The non-psutil `ps` fallback is unchanged.

### Patch 0123: children's bare `python3`

`hermes_cli._launchers.prefer_signed_python_on_path(path)` walks PATH; for each entry ending in `/bin` it asks
`signed_python_app(<entry>/python3)` (so the same exact-version rule applies) and, on a match, inserts the bundle's
`Contents/MacOS` directly before that entry (moving an existing later copy up instead of duplicating it). Off macOS,
or with an empty PATH, it returns PATH unchanged. `tools/environments/local.py::_scrubbed_env`, the sanitizer the
terminal tool's local backend and cron scripts both build child environments with, applies it after
`_prepend_hermes_bin_dir`.

### Patch 0142: the signed bundle owns the launchd job

Upstream wraps the gateway in `osascript` (JXA `$.system(...)`) so the job has a Local Network identity macOS
accepts (#71206). 0142 adds `launchd_responsible_python()` (= `signed_python_app(<gateway python>)`) and a
`responsible=` argument to `launchd_program_arguments`; `generate_launchd_plist` passes it. With a bundle, the
ProgramArguments become the bundle executable running a one-line Python wrapper (`_SIGNED_WRAPPER_SOURCE`):
`subprocess.Popen(['/bin/sh', '-c', sys.argv[1]]).wait()`, exiting with the child's code, or `128+N` for a
signal death (the JXA wrapper's mapping, so KeepAlive's `SuccessfulExit` semantics hold). SIGTERM keeps its
default disposition: launchd signals the job's process group, so the gateway gets exactly one SIGTERM, as under
osascript. The bundle's Info.plist must carry `NSLocalNetworkUsageDescription` for LAN access to keep working.
`gateway/status.py` returns `None` from `_gateway_command_subcommand` for any command line containing
`SIGNED_LAUNCHD_WRAPPER_MARKER`, so `stop`/`status` never take the wrapper's ps line for the gateway (its spaced
interpreter path defeats the usual `-c` token check).

## Code

### `hermes_cli/_launchers.py` (new function, after `_store_python`)

```python
def signed_python_app(store_python: Path) -> Path | None:
    """A Developer-ID-signed twin of the store Python (~/Applications/Hermes Python.app),
    used when its version matches the store's. Running processes re-exec into it;
    persisted launchers keep the store path (the bundle may vanish)."""
    if sys.platform != "darwin":
        return None
    contents = Path.home() / "Applications" / "Hermes Python.app" / "Contents"
    try:
        import plistlib

        info = plistlib.loads((contents / "Info.plist").read_bytes())
    except (OSError, ValueError):
        return None
    version = str(info.get("CFBundleShortVersionString", ""))
    exe = contents / "MacOS" / str(info.get("CFBundleExecutable", ""))
    if version and f"python-{version}+" in str(store_python) and exe.is_file():
        return exe
    return None
```

### `hermes_cli/venv_sync.py` → `prepare_launch()`

```python
    python = resolve_store_python(root)
    if python is None:
        raise RuntimeError("source update has no managed Python; run `hermes pm install`")
    from hermes_cli._launchers import signed_python_app

    python = signed_python_app(python) or python  # stable macOS TCC identity
    # ... upstream: same = normcase(abspath(python)) == normcase(abspath(sys.executable)) ...
```

Only `prepare_launch` is touched. `_prepare_borrowed_launch` (checkouts owned by another data root,
e.g. test `HERMES_HOME`s) and `publish_launchers` still use the store path.

### `gateway/status.py` → `_read_process_cmdline()` (psutil branch)

```python
        cmdline_parts = psutil.Process(pid).cmdline()
        if cmdline_parts:
            # Quote whitespace-bearing args so the quote-aware tokenizer in
            # _gateway_command_subcommand keeps them one token. Args that already
            # carry a double quote (an inline -c source) stay as-is.
            return " ".join(
                f'"{part}"' if any(c.isspace() for c in part) and '"' not in part else part
                for part in cmdline_parts
            )
```

`_gateway_command_subcommand` already tokenizes with `shlex.split(command, posix=False)` and strips
surrounding quotes per token, so no change is needed on the parsing side.

### `hermes_cli/_launchers.py` → `prefer_signed_python_on_path()` and `tools/environments/local.py` → `_scrubbed_env()` (0123)

```python
        out[path_key] = _prepend_hermes_bin_dir(fix_path(out.get(path_key, "")))
        # [daveey] bare python3 -> the Developer-ID Hermes Python.app, so macOS TCC uses its grants.
        from hermes_cli._launchers import prefer_signed_python_on_path
        out[path_key] = prefer_signed_python_on_path(out[path_key])
```

## Tests

| File | Patch | Checks |
|---|---|---|
| `tests/hermes_cli/test_signed_python_app.py` (new) | 0017 | no bundle → `None`; matching `3.14.7` bundle → bundle exe; store `3.14.8` with `3.14.7` bundle → `None`. Marked `@pytest.mark.platforms("macos")` (skipped elsewhere); monkeypatches `Path.home` to `tmp_path`. |
| `tests/hermes_cli/test_signed_python_path.py` (new) | 0123 | no bundle → PATH unchanged; matching bundle → its dir lands right before the store `bin`; idempotent and de-duplicating; version mismatch keeps the store first; `_scrubbed_env` (the terminal/cron child-env sanitizer) puts the bundle dir ahead of the store `bin`. All `@pytest.mark.platforms("macos")`. |
| `tests/hermes_cli/test_gateway_service.py` (3 new) | 0142, 0148 | with a bundle the plist's ProgramArguments are `[bundle, -I, -c, <wrapper>, "exec … >> gateway.log 2>> gateway.error.log"]` and that line is not taken for a gateway; without one the first argument stays `/usr/bin/osascript`; the wrapper (run with the test interpreter as `responsible`) keeps the child in its process group and maps exit 23 → 23 and SIGTERM → 143 (macOS only). Run Oct 19, 2026 PT on 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127, 0128, 0129, 0130, 0131, 0132, 0133, 0134, 0135, 0136, 0137, 0138, 0139, 0140, 0141, 0142, 0143, 0144, 0145, 0146, 0147, 0148: all 6 `launchd_signed or launchd_osascript` tests pass, including the SIGTERM case (exit 143 and the child's process group both checked). Fix-up 0148 makes the children `print(os.getpgrp(), flush=True)`: before it the SIGTERM child was killed before its block-buffered print reached the log file, so that case failed on an empty file. |
| `tests/gateway/test_status_cmdline_spaced_exe.py` (new) | 0027 | fake `psutil` module returns an argv whose argv[0] is `…/Hermes Python.app/…`; asserts the joined string starts with a quoted path and `looks_like_gateway_runtime_command_line()` is true. Stubs `Path.read_bytes` so the `/proc` branch is skipped on Linux. |

Run from the repo root with the repo's test runner (it locates the right interpreter; a bare
`venv/bin/python -m pytest` may lack runtime deps such as `ruamel.yaml`):

```sh
scripts/run_tests.sh tests/hermes_cli/test_signed_python_app.py tests/gateway/test_status_cmdline_spaced_exe.py \
  tests/hermes_cli/test_signed_python_path.py
```

0017/0027's tests passed on upstream/main + those two patches; `test_signed_python_path.py` passed on the full
0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127 series (Oct 17, 2026 PT). Untested: an actual TCC prompt or grant (only PATH ordering is checked), and the
cron path end to end (the test calls `_scrubbed_env` directly, which cron scripts use, rather than running a cron
job).

## Applying

Independent of every other patch in `core/patches`, and of each other (0027 is only useful once 0017
puts a spaced path in argv[0], but applies alone). Both apply cleanly on current `upstream/main`:

```sh
cd ~/.hermes/hermes-agent            # your Hermes checkout
git am -3 --empty=keep ~/code/hermes-mods/core/patches/0017-*.patch \
          ~/code/hermes-mods/core/patches/0027-*.patch
```

If re-implementing against a newer upstream: locate wherever the launcher decides which interpreter
to re-exec into (today `prepare_launch`, called from `hermes_bootstrap.py`) and wrap that value; and
make sure any psutil-joined cmdline quotes spaced args before the gateway-identity tokenizer sees it.

0123 and 0142 need 0017 (`signed_python_app`). 0142 also needs upstream's osascript launchd wrapper
(`launchd_program_arguments`, #71206). It takes effect only once the launchd plist is regenerated (reinstall the
gateway service); then grant Accessibility/Automation to Hermes Python once. Verified: 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127, 0128, 0129, 0130, 0131, 0132, 0133, 0134, 0135, 0136, 0137, 0138, 0139, 0140, 0141, 0142, 0143 apply with
`git am -3 --empty=keep` at merge-base `4957bbd0a0` (Oct 19, 2026 PT).

## Gotchas

- **Re-exec only happens through `prepare_launch`.** A process started directly on the store Python
  without going through `hermes_bootstrap.py` stays on the store binary. Restart the gateway after
  building the bundle; an already-running gateway keeps its old interpreter.
- **Version match is a substring test** on the store path (`python-3.14.7+`). It relies on the store
  directory naming `python-<X.Y.Z>+<build>-<platform>`. If upstream renames the store layout the
  bundle silently stops being used.
- **Silent fallback.** Nothing logs that the bundle was skipped. Check with
  `ps -o command= -p <gateway pid>` (see step 6) after every Python upgrade.
- **Sealed bundle vs runtime writes.** Python writes `.pyc` next to sources by default; a write inside
  `Contents/lib` invalidates the signature. Pre-compile and make `lib/` read-only (steps 2 and 5).
- **Spaces in argv are only fixed on the psutil path.** Without psutil, `ps -o command=` output is used
  and is not quoted; the gateway will again read as stopped.
- **`git am` and `hermes update`.** These are local commits on top of upstream; `hermes update` must
  rebase them. Both touch small regions (`_launchers.py` append, one line in `prepare_launch`, one
  return in `_read_process_cmdline`), so conflicts are rare but possible in `prepare_launch`, which
  upstream edits often.
- **0123 only covers PATH lookups.** A script that names the store interpreter by absolute path, or a venv's
  own `python`, still runs whatever that path points at. Names resolve only if the bundle's `Contents/MacOS` has
  them (step 1 links `python3` and `python`).
- **0142 needs `NSLocalNetworkUsageDescription` in the bundle's Info.plist** (step 3 lists it among the optional
  keys; for the launchd job it is required), or the gateway loses LAN access (Home Assistant, local devices)
  once the bundle owns the job. Only verified live on macOS 26.6.
- **0142 and terminal launches.** Only the launchd job changes. A gateway started from a terminal still has the
  terminal as its responsible process, so prompts name the terminal app.
- **Desktop app signing is separate.** `APPLE_SIGNING_IDENTITY` for `hermes desktop --build-only`
  signs the Electron app, not this Python bundle; TCC grants for one don't cover the other.

## Could this be a plugin instead?

No. The interpreter choice happens in the bootstrap before any plugin loads, and the cmdline fix is
inside `gateway/status.py`'s process-identity logic. A wrapper script that execs the bundle directly
would work for a launchd job, but `prepare_launch` would then re-exec back to the store Python, so a
core change is required.

## Build your own signed Python.app

The patches only need a bundle at `~/Applications/Hermes Python.app` whose `Info.plist` carries
`CFBundleShortVersionString` and `CFBundleExecutable`. The steps below build one. Replace the
placeholders with your own values:

- `TEAMID`: your 10-character Apple Developer Team ID.
- `com.example.hermes-python`: replace with any reverse-DNS bundle id you control. Keep it stable
  forever, because TCC grants are tied to it.
- `IDENTITY`: the SHA-1 or the full name of your certificate, e.g.
  `Developer ID Application: Your Name (TEAMID)`.

**Prerequisites:** a paid Apple Developer account and a *Developer ID Application* certificate in your
login keychain. Check that the certificate is installed:

```sh
security find-identity -v -p codesigning | grep "Developer ID Application"
```

### 1. Lay out the bundle from the store Python

```sh
SRC=$(ls -d ~/.hermes/tools/python-3.*-darwin-arm64 | sort -V | tail -1)   # newest store Python
VER=$("$SRC/bin/python3" -I -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')
MM=${VER%.*}                                                                 # e.g. 3.14
APP="$HOME/Applications/Hermes Python.app"; STAGE="$APP.staging"
rm -rf "$STAGE"; mkdir -p "$STAGE/Contents/MacOS"

cp -p "$SRC/bin/python$MM" "$STAGE/Contents/MacOS/python$MM"       # the real Mach-O binary, not a shim
ln -s "python$MM" "$STAGE/Contents/MacOS/python3"
ln -s "python$MM" "$STAGE/Contents/MacOS/python"
cp -R "$SRC/lib" "$SRC/include" "$STAGE/Contents/"                 # stdlib + headers beside MacOS/
```

Python finds its prefix relative to the executable, so `Contents/` acts as `sys.base_prefix`. Check
this later with `"$APP/Contents/MacOS/python3" -c 'import sys;print(sys.base_prefix)'`.

### 2. Byte-compile before sealing

Any `.pyc` that Python writes into the bundle after signing breaks the signature, so compile
everything now:

```sh
"$STAGE/Contents/MacOS/python$MM" -I -m compileall -q -j0 \
  -x '(lib2to3|test|idlelib)/' "$STAGE/Contents/lib/python$MM"
```

### 3. Info.plist

```sh
/usr/bin/python3 - "$STAGE/Contents/Info.plist" "$VER" "$MM" <<'EOF'
import plistlib, sys
path, ver, mm = sys.argv[1:]
plistlib.dump({
    "CFBundleIdentifier": "com.example.hermes-python",
    "CFBundleName": "Hermes Python",
    "CFBundleExecutable": f"python{mm}",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": ver,      # patch 0017 matches this against the store path
    "CFBundleVersion": ver,
    "LSUIElement": True,                    # no Dock icon
    # Usage strings: macOS shows these in permission prompts. Without the matching key,
    # some services deny the request without prompting.
    "NSAppleEventsUsageDescription": "Hermes automates apps on your behalf.",
    "NSPhotoLibraryUsageDescription": "Hermes reads your Photos library when you ask it to.",
    "NSContactsUsageDescription": "Hermes looks up contacts when you ask it to.",
    "NSCalendarsFullAccessUsageDescription": "Hermes reads and edits your calendar when you ask it to.",
    "NSRemindersFullAccessUsageDescription": "Hermes reads and edits reminders when you ask it to.",
}, open(path, "wb"))
EOF
```

Add any other `NS…UsageDescription` keys you need (microphone, camera, location, Bluetooth, local
network).

### 4. Sign inside-out, with a pinned designated requirement

```sh
IDENTITY="Developer ID Application: Your Name (TEAMID)"
REQ='anchor apple generic and identifier "com.example.hermes-python" and certificate leaf[subject.OU] = "TEAMID"'

# a) every nested Mach-O (the .so/.dylib files under lib/) first
find "$STAGE/Contents/lib" -type f \( -name '*.so' -o -name '*.dylib' \) -print0 |
  xargs -0 -n 200 codesign --force --timestamp=none --sign "$IDENTITY"

# b) then the bundle itself, with an explicit identifier + designated requirement
codesign --force --timestamp=none --sign "$IDENTITY" \
  --identifier "com.example.hermes-python" --requirements "=designated => $REQ" "$STAGE"

codesign --verify --deep --strict -R="$REQ" "$STAGE"
```

Notes:

- **Don't use the hardened runtime** (`--options runtime`). Its library validation refuses to load
  third-party extension modules from your venv, which aren't signed by your team.
- The explicit designated requirement (bundle id + Team ID, with no cdhash) is what keeps grants
  stable across rebuilds.
- `--timestamp=none` is enough for local use. Use `--timestamp` and notarize only if you distribute
  the bundle.

### 5. Seal, smoke-test, swap into place

```sh
"$STAGE/Contents/MacOS/python$MM" -c 'import ssl, sqlite3, ctypes; print("ok")'
codesign --verify --deep --strict -R="$REQ" "$STAGE"        # must still verify after a run
chmod -R a-w "$STAGE/Contents/lib"                          # stray imports can't write .pyc into the seal
rm -rf "$APP.old"; [ -e "$APP" ] && mv "$APP" "$APP.old"; mv "$STAGE" "$APP"; rm -rf "$APP.old"
```

### 6. Grant permissions once, then restart Hermes

In System Settings → Privacy & Security, add `~/Applications/Hermes Python.app` under Full Disk Access
and Accessibility (use **+**, then ⌘⇧G to type the path). Photos, Contacts and Automation prompt on
first use. Then restart the gateway (`hermes gateway restart`, or the launchd job) so it re-execs into
the bundle. Confirm it worked:

```sh
ps -o command= -p "$(pgrep -f 'main.py gateway run' | head -1)"   # should start with ~/Applications/Hermes Python.app/…
hermes gateway status                                             # should show running (patch 0027)
```

### Optional: point a dev venv at the bundle

A source checkout's `.venv` can use the bundle too. Symlink `.venv/bin/python`, `python3` and
`python$MM` to `$APP/Contents/MacOS/python$MM`, and set `home = $APP/Contents/MacOS` in
`.venv/pyvenv.cfg`. Only do this when the venv's `version` has the same major.minor.

### After a Python upgrade

When Hermes installs a new store Python, patch 0017 stops using the bundle (the versions no longer
match) until you rebuild it. Repeat steps 1–5 with the same `com.example.hermes-python` and certificate. The
existing grants carry over and you don't need to re-approve anything. Script the steps and run them
from a periodic job so the bundle stays current.

## Troubleshooting

- **Grants still missing after the rebuild:** check that the designated requirement matches what TCC
  stored. `codesign -d -r- "$APP"` must print the bundle-id + team requirement, with no `cdhash`.
- **`codesign --verify` fails after the bundle has been used:** something wrote into the bundle,
  usually a `.pyc`. Rebuild, and keep `lib/` read-only.
- **`ImportError: … not valid for use in process … library validation`:** the bundle was signed
  with the hardened runtime. Re-sign without `--options runtime`.
- **Gateway shows as stopped although it's running:** patch 0027 is missing, or psutil isn't
  installed and the `ps` fallback is in use.
