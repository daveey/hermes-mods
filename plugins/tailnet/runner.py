"""Runs under ``token-broker exec``: the broker puts TAILNET_SSH_KEY in this process's environment.

Reads a credential-free job (JSON on stdin), writes the key to a 0600 file in a private tmpfs dir, runs ssh/scp
with a scrubbed environment, deletes the key, prints ONE JSON line {rc, stdout, stderr, seconds}. Output is capped
and any key material is redacted. Stdlib only.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)


def clean(text, key, cap):
    text = KEY_BLOCK.sub("[redacted key]", text or "")
    for line in (key or "").splitlines():
        if len(line) > 20:
            text = text.replace(line, "[redacted]")
    return text[:cap]


def base_opts(keyfile, known_hosts):
    return ["-i", keyfile, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + known_hosts,
            "-o", "LogLevel=ERROR", "-o", "ServerAliveInterval=15"]


def build(job, keyfile):
    t = job["target"]
    dest = f"{t['user']}@{t['addr']}"
    opts = base_opts(keyfile, job["known_hosts"])
    if job.get("via"):
        v = job["via"]
        hop = " ".join(["ssh"] + base_opts(keyfile, job["known_hosts"]) + ["-W", "%h:%p", f"{v['user']}@{v['addr']}"])
        opts += ["-o", "ProxyCommand=" + hop]
    mode = job["mode"]
    if mode == "exec":
        return ["ssh"] + opts + [dest, job["command"]]
    if mode == "put":
        return ["scp", "-q", "-p"] + opts + [job["local"], f"{dest}:{job['remote']}"]
    if mode == "get":
        return ["scp", "-q", "-p"] + opts + [f"{dest}:{job['remote']}", job["local"]]
    raise ValueError("bad mode")


def main():
    job = json.load(sys.stdin)
    key = os.environ.pop("TAILNET_SSH_KEY", "")
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
            p = subprocess.run(build(job, kf), capture_output=True, text=True, errors="replace",
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
