"""Daily cross-gateway Inbox junk audit.

Strict acceptance counts ANY untouched archive, test/status card or poster absent from
shipped posters.yaml. This does not change admission's consecutive-archive mute rule.
Creation and archive-decision observation bounds and missing timestamps are explicit.
Fresh archive evidence uses resolved_at, never creation/update time. One stable family
owner is filed for fresh findings; failed filing makes the audit exit nonzero.

Run: ``python3 junk_audit.py [--json FILE] [--dry-run]`` (existing daily hub cron).
Dry-run reads the retained snapshot and prints findings without any writes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

WINDOW_S = 7 * 86400
FRESH_S = 86400
_STORE = Path(__file__).resolve().parent / "store.py"


def _store():
    spec = importlib.util.spec_from_file_location("_inbox_store_audit", _STORE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sender(item, st) -> str:
    poster = item.get("poster") or st.key_poster(item.get("key")) or "(none)"
    return st.poster_group(poster) or poster


def _untouched(item) -> bool:
    return item.get("state") == "archived" and (item.get("outcome") in (None, "archived"))


KINDS = ("senders", "reposted_keys", "test_cards", "strict_findings")


def strict_audit(items, now, st):
    """Count the acceptance contract, not the operational muting threshold.

    Creation bounds apply to content/registry checks; archive decisions can bring older
    cards into scope. Missing decision timestamps remain visible, never guessed fresh.
    The cross-gateway snapshot supplies no remote registry extras or full body text.
    """
    spec = importlib.util.spec_from_file_location("_inbox_ask_audit", _STORE.with_name("ask_contract.py"))
    assert spec is not None and spec.loader is not None
    ac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ac)
    registry = json.loads(_STORE.with_name("posters.yaml").read_text())
    counts = dict(archived_untouched=0, test_cards=0, status_cards=0, unregistered_posters=0, junk_items=0)
    findings, unknown = [], 0
    start = now - WINDOW_S
    for i in items:
        created = float(i.get("created_at") or 0)
        decided = float(i.get("resolved_at") or 0)
        created_in_window = start <= created <= now
        archived = _untouched(i)
        if archived and not decided:
            unknown += 1
        if not (created_in_window or (archived and start <= decided <= now)):
            continue
        reasons, times = [], []
        if archived:
            reasons.append("archived_untouched")
            times.append(decided)  # updated_at may be a replica refresh, not an archive
        if created_in_window:
            title, key = i.get("title") or "", i.get("key")
            if st.junk_reason(title, key) or ac.is_test(title) or ac.is_test(str(key or "")):
                reasons.append("test_cards")
                times.append(created)
            row = registry.get(ac.family(i.get("poster")))
            if not row:
                reasons.append("unregistered_posters")
                times.append(created)
            if ac.status_reason(row or {}, i.get("actions") or [], title, i.get("body") or ""):
                reasons.append("status_cards")
                times.append(created)
        if reasons:
            for reason in reasons:
                counts[reason] += 1
            counts["junk_items"] += 1
            findings.append({k: i.get(k) for k in ("gw", "session_id", "key", "poster", "title")} | {
                "family": ac.family(i.get("poster")) if i.get("poster") else _family_of("test_cards", i),
                "reasons": reasons, "created_at": created, "resolved_at": decided or None,
                "fresh": any(now - FRESH_S <= t <= now for t in times if t),
            })
    return {"strict_counts": counts, "strict_findings": findings, "observation": {
        "observed_at": now, "creation_since": start, "decision_since": start,
        "fresh_since": now - FRESH_S, "input_items": len(items),
        "archive_time_unknown": unknown,
        "registry_scope": "shipped posters.yaml; remote extras not observed",
        "content_scope": "snapshot titles/keys/actions; body only when supplied",
        "coverage": "retained supplied snapshot only; not proof of complete gateway history",
    }}


def audit(items: list[dict], now: float) -> dict:
    """Findings over the last WINDOW_S. Each finding lists its cards; ``fresh`` = has a card from the last FRESH_S."""
    st = _store()
    recent = [i for i in items if float(i.get("created_at") or 0) >= now - WINDOW_S]
    fresh = lambda cards: any(float(c.get("created_at") or 0) >= now - FRESH_S for c in cards)  # noqa: E731
    out = {"window_items": len(recent), "senders": [], "reposted_keys": [], "test_cards": []}

    # Same rule as the store's noise breaker (Store._ignored_streak): the NEWEST cards of a sender are archived
    # untouched in a row. A card David acted on (accepted / resolved) ends the streak, so a sender whose drafts
    # he mostly uses is not flagged for two scattered archives.
    by_sender_all = defaultdict(list)
    for i in recent:
        if i.get("poster"):  # key-family senders are judged per key (reposted_keys)
            by_sender_all[(i.get("gw"), _sender(i, st))].append(i)
    by_sender = {}
    for k, cards in by_sender_all.items():
        streak = []
        for c in sorted(cards, key=lambda c: float(c.get("created_at") or 0), reverse=True):
            if c.get("state") == "open":
                continue
            if not _untouched(c):
                break
            streak.append(c)
        by_sender[k] = list(reversed(streak))
    for (gw, sender), cards in sorted(by_sender.items()):
        if len(cards) >= st.MUTE_AFTER:
            out["senders"].append({"gw": gw, "sender": sender, "archived_untouched": len(cards),
                                   "fresh": any(now - FRESH_S <= float(c.get("resolved_at") or 0) <= now for c in cards),
                                   "titles": [c.get("title") for c in cards][:5]})

    by_key = defaultdict(list)
    for i in recent:
        if i.get("key"):
            by_key[(i.get("gw"), i["key"])].append(i)
    for (gw, key), cards in sorted(by_key.items()):
        cards.sort(key=lambda c: float(c.get("created_at") or 0))
        reposts = [b for a, b in zip(cards, cards[1:]) if _untouched(a)]
        if reposts:
            out["reposted_keys"].append({"gw": gw, "key": key, "reposts": len(reposts), "fresh": fresh(reposts),
                                         "title": cards[-1].get("title"), "poster": cards[-1].get("poster")})

    for i in recent:
        why = st.junk_reason(i.get("title") or "", i.get("key"))
        if why:
            out["test_cards"].append({"gw": i.get("gw"), "key": i.get("key"), "title": i.get("title"),
                                      "poster": i.get("poster"), "fresh": fresh([i])})
    out.update(strict_audit(items, now, st))
    out["fresh"] = any(f["fresh"] for k in KINDS for f in out[k])
    return out


def card_body(res: dict) -> str:
    lines = ["Daily Inbox strict audit: ANY untouched archive, test/status card, or unregistered poster is a finding.",
             "This is separate from the unchanged consecutive-archive noise breaker. An archive does not prove",
             "why a draft was rejected. Investigate the producer without suppressing legitimate publication, spend",
             "or login decisions. Preserve history; do not relabel archived publication drafts to claim zero junk.",
             "Reuse existing producer repair owners; do not duplicate their work or post to the Inbox.",
             "Bounds/gaps: " + json.dumps(res.get("observation", {}), sort_keys=True), ""]
    for f in res.get("strict_findings", []):
        lines.append(f"- strict {','.join(f['reasons'])} on {f['gw']} / {f['session_id']}: "
                     f"{f['family']} `{f['key']}` {str(f['title'])[:100]}"
                     f"{' (NEW)' if f['fresh'] else ''}; decision={f['resolved_at']}")
    for f in res["senders"]:
        lines.append(f"- sender `{f['sender']}` on {f['gw']}: {f['archived_untouched']} archived untouched"
                     f"{' (NEW)' if f['fresh'] else ''}: " + "; ".join(str(t)[:60] for t in f["titles"]))
    for f in res["reposted_keys"]:
        lines.append(f"- key `{f['key']}` on {f['gw']} re-posted {f['reposts']}x after an archive"
                     f"{' (NEW)' if f['fresh'] else ''}: {str(f['title'])[:80]}")
    for f in res["test_cards"]:
        lines.append(f"- test/probe card on {f['gw']}{' (NEW)' if f['fresh'] else ''}: `{f['key']}` {str(f['title'])[:80]}")
    return "\n".join(lines)


def file_card(res: dict, now: float, runner=subprocess.run) -> str | None:
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    argv = ["hermes", "kanban", "create", f"Inbox junk audit {day}: junk reached the Inbox",
            "--assignee", "default", "--body-file", "-", "--json", "--created-by", "inbox-junk-audit",
            "--idempotency-key", f"inbox-junk-audit:{day}", "--no-requires"]
    p = runner(argv, input=card_body(res), capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        print(f"card create failed rc={p.returncode}: {(p.stderr or p.stdout)[-300:]}", file=sys.stderr)
        return None
    try:
        return json.loads(p.stdout or "{}").get("id")
    except ValueError:
        return None


def _family_of(kind: str, f: dict) -> str:
    """Poster family a finding belongs to: a sender's normalised label, else the key family before ':'."""
    if f.get("family"):
        return f["family"]
    if kind == "senders":
        label = str(f["sender"])
    elif f.get("poster"):
        label = str(f["poster"])
    else:
        label = str(f.get("key") or "unlabelled").split(":", 1)[0]
    return "-".join(label.split("·", 1)[0].strip().lower().replace("_", " ").split()) or "unlabelled"


def by_family(res: dict) -> dict:
    out: dict = {}
    for kind in KINDS:
        for f in res.get(kind, []):
            out.setdefault(_family_of(kind, f), {k: [] for k in KINDS})[kind].append(f)
    return {fam: dict(r, observation=res.get("observation", {}),
                      fresh=any(x["fresh"] for k in KINDS for x in r[k]))
            for fam, r in out.items()}


def _file_family(fam, res, now, runner):
    # Only fresh evidence identifies an occurrence. Observation time and ageing history
    # must not produce a new notification on every retry.
    evidence = [{"kind": kind, **{k: v for k, v in f.items() if k != "fresh"}}
                for kind in KINDS for f in res[kind] if f["fresh"]]
    encoded = sorted(json.dumps(f, sort_keys=True) for f in evidence)
    markers = ["inbox-audit-evidence:" + hashlib.sha256(e.encode()).hexdigest() for e in encoded]
    body = "\n".join(markers) + "\n\n" + card_body(res)
    key = f"inbox-junk-audit:{fam}:owner"
    day = time.strftime("%Y-%m-%d", time.gmtime(now))

    def call(argv, **kw):
        p = runner(["hermes", "kanban", *argv], capture_output=True, text=True, timeout=120, **kw)
        if p.returncode != 0:
            raise RuntimeError(f"rc={p.returncode}: {(p.stderr or p.stdout)[-300:]}")
        return p.stdout

    def read(task_id):
        view = json.loads(call(["show", task_id, "--json"]))
        task = view["task"]
        if task.get("id") != task_id or not task.get("status"):
            raise ValueError("invalid task readback")
        bodies = [task.get("body") or "", *(c.get("body") or "" for c in view.get("comments", []))]
        return task, all(any(marker in b for b in bodies) for marker in markers)

    history = json.loads(call(["list", "--archived", "--json"]))
    if not isinstance(history, list):
        raise ValueError("invalid owner history response")
    # Core create dedup deliberately excludes archived rows. Inspect retained history
    # first so archiving an owner cannot make an identical finding a new incident.
    owners = {t.get("idempotency_key"): t for t in sorted(history, key=lambda t: t.get("created_at") or 0)}
    for _ in range(100):  # bounded successor chain; a corrupt chain must fail visibly
        out = owners.get(key)
        if out is None:
            out = json.loads(call(["create", f"Inbox junk audit {day}: poster family {fam}",
                                  "--assignee", "default", "--body-file", "-", "--json",
                                  "--created-by", "inbox-junk-audit", "--idempotency-key", key,
                                  "--no-requires"], input=body))
        task_id = out.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("missing task id in create response")
        task, recorded = read(task_id)
        if recorded:
            return task_id
        if task["status"] in ("done", "archived"):
            key = f"inbox-junk-audit:{fam}:owner#after:{task_id}"
            continue
        call(["comment", task_id, body])
        _, recorded = read(task_id)
        if not recorded:
            raise RuntimeError("evidence comment missing from readback")
        return task_id
    raise RuntimeError("owner successor chain exceeded 100 cards")


def file_cards(res: dict, now: float, runner=subprocess.run) -> dict:
    """One durable owner per poster family, including retries across UTC midnight.

    A filing error is a failed audit, even if other families were filed successfully.
    """
    made, errors = {}, {}
    for fam, r in sorted(by_family(res).items()):
        if not r["fresh"]:
            continue
        try:
            made[fam] = _file_family(fam, r, now, runner)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
            errors[fam] = str(exc)
    if errors:
        raise RuntimeError(f"card filing failed: {json.dumps(errors)}; filed={json.dumps(made)}")
    return made


def drain_refusals(home, creator=None) -> int:
    """File the Kanban card for refusals whose routing failed at post time (worker fence, board down).

    ``<home>/inbox/refusals.jsonl`` keeps every refusal; one with ``routing_error`` and no ``task`` was never
    filed. A cron/gateway process (not a fenced worker) retries it with the same idempotency key, then rewrites the
    log atomically with the task id. Returns how many cards were filed.
    """
    import os
    path = Path(home) / "inbox" / "refusals.jsonl"
    if not path.exists():
        return 0
    creator = creator or _store().kanban_task_creator
    rows, filed, errors = [], 0, []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            rows.append(line)
            continue
        if row.get("routing_error") and not row.get("task"):
            try:
                row["task"] = creator(
                    title=f"Inbox refused: {row.get('title')}"[:120],
                    body=f"Reason: {row.get('reason')}\n\n{row.get('title')}\n\n{row.get('body') or ''}\n\n"
                         f"Requested instruction: {row.get('accept_prompt')}",
                    assignee="default", idempotency_key=f"inbox-refused:{row.get('family')}:{row.get('key') or row.get('title')}")
                if not row["task"]:
                    raise ValueError("missing task id from refusal creator")
                row.pop("routing_error", None)
                filed += 1
            except Exception as exc:  # noqa: BLE001 - leave it for the next run
                row["routing_error"] = f"{type(exc).__name__}: {exc}"
                errors.append(row["routing_error"])
        rows.append(row)
    if filed:
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(r if isinstance(r, str) else json.dumps(r, ensure_ascii=True) for r in rows) + "\n")
        os.replace(tmp, path)
    if errors:
        raise RuntimeError(f"refusal drain failed ({len(errors)}); filed={filed}: {errors}")
    return filed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="read items from this file instead of `hermes-hub inbox --all --json`")
    ap.add_argument("--dry-run", action="store_true", help="print findings, file no card")
    a = ap.parse_args(argv)
    if a.json:
        items = json.loads(Path(a.json).read_text())
    else:
        p = subprocess.run(["hermes-hub", "inbox", "--all", "--json"], capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            print(f"hermes-hub inbox failed rc={p.returncode}: {p.stderr[-300:]}", file=sys.stderr)
            return 1
        items = json.loads(p.stdout)
    now = time.time()
    drained, drain_error = 0, None
    if not a.dry_run:
        try:
            drained = drain_refusals(_store()._home())
        except (RuntimeError, OSError) as exc:
            drain_error = str(exc)
            print(drain_error, file=sys.stderr)
    res = audit(items, now)
    cards, filing_error = {}, None
    try:
        if res["fresh"] and not a.dry_run:
            cards = file_cards(res, now)
    except RuntimeError as exc:
        filing_error = str(exc)
        print(filing_error, file=sys.stderr)
    print(json.dumps({"items_7d": res["window_items"], "senders": len(res["senders"]),
                      "reposted_keys": len(res["reposted_keys"]), "test_cards": len(res["test_cards"]),
                      "strict_counts": res["strict_counts"], "strict_findings": res["strict_findings"],
                      "observation": res["observation"], "filing_error": filing_error,
                      "fresh": res["fresh"], "cards": cards, "refusals_drained": drained, "drain_error": drain_error}))
    if a.dry_run:
        print(card_body(res))
    return 1 if filing_error or drain_error else 0


if __name__ == "__main__":
    sys.exit(main())
