"""Import default-board self-heal digest comments into the ranked pending backlog.

Called later from the self_heal CLI. Receives the ``self_heal`` module and a store
plus inventory/prune **paths or dicts** (dicts keep tests off private hub dumps).

Frozen rules:
- evolve ``pending`` in place (fp, payload, retry, version); existing keys stay
- LaneState is the sole lifecycle store
- never queue digest history as recurrence for resolved/fixed/suppressed fps
- exclude manually pruned raw **and** canonical fingerprints
- fail closed when prune/inventory provenance is missing
- dry-run is read-only: never ``_pending_db`` (that CREATE TABLE writes)
- apply: consistent ``.bak`` then one transaction including provenance ids
  (fold truncates evidence, so comment/block ids are the idempotency key)
- missing occurrence fields → None; missing p_defect → 0.5
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path

DIGEST_KEY_RE = re.compile(r"^selfheal-digest:2026-11-(?:13|14|15|16|17|18|19)(?::[0-9]+)?$")
# Same recovery pattern as self_heal._digest_fingerprints.
DIGEST_FP_RE = re.compile(r"(?m)^- fingerprint: ([a-z0-9_-]+)(?: ·|$)")
SESSION_RE = re.compile(r"^@session:([^/]+)/(.+)$")
PROVENANCE_TABLE = "digest_migration_provenance"
PENDING_NAME = "selfheal_pending.sqlite"
RESOLVED_STATUSES = frozenset(
    {"fixed", "suppressed", "not_defect", "declined", "reverted", "stale"}
)
PLACEHOLDERS = frozenset({"?", "none"})


class DigestMigrationError(ValueError):
    """Fail-closed migration: missing provenance or unusable comment identity."""


def migrate_digest_backlog(sh, store, inventory=None, prunes=None, dry_run=True, now=None,
                           board="default", expected_prune_count=None):
    """Import digest comments into ``store``'s pending sqlite.

    ``sh``: the ``self_heal`` module (fold/LaneState/_ro_state/canonicalize).
    ``inventory`` / ``prunes``: dict (``{cards: [...]}`` / ``{rows: [...]}``),
    a JSON path, or (inventory only) ``"board"`` to read the default board via
    ``sh._ro_state``. Missing prune provenance raises ``DigestMigrationError``.
    ``dry_run=True`` (default) plans only: no pending CREATE, no ``.bak``, no lane writes.
    """
    now = sh.time.time() if now is None else float(now)
    prune_rows = _load_prunes(prunes)
    if expected_prune_count is not None:
        if (len(prune_rows) != expected_prune_count
                or any("manual backlog prune" not in str(r.get("why") or "").lower()
                       or not r.get("ts") for r in prune_rows)):
            raise DigestMigrationError("incomplete manual-prune provenance")
    cards = _load_inventory(sh, inventory, board)
    pruned = _pruned_forms(sh, [row for row in prune_rows if row.get("symptom") and row.get("component")])
    pruned.update(row["fingerprint"] for row in prune_rows)
    # Prune log records intentionally omit symptom/component. Recover aliases
    # from every matching historical block BEFORE considering any imports.
    changed = True
    while changed:
        before = set(pruned)
        for card in cards:
            for comment in card.get("comments") or []:
                for detection in parse_digest_comment_blocks(sh, comment.get("body") or ""):
                    forms = _fp_forms(sh, detection)
                    if forms & pruned:
                        pruned.update(forms)
        changed = pruned != before
    pending = _read_pending(sh, store)
    pending_snapshot = json.loads(json.dumps(pending))
    provenance = _read_provenance(sh, store)
    existing_keys = list(pending)
    lane = sh.LaneState(store.root)
    lane_map = _read_lane(lane)

    report = {
        "dry_run": bool(dry_run),
        "cards": len(cards),
        "comments_seen": 0,
        "queued": [],
        "folded": [],
        "pruned": [],
        "history_retained": [],
        "excluded_resolved": [],
        "skipped_comments": 0,
        "preserved_pending_keys": list(existing_keys),
        "imported_comment_ids": [],
        "bak": None,
        "error": None,
    }
    writes = []  # pending mutations
    provenance_rows = []
    history = []  # (lane_key, detection)

    for card in cards:
        card_id = card.get("id")
        for comment in card.get("comments") or []:
            report["comments_seen"] += 1
            cid = comment.get("id")
            if cid is None:
                raise DigestMigrationError("digest comment missing id (fail closed)")
            body = comment.get("body") or ""
            blocks = parse_digest_comment_blocks(sh, body)
            if not blocks:
                report["skipped_comments"] += 1
                continue
            for index, detection in enumerate(blocks):
                block_id = f"{card_id}:{cid}:{index}"
                if ("block", block_id) in provenance or ("comment", str(cid)) in provenance:
                    continue
                fp = detection["fingerprint"]
                detection = {
                    **detection,
                    "digest_card": card_id,
                    "digest_key": card.get("key"),
                    "digest_comment_id": cid,
                    "digest_block_id": block_id,
                    "board": board,
                }
                forms = _fp_forms(sh, detection)
                if detection.get("component") and detection.get("symptom"):
                    detection = sh.canonicalize(detection)
                disposition = None
                if forms & pruned:
                    disposition = "pruned"
                    report["pruned"].append(fp)
                else:
                    lane_key, st = _lane_hit(lane_map, forms)
                    if st and st.get("status") in RESOLVED_STATUSES:
                        if lane_key:
                            disposition = "history"
                            history.append((lane_key, detection))
                            report["history_retained"].append(fp)
                        else:
                            disposition = "excluded_resolved"
                            report["excluded_resolved"].append(fp)
                    else:
                        target = _pending_key(pending, forms)
                        if target is not None:
                            old = pending[target]["payload"]
                            merged = sh.fold_payloads(old, detection)
                            merged["fingerprint"] = target
                            pending[target] = {
                                "payload": merged,
                                "retry": min(pending[target]["retry"], now),
                                "version": uuid.uuid4().hex,
                            }
                            writes.append(("update", target, pending[target]))
                            disposition = "folded"
                            report["folded"].append(fp)
                        else:
                            row = {
                                "payload": detection,
                                "retry": now,
                                "version": uuid.uuid4().hex,
                            }
                            target = detection["fingerprint"]
                            pending[target] = row
                            writes.append(("insert", target, row))
                            disposition = "queued"
                            report["queued"].append(fp)
                provenance_rows.append(
                    ("block", block_id, fp, disposition, card_id, cid, now)
                )
                report["imported_comment_ids"].append(cid)

    report["preserved_pending_keys"] = [k for k in existing_keys if k in pending]
    if dry_run:
        return report

    mutated = bool(writes or provenance_rows or history)
    if not mutated:
        return report

    # Same lock order as backlog_control: admission -> pending -> lane.
    # Planning is read-only; revalidate the whole snapshot (including inserts and
    # same-version classification updates), not just the rows we intend to touch.
    with sh._admission_lock(store):
        db = sh._pending_db(store)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                with lane._locked():
                    if (_read_pending(sh, store) != pending_snapshot
                            or _read_provenance(sh, store) != provenance
                            or _read_lane(lane) != lane_map):
                        raise DigestMigrationError("migration snapshot changed; retry dry-run")
                    path = Path(store.root) / PENDING_NAME
                    bak = path.with_suffix(".sqlite.bak")
                    if not bak.exists():
                        _sqlite_backup(path, bak)
                    report["bak"] = str(bak)
                    db.execute(
                        f"CREATE TABLE IF NOT EXISTS {PROVENANCE_TABLE} ("
                        "source_kind TEXT NOT NULL, source_id TEXT NOT NULL, "
                        "fingerprint TEXT, disposition TEXT, card_id TEXT, comment_id TEXT, "
                        "imported_at REAL NOT NULL, PRIMARY KEY (source_kind, source_id))"
                    )
                    # Write evidence BEFORE recording import completion. A crash
                    # rolls back SQLite; retry unions the same occurrence hashes
                    # into lane evidence without changing lifecycle or count.
                    for lane_key, detection in history:
                        old = lane.read()[lane_key].get("backlog_evidence")
                        evidence = sh.fold_payloads(old, detection) if old else dict(detection)
                        evidence["fingerprint"] = lane_key
                        evidence["backlog_stats"] = sh._backlog_stats(evidence)
                        lane._apply(lane_key, {"backlog_evidence": evidence})
                    for op, fp, row in writes:
                        payload = json.dumps(row["payload"])
                        if op == "insert":
                            db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)",
                                       (fp, payload, row["retry"], row["version"]))
                        else:
                            db.execute("UPDATE pending SET payload=?, retry=?, version=? WHERE fp=?",
                                       (payload, row["retry"], row["version"], fp))
                    db.executemany(
                        f"INSERT INTO {PROVENANCE_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?)",
                        provenance_rows,
                    )
        finally:
            db.close()
    report["preserved_pending_keys"] = existing_keys
    return report


def parse_digest_comment_blocks(sh, body: str) -> list:
    """Parse ``_occurrence_text`` blocks; skip schedule/completion comments.

    Uses ``_digest_fingerprints``'s fingerprint line. Missing fields are None;
    p_defect is 0.5. Several ``- fingerprint:`` lines in one comment are blocks.
    """
    text = (body or "").replace("\r\n", "\n")
    matches = list(DIGEST_FP_RE.finditer(text))
    if not matches:
        return []
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end]
        blocks.append(_parse_occurrence_chunk(sh, chunk, m.group(1)))
    return blocks


def _parse_occurrence_chunk(sh, chunk: str, fp: str) -> dict:
    fields = {
        "fingerprint": fp,
        "component": None,
        "source": None,
        "host": None,
        "session_id": None,
        "profile": None,
        "symptom": None,
        "quote": None,
        "p_defect": 0.5,
        "at": None,
        "kind": None,
    }
    lines = chunk.split("\n")
    evidence_lines = []
    in_evidence = False
    for line in lines:
        if in_evidence:
            evidence_lines.append(line)
            continue
        if line.startswith("- fingerprint:"):
            rest = line[len("- fingerprint:"):].strip()
            # "{fp} · component {c} · detector {s}"
            parts = rest.split(" · ")
            for part in parts[1:]:
                if part.startswith("component "):
                    fields["component"] = _missing(part[len("component "):])
                elif part.startswith("detector "):
                    fields["source"] = _missing(part[len("detector "):])
        elif line.startswith("- when:"):
            rest = line[len("- when:"):].strip()
            when, _, host = rest.partition(" · host ")
            fields["at"] = sh._detected_at({"at": when.strip()}) if _missing(when) else None
            fields["host"] = _missing(host)
        elif line.startswith("- session:"):
            raw = line[len("- session:"):].strip()
            profile, sid = _session(raw)
            fields["profile"], fields["session_id"] = profile, sid
        elif line.startswith("- symptom:"):
            fields["symptom"] = _missing(line[len("- symptom:"):].strip())
        elif line.startswith("- evidence:"):
            in_evidence = True
            evidence_lines.append(line[len("- evidence:"):].lstrip())
    if evidence_lines:
        ev = "\n".join(evidence_lines).strip()
        fields["quote"] = ev or None
    return fields


def _missing(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in PLACEHOLDERS:
        return None
    return text


def _session(raw: str):
    text = _missing(raw)
    if text is None:
        return None, None
    m = SESSION_RE.match(text)
    if m:
        return m.group(1) or None, m.group(2) or None
    return None, text


def _load_json(value, what: str):
    if value is None:
        raise DigestMigrationError(f"missing {what}")
    if isinstance(value, (str, Path)):
        path = Path(value)
        if not path.is_file():
            raise DigestMigrationError(f"missing {what}")
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise DigestMigrationError(f"missing {what}") from exc
    return value


def _load_prunes(prunes) -> list:
    data = _load_json(prunes, "prune provenance")
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and "rows" in data:
        rows = data["rows"]
    else:
        raise DigestMigrationError("missing prune provenance")
    if not isinstance(rows, list):
        raise DigestMigrationError("missing prune provenance")
    for row in rows:
        if not (row or {}).get("fingerprint"):
            raise DigestMigrationError("missing prune provenance")
    return rows


def _load_inventory(sh, inventory, board: str) -> list:
    if inventory == "board":
        return _inventory_from_board(sh, board)
    data = _load_json(inventory, "digest inventory")
    if isinstance(data, list):
        cards = data
    elif isinstance(data, dict) and "cards" in data:
        cards = data["cards"]
    else:
        raise DigestMigrationError("missing digest inventory")
    if not isinstance(cards, list):
        raise DigestMigrationError("missing digest inventory")
    return [c for c in cards if DIGEST_KEY_RE.fullmatch(str((c or {}).get("key") or ""))]


def _inventory_from_board(sh, board: str) -> list:
    path = sh.board_db_path(board)
    if path is None or not Path(path).exists():
        raise DigestMigrationError("missing digest inventory")
    con = sh._ro_state(path)
    try:
        cards = []
        for tid, key, status in con.execute(
                "SELECT id, idempotency_key, status FROM tasks "
                "WHERE idempotency_key LIKE 'selfheal-digest:%'"):
            if not DIGEST_KEY_RE.fullmatch(key or ""):
                continue
            comments = [
                {"id": r[0], "body": r[1], "created_at": r[2]}
                for r in con.execute(
                    "SELECT id, body, created_at FROM task_comments WHERE task_id=? ORDER BY id",
                    (tid,),
                )
            ]
            cards.append({"id": tid, "key": key, "status": status, "comments": comments})
        return cards
    finally:
        con.close()


def _pruned_forms(sh, rows) -> set:
    out = set()
    for row in rows:
        fp = row["fingerprint"]
        out.add(fp)
        stub = {
            "fingerprint": fp,
            "component": row.get("component") or "",
            "symptom": row.get("symptom") or "",
            "source": row.get("source") or "",
        }
        out |= _fp_forms(sh, stub)
    return out


def _fp_forms(sh, d: dict) -> set:
    forms = {x for x in (d.get("fingerprint"), d.get("raw_fingerprint"), d.get("canon_fingerprint")) if x}
    try:
        c = sh.canonicalize(dict(d))
    except Exception:
        return forms
    forms.update(x for x in (c.get("fingerprint"), c.get("raw_fingerprint"), c.get("canon_fingerprint")) if x)
    return forms


def _lane_hit(lane_map: dict, forms: set):
    for key in forms:
        if key in lane_map:
            return key, lane_map[key]
    return None, None


def _pending_key(pending: dict, forms: set):
    for key, row in pending.items():
        payload = row["payload"] if isinstance(row, dict) and "payload" in row else row
        if not isinstance(payload, dict):
            payload = {}
        hit = {key, payload.get("fingerprint"), payload.get("raw_fingerprint"),
               payload.get("canon_fingerprint")}
        if forms & {x for x in hit if x}:
            return key
    return None


def _read_lane(lane) -> dict:
    try:
        data = json.loads(lane.path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise DigestMigrationError("unreadable lane lifecycle") from exc
    if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
        raise DigestMigrationError("invalid lane lifecycle")
    return data


def _read_pending(sh, store) -> dict:
    path = Path(store.root) / PENDING_NAME
    if not path.exists():
        return {}
    con = sh._ro_state(path)
    try:
        if not con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending'").fetchone():
            return {}
        out = {}
        for fp, payload, retry, version in con.execute(
                "SELECT fp, payload, retry, version FROM pending"):
            try:
                data = json.loads(payload)
            except ValueError as exc:
                raise DigestMigrationError(f"invalid pending payload: {fp}") from exc
            if not isinstance(data, dict):
                raise DigestMigrationError(f"invalid pending payload: {fp}")
            out[fp] = {"payload": data, "retry": retry, "version": version}
        return out
    finally:
        con.close()


def _read_provenance(sh, store) -> set:
    path = Path(store.root) / PENDING_NAME
    if not path.exists():
        return set()
    con = sh._ro_state(path)
    try:
        if not con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (PROVENANCE_TABLE,)).fetchone():
            return set()
        return {(kind, sid) for kind, sid in con.execute(
            f"SELECT source_kind, source_id FROM {PROVENANCE_TABLE}")}
    finally:
        con.close()


def _sqlite_backup(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(src)
    try:
        d = sqlite3.connect(dest)
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()
