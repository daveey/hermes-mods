"""Runs under ``token-broker exec``: the broker puts TAILNET_SSH_KEY in this process's environment.

Reads a credential-free job (JSON on stdin), writes the key to a 0600 file in a private tmpfs dir, runs ssh/scp
with a scrubbed environment, deletes the key, prints ONE JSON line {rc, stdout, stderr, seconds}. Output is capped
and any key material is redacted. Stdlib only.
"""
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)


def clean(text, key, cap):
    text = KEY_BLOCK.sub("[redacted key]", text or "")
    for line in (key or "").splitlines():
        if len(line) > 20:
            text = text.replace(line, "[redacted]")
    return text[:cap]


def base_opts(keyfile, known_hosts, isolated=False):
    opts = ["-i", keyfile, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + known_hosts,
            "-o", "LogLevel=ERROR", "-o", "ServerAliveInterval=15"]
    return (["-F", "/dev/null"] if isolated else []) + opts


def build(job, keyfile, isolated=False):
    t = job["target"]
    dest = f"{t['user']}@{t['addr']}"
    opts = base_opts(keyfile, job["known_hosts"], isolated)
    if job.get("via"):
        v = job["via"]
        hop = " ".join(["ssh"] + base_opts(keyfile, job["known_hosts"], isolated) + ["-W", "%h:%p", f"{v['user']}@{v['addr']}"])
        opts += ["-o", "ProxyCommand=" + hop]
    mode = job["mode"]
    if mode == "exec":
        return ["ssh"] + opts + [dest, job["command"]]
    if mode == "put":
        return ["scp", "-q", "-p"] + opts + [job["local"], f"{dest}:{job['remote']}"]
    if mode == "get":
        return ["scp", "-q", "-p"] + opts + [f"{dest}:{job['remote']}", job["local"]]
    raise ValueError("bad mode")


def canonical_address(value):
    value = value.lower().rstrip(".")
    try:
        ip = ipaddress.ip_address(value)
        return str(getattr(ip, "ipv4_mapped", None) or ip)
    except ValueError:
        return value


def load_policy():
    # Machine-wide policy, deliberately independent of profile/job configuration.
    path = Path.home() / ".hermes" / "tailnet" / "workload-policy.json"
    try:
        raw = path.read_text()
    except FileNotFoundError:
        if not path.is_symlink():
            return None
        raise ValueError("invalid workload policy: dangling symlink")
    except OSError as exc:
        raise ValueError("invalid workload policy: cannot read file") from exc
    try:
        policy = json.loads(raw)
        if (not isinstance(policy, dict) or set(policy) != {
                "version", "protected_addresses", "protected_aliases", "routing_message"}
                or type(policy["version"]) is not int or policy["version"] != 1):
            raise ValueError("expected version 1 policy fields")
        for field in ("protected_addresses", "protected_aliases"):
            if not isinstance(policy[field], list) or any(
                    not isinstance(v, str) or not v or
                    (field == "protected_aliases" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", v))
                    for v in policy[field]):
                raise ValueError("invalid " + field)
            policy[field] = {canonical_address(v) for v in policy[field]}
        for addr in policy["protected_addresses"]:
            ipaddress.ip_address(addr)
        if not isinstance(policy["routing_message"], str) or not policy["routing_message"].strip():
            raise ValueError("routing_message must be nonempty")
        return policy
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("invalid workload policy: " + str(exc)) from exc


def resolved_target(target):
    addr = canonical_address(target["addr"])
    try:
        ipaddress.ip_address(addr)
        addresses = [addr]
    except ValueError:
        try:
            addresses = list(dict.fromkeys(canonical_address(row[4][0]) for row in
                            socket.getaddrinfo(addr, 22, type=socket.SOCK_STREAM)))
        except OSError as exc:
            raise ValueError("tailnet workload policy: cannot resolve target") from exc
        if not addresses:
            raise ValueError("tailnet workload policy: cannot resolve target")
    return addr, addresses


def workload_reason(command):
    try:
        path = Path(__file__).resolve().parent.parent / "mac-heavy-guard" / "guard.py"
        spec = importlib.util.spec_from_file_location("tailnet_heavy_classifier", path)
        if spec is None or spec.loader is None:
            raise ImportError("missing classifier loader")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        tokenize = guard._tokens

        def strict_tokens(text):
            tokens = tokenize(text)
            if tokens is None:
                raise ValueError("tailnet workload policy: cannot classify malformed shell command")
            return tokens

        setattr(guard, "_tokens", strict_tokens)
        # Do not call check_shell: that follows gateway-local scripts and honors opt-outs.
        commands = list(guard.commands(command))
        for argv, _assignments in commands:
            reason = guard.check_argv(argv)
            if reason:
                return reason
            name, args = os.path.basename(argv[0]), argv[1:]
            if name in {"rsync", "scp", "cp", "ditto", "tar", "bsdtar", "unzip", "dd",
                        "curl", "wget", "rclone", "sftp", "install"}:
                return "unbounded transfer/archive operation; use bounded tailnet_copy instead"
            if name == "xcrun":
                while args and args[0].startswith("-"):
                    args = args[2:] if args[0] in ("--sdk", "-sdk", "--toolchain", "-toolchain") else args[1:]
                if not args:
                    continue
                name, args = os.path.basename(args[0]), args[1:]
            if name == "xcodebuild":
                actions = {"build", "test", "build-for-testing", "test-without-building", "archive"}
                readonly = any(a in ("-version", "-list") or a.startswith("-show") for a in args)
                if actions.intersection(args) or not (readonly or "clean" in args):
                    return "xcodebuild build/test/archive (including default build)"
            if name == "simctl" and set(args) & {"boot", "create", "install", "bootstatus"}:
                return "simctl simulator workload"
            build_actions = {"build", "test", "b", "t"} if name == "cargo" else {"build", "test"}
            if name in ("swift", "cargo") and set(args) & build_actions:
                return name + " build/test (including debug)"
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("tailnet workload policy: classifier unavailable") from exc
    return None


def enforce_workload(job):
    policy = load_policy()
    if policy is None:
        return False
    addr = canonical_address(job["target"]["addr"])
    protected = addr in policy["protected_aliases"] or addr in policy["protected_addresses"]
    addresses = None
    if not protected:
        _, addresses = resolved_target(job["target"])
        protected = bool(set(addresses) & policy["protected_addresses"])
    if protected and job["mode"] == "exec":
        reason = workload_reason(job["command"])
        if reason:
            raise ValueError("tailnet workload policy: " + reason + "; " + policy["routing_message"])
    if protected and job["mode"] == "put":
        info = os.stat(job["local"])
        limit = int(job.get("max_bytes") or 0)
        if not stat.S_ISREG(info.st_mode) or info.st_size >= 1024**3 or info.st_size > limit:
            raise ValueError("tailnet workload policy: put size limit (regular file, below 1 GiB and "
                             "within max_bytes); " + policy["routing_message"])
    if protected and job["mode"] == "get":
        raise ValueError("tailnet workload policy: get refused: remote size is not safely bounded before "
                         "transfer; " + policy["routing_message"])
    # Pin the inspected resolution, including the jump host, and ignore SSH config.
    if addresses is None:
        _, addresses = resolved_target(job["target"])
    job["target"]["addr"] = addresses[0]
    if job.get("via"):
        _, hop_addresses = resolved_target(job["via"])
        job["via"]["addr"] = hop_addresses[0]
    return True


def main():
    job = json.load(sys.stdin)
    key = os.environ.pop("TAILNET_SSH_KEY", "")
    try:
        isolated = enforce_workload(job)
    except Exception as exc:
        msg = clean(str(exc), key, 8000)
        print(json.dumps({"rc": 126, "ran": False, "blocked": True, "stdout": "", "stderr": msg,
                          "error": msg, "seconds": 0}))
        return
    if not key.strip():
        print(json.dumps({"rc": 255, "stdout": "", "stderr": "no key vended by the broker", "seconds": 0}))
        return
    if not key.endswith("\n"):
        key += "\n"
    base = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
    d = tempfile.mkdtemp(prefix="tn-", dir=base)
    os.chmod(d, 0o700)
    kf = os.path.join(d, "k")
    cap = int(job.get("cap") or 8000) + 1  # one over, so the caller can tell it was cut
    t0 = time.time()
    try:
        fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
        os.makedirs(os.path.dirname(job["known_hosts"]), exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ("TAILNET_SSH_KEY", "SSH_AUTH_SOCK")}
        if job["mode"] == "put" and os.path.getsize(job["local"]) > int(job.get("max_bytes") or 0):
            raise ValueError("file too large")
        try:
            if job["mode"] == "put" and job.get("sha256"):
                with open(job["local"], "rb") as f:
                    if hashlib.sha256(f.read()).hexdigest() != job["sha256"]:
                        raise ValueError("staged file changed since it was requested/approved; not uploaded")
            p = subprocess.run(build(job, kf, isolated), capture_output=True, text=True, errors="replace",
                               timeout=int(job.get("timeout") or 120), env=env, stdin=subprocess.DEVNULL)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as exc:
            rc, out, err = 124, (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""), "timed out"
        except Exception as exc:
            rc, out, err = 255, "", f"{type(exc).__name__}: {exc}"
        if job["mode"] == "get" and rc == 0 and os.path.exists(job["local"]) \
                and os.path.getsize(job["local"]) > int(job.get("max_bytes") or 0):
            os.unlink(job["local"])
            rc, err = 1, "file over the size limit; discarded"
        elif job["mode"] in ("put", "get") and rc == 0:
            out = out or f"copied ({os.path.getsize(job['local'])} bytes locally)"
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(json.dumps({"rc": rc, "stdout": clean(out, key, cap), "stderr": clean(err, key, cap),
                      "seconds": round(time.time() - t0, 1)}))


if __name__ == "__main__":
    main()
