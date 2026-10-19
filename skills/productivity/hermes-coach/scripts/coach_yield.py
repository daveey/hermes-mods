"""Read-only Engine D aggregation. No source imports, network, or automatic edits.

Counts are distinct items/ideas (not events); multi-source ideas credit each source.
Dates without a zone in the research/ledger formats denote UTC days. Source age
uses observation dates, never publication dates or the current config's existence.
"""
import ast
import collections
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

UTC = timezone.utc
PT = ZoneInfo("America/Los_Angeles")
CAPS = {"scout": 3, "fixer": 7, "trial": 10}
LANES = {**CAPS, "bot": None}  # bot = the coach Bot's own chat turns: no lane cap, counts toward the $20 total
STAGES = ("scouted", "ideas", "proposed", "trialed", "applied", "kept")
URL_RX = re.compile(r"https?://[^\s<>\]\)\"']+")
REF_RX = re.compile(r"(?:reports/)?ideas\.md\s+(\d{4}-\d{2}-\d{2})\s*#(\d+)")


def timestamp(value):
    try:
        if isinstance(value, (float, int)):
            return float(value)
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d.replace(tzinfo=d.tzinfo or UTC).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def norm_url(url):
    p = urlsplit(url.rstrip(".,;"))
    host = p.netloc.lower().replace("www.twitter.com", "x.com").replace("twitter.com", "x.com")
    if host == "www.x.com":
        host = "x.com"
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k not in ("ref", "s", "t")]
    path = p.path.rstrip("/")
    # seen.json historically lowercases the entire URL, not just the host.
    return urlunsplit((p.scheme.lower(), host, path, urlencode(query), "")).lower()


def urls(value):
    return {norm_url(u) for u in URL_RX.findall(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)}


def rows(path, notes):
    if not path.exists():
        notes.add(f"Missing {path.name}; coverage is unknown.")
        return []
    result = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("not an object")
            result.append(row)
        except (ValueError, TypeError):
            notes.add(f"Malformed {path.name} line {n}; excluded.")
    return result


def source_config(directory, notes):
    """Extract only literal assignments, never execute private scripts."""
    config = {}
    for filename, name in (("coach_scout.py", "X_HANDLE_GROUPS"), ("coach_research.py", "FEEDS")):
        try:
            text = (Path(directory) / filename).read_text()
            tree = ast.parse(text)
            nodes = [n for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
            if len(nodes) != 1:
                raise ValueError("requires one literal assignment")
            node = nodes[0]
            value = ast.literal_eval(node.value)
            if not isinstance(value, list):
                raise ValueError("requires a list")
            if name == "X_HANDLE_GROUPS":
                if not all(isinstance(g, list) and all(isinstance(h, str) and re.fullmatch(r"\w{1,15}", h) for h in g) for g in value):
                    raise ValueError("invalid handle groups")
            elif not all(isinstance(f, (tuple, list)) and len(f) == 3 and isinstance(f[0], str) and isinstance(f[1], str) for f in value):
                raise ValueError("invalid feeds")
            config[name] = {"value": value, "old": ast.get_source_segment(text, node), "file": filename}
        except (OSError, SyntaxError, ValueError, TypeError) as exc:
            notes.add(f"Source config {filename} unavailable/nonliteral: {exc}; no edits for {name}.")
    return config


def classify(url, config, label="", kind="", handle=""):
    p = urlsplit(url)
    host = p.netloc
    parts = p.path.strip("/").split("/")
    feed = next((f[0] for f in config.get("FEEDS", {}).get("value", []) if f[0] == label), "")
    if not feed:
        matches = [f[0] for f in config.get("FEEDS", {}).get("value", []) if urlsplit(f[1]).netloc.lower() == host]
        feed = matches[0] if len(matches) == 1 else ""
    if host in ("x.com", "twitter.com") and parts[0] not in ("i", "search", "home"):
        return ("x", parts[0].lower(), "")
    labels = {"Hacker News": "hn", "GitHub new repos": "github", "Hermes issues/PRs": "hermes-gh", "arXiv": "arxiv", "Reddit": "reddit"}
    inferred = kind or labels.get(label) or ("feed" if feed else "release" if "/releases/" in url else
               "arxiv" if host.endswith("arxiv.org") else "hn" if host == "news.ycombinator.com" else
               "reddit" if host.endswith("reddit.com") else "github" if host == "github.com" else
               "docs" if "docs index" in label.lower() else "unknown")
    return (inferred if inferred != "unknown" else kind or "unknown", "", feed)


def source_keys(source):
    kind, handle, feed = source
    return [("source_kind", kind)] + ([("handle", handle)] if handle else []) + ([("feed", feed)] if feed else [])


def aggregate(data, now, sources_dir=None, source_target_root=None):
    data = Path(data)
    cutoff = now - 42 * 86400
    notes = set()
    config = source_config(sources_dir or Path(__file__).parent, notes)
    counts = collections.defaultdict(lambda: {s: set() for s in STAGES})
    observed = collections.defaultdict(list)
    items = {}
    item_evidence = {}

    def observe(source, t):
        if t is not None and t <= now:
            for key in source_keys(source):
                observed[key].append(t)

    def item(url, t, source):
        if t is None or t > now:
            return
        observe(source, t)
        if url not in items:
            items[url] = {"time": t, "sources": set()}
        items[url]["time"] = min(t, items[url]["time"])
        items[url]["sources"].add(source)

    for f in sorted((data / "research").glob("*.md")):
        t = timestamp(f.name[:10])
        label = ""
        lines = f.read_text().splitlines()
        for n, line in enumerate(lines):
            if line.startswith("## "):
                label = line[3:].strip()
                for feed in config.get("FEEDS", {}).get("value", []):
                    if feed[0] == label:
                        observe(("feed", "", label), t)
            # Only item headers followed by the primary URL, never body citations.
            if not re.match(r"^### .+ \(\d{4}-\d{2}-\d{2}\)", line):
                continue
            following = next((x.strip() for x in lines[n + 1:] if x.strip()), "")
            if not following.startswith(("https://", "http://")):
                continue
            us = urls(following)
            tag = re.search(r"\[([\w-]+)(?:/@([^\]]+))?\]\s*$", line)
            for u in us:
                end = next((i for i in range(n + 1, len(lines)) if lines[i].startswith("##")), len(lines))
                item_evidence[u] = "\n".join(lines[n + 2:end])
                item(u, t, classify(u, config, label, tag[1] if tag else "", tag[2] if tag else ""))
    seen_path = data / "research/state/seen.json"
    try:
        seen = json.loads(seen_path.read_text())
        if not isinstance(seen, dict):
            raise ValueError("not an object")
        for u, value in seen.items():
            t = timestamp(value.get("first_seen") if isinstance(value, dict) else value)
            u = norm_url(u)
            # Prefer section attribution (HN links may point to arbitrary domains).
            sources = items[u]["sources"] if u in items else {classify(u, config)}
            for source in list(sources):
                item(u, t, source)
    except (OSError, ValueError, TypeError):
        notes.add("Missing/malformed seen.json; scouted coverage is partial.")
    for u, record in items.items():
        if cutoff <= record["time"] <= now:
            for source in record["sources"]:
                for key in source_keys(source):
                    counts[key]["scouted"].add(u)

    ideas = {}
    for n, r in enumerate(rows(data / "reports/ideas.jsonl", notes)):
        recorded = timestamp(r.get("at") or r.get("date"))
        if recorded is not None and recorded > now:
            continue
        key = str(r.get("id") or r.get("idea_id") or (str(r.get("date")) + ":" + str(r.get("idea") or n)))
        ideas.setdefault(key, {}).update(r)
    # Resolve legacy numbered entries only by exact date + title, never row position.
    refs = collections.defaultdict(set)
    legacy = data / "reports/ideas.md"
    day = ""
    if legacy.exists():
        for line in legacy.read_text().splitlines():
            m = re.match(r"## (\d{4}-\d{2}-\d{2})", line)
            if m:
                day = m[1]
            m = re.match(r"(\d+)\. \*\*(.+?)\*\*", line)
            if m:
                for key, idea in ideas.items():
                    if idea.get("date") == day and idea.get("idea") == m[2]:
                        refs[(day, m[1])].add(key)
    by_url = collections.defaultdict(set)
    for key, idea in ideas.items():
        source_urls = urls([idea.get(k, "") for k in ("source", "url", "source_url", "source_urls")])
        idea["_urls"] = source_urls
        source_set = set()
        for u in source_urls:
            by_url[u].add(key)
            source_set.update(items[u]["sources"] if u in items else {classify(u, config, kind=idea.get("source_kind", ""))})
        if not source_set:
            source_set.add((idea.get("source_kind") or "unknown", str(idea.get("handle", "")).lstrip("@").lower() if idea.get("source_kind") == "x" else "", ""))
        idea["_sources"] = source_set
        idea["_stages"] = {"ideas"}
        status = str(idea.get("status", "")).lower()
        if status in ("proposed", "trialed", "applied", "kept"):
            idea["_stages"].add("proposed")
        if status in ("trialed", "applied", "kept"):
            idea["_stages"].add(status)
        if status in ("applied", "kept"):
            idea["_stages"].update(("applied", "kept"))

    proposals = collections.defaultdict(list)
    for r in rows(data / "reports/proposals.jsonl", notes):
        t = timestamp(r.get("at") or r.get("date"))
        if t is not None and t <= now:
            proposals[str(r.get("id", "unknown"))].append(r)
        elif t is None:
            notes.add("proposals.jsonl contains missing/invalid event dates; excluded.")
    proposal_links, proposal_parents, proposal_stages = {}, {}, {}
    for pid, events in proposals.items():
        events.sort(key=lambda r: timestamp(r.get("at") or r.get("date")) or 0)
        p = {}
        for r in events:
            p.update(r)
        ch = p.get("change") or {}
        if not isinstance(ch, dict):
            ch = {}
        linked = set()
        explicit = [p.get("idea_id"), ch.get("idea_id")]
        explicit += p.get("idea_ids", []) if isinstance(p.get("idea_ids"), list) else []
        for ident in explicit:
            if ident is not None and str(ident) in ideas:
                linked.add(str(ident))
        text = json.dumps([p.get("evidence", ""), p.get("source", ""), p.get("source_url", ""), ch.get("idea", "")])
        for ref in REF_RX.findall(text):
            if len(refs[ref]) == 1:
                linked.update(refs[ref])
            else:
                notes.add(f"Unresolved/ambiguous legacy idea reference {ref[0]} #{ref[1]} ({pid}).")
        if not linked:
            for u in urls(text):
                if len(by_url[u]) == 1:
                    linked.update(by_url[u])
                elif by_url[u]:
                    notes.add(f"Ambiguous idea URL on proposal {pid}; not joined.")
        proposal_links[pid] = linked
        proposal_parents[pid] = {str(v) for container in (p, ch)
                                 for field in ("trial_id", "parent_proposal_id", "source_proposal_id")
                                 if (v := container.get(field)) is not None}
        statuses = {str(e.get("status", "")).lower() for e in events}
        stages = {"proposed"}
        if "trialed" in statuses or (p.get("kind") == "trial" and statuses & {"carded", "applied", "kept"}):
            stages.add("trialed")
        if statuses & {"applied", "kept"}:
            stages.add("applied")
        if str(p.get("status", "")).lower() in ("applied", "kept"):
            stages.add("kept")
        proposal_stages[pid] = stages
    # Fixed-point ID joins handle out-of-order rollout events and cycles safely.
    for _ in range(len(proposals)):
        changed = False
        for pid, parents in proposal_parents.items():
            inherited = set().union(*(proposal_links.get(p, set()) for p in parents))
            if inherited - proposal_links[pid]:
                proposal_links[pid].update(inherited)
                changed = True
        if not changed:
            break
    unlinked = sum(not linked for linked in proposal_links.values())
    for pid, linked in proposal_links.items():
        for key in linked:
            ideas[key]["_stages"].update(proposal_stages[pid])
    if unlinked:
        notes.add(f"{unlinked} proposals have unknown idea links; no title/finding-ID guesses made.")

    additions = collections.defaultdict(set)
    for key, idea in ideas.items():
        t = timestamp(idea.get("date") or idea.get("at"))
        if t is None:
            notes.add("Ideas with missing/invalid dates excluded.")
            continue
        if not cutoff <= t <= now:
            continue
        for source in idea["_sources"]:
            for group in source_keys(source):
                for stage in idea["_stages"]:
                    counts[group][stage].add(key)
        if "kept" in idea["_stages"]:
            # Only explicitly labelled quote/reply evidence, not arbitrary @mentions.
            for field in ("quoted_handles", "replied_handles", "quoted_accounts", "replied_accounts"):
                values = idea.get(field, [])
                if isinstance(values, str):
                    values = [values]
                for h in values:
                    if isinstance(h, str) and re.fullmatch(r"@?\w{1,15}", h):
                        additions[h.lstrip("@").lower()].add(f"idea {key}, {field}; sources: {', '.join(sorted(idea['_urls']))}")
            evidence = str(idea.get("evidence", "")) + " " + " ".join(item_evidence.get(u, "") for u in idea["_urls"])
            for h in re.findall(r"(?i)(?:quoted?|replying to|replied to)\s+(@\w{1,15})\b", evidence):
                additions[h[1:].lower()].add(f"idea {key}: {evidence}")

    spend_rows = rows(data / "research/state/spend.jsonl", notes)
    for r in spend_rows:
        m = re.search(r"x_search handles:([\w,]+)", str(r.get("what", "")))
        if m:
            for h in m[1].split(","):
                observe(("x", h.lower(), ""), timestamp(r.get("date")))
    configured = {("handle", h.lower()) for g in config.get("X_HANDLE_GROUPS", {}).get("value", []) for h in g}
    configured |= {("feed", f[0]) for f in config.get("FEEDS", {}).get("value", [])}
    drops = []
    for group in configured:
        counts[group]  # show configured sources even when their history is unknown
        history = observed[group]
        if history and min(history) <= cutoff and max(history) >= now - 7 * 86400 and not counts[group]["proposed"]:
            drops.append(group)
    if any(k[1] == "unknown" for k in counts):
        notes.add("Unknown source attribution is retained as unknown, not assigned to a feed.")
    incomplete = any("ideas.jsonl" in n or "proposals.jsonl" in n for n in notes)
    return {"counts": {k: {s: len(v) for s, v in c.items()} for k, c in counts.items()},
            "observed": dict(observed), "drops": sorted(drops), "additions": dict(additions),
            "config": config, "target_root": source_target_root, "notes": notes, "spend": spend_rows, "unlinked": unlinked,
            "incomplete": incomplete}


def spend_buckets(records, now):
    """Seven UTC calendar buckets ending today (today is partial), not 168h rolling."""
    today = datetime.fromtimestamp(now, UTC).date()
    buckets: dict[str, dict[str, float | None]] = {
        str(today - timedelta(days=i)): {k: None for k in LANES} for i in reversed(range(7))}
    flags = []
    for r in records:
        day, lane, usd = r.get("date"), r.get("lane"), r.get("usd")
        if day not in buckets:
            continue
        if lane not in LANES or isinstance(usd, bool) or not isinstance(usd, (float, int)) or not math.isfinite(usd) or (usd < 0 and r.get("kind") != "settle"):
            flags.append(f"{day}: invalid/unknown lane or amount; excluded")
            continue
        if timestamp(r.get("at")) is not None and timestamp(r["at"]) > now:
            continue
        buckets[day][lane] = (buckets[day][lane] or 0) + usd
    for day, lanes in buckets.items():
        for lane, value in lanes.items():
            if value is not None and CAPS.get(lane) is not None and value > CAPS[lane]:
                flags.append(f"{day}: {lane} OVER ${CAPS[lane]}/day")
        if sum(v or 0 for v in lanes.values()) > 20:
            flags.append(f"{day}: total OVER $20/day")
    total = sum(v or 0 for lanes in buckets.values() for v in lanes.values())
    if total > 140:
        flags.append("Weekly total OVER $140/week")
    return buckets, total, flags


def enforced_stops(data, buckets):
    """Lines for the watchdog's stops/resumes (research/state/budget-stops.jsonl) in the seven budget days."""
    out = []
    for r in rows(Path(data) / "research/state/budget-stops.jsonl", set()):
        if r.get("date") not in buckets:
            continue
        if r.get("action") == "stop":
            out.append(f"- {r['date']}: stopped `{r.get('card')}` ({r.get('lane')}, card ${float(r.get('card_usd') or 0):.2f}"
                       f" of ${float(r.get('card_budget') or 0):g}; lane ${float(r.get('lane_usd') or 0):.2f}, total "
                       f"${float(r.get('total_usd') or 0):.2f}): {r.get('reason')} -> {r.get('result')}")
        elif r.get("action") == "resume":
            out.append(f"- {r['date']}: resumed `{r.get('card')}` ({r.get('result')})")
    return out or ["None: no running engine card was stopped for budget in these seven days."]


def is_source_rule(proposal):
    """Only literal, bounded source-list changes qualify for automatic rollout."""
    target = str(proposal.get("target", ""))
    match = re.fullmatch(r"~/code/hermes/hosts/[A-Za-z0-9_-]+/coach/(coach_scout|coach_research)\.py", target)
    name = {"coach_scout": "X_HANDLE_GROUPS", "coach_research": "FEEDS"}.get(match[1]) if match else None
    change = proposal.get("change", {})
    if proposal.get("kind") != "plugin_rule" or not name or set(change) != {"old_string", "new_string"}:
        return False
    values = []
    try:
        for field in ("old_string", "new_string"):
            tree = ast.parse(change[field])
            if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
                return False
            node = tree.body[0]
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name) or node.targets[0].id != name:
                return False
            value = ast.literal_eval(node.value)
            if not isinstance(value, list) or not value:
                return False
            if name == "X_HANDLE_GROUPS":
                if not all(isinstance(g, list) and 0 < len(g) <= 10 and
                           all(isinstance(h, str) and re.fullmatch(r"[A-Za-z0-9_]{1,15}", h) for h in g) for g in value):
                    return False
            elif not all(isinstance(f, (list, tuple)) and len(f) == 3 and
                         isinstance(f[0], str) and isinstance(f[1], str) and
                         f[1].startswith("https://") and (f[2] is None or isinstance(f[2], str)) for f in value):
                return False
            values.append(value)
        # Feed pruning only; no new network destinations at automatic tier.
        return name != "FEEDS" or all(f in values[0] for f in values[1])
    except (ValueError, TypeError, SyntaxError):
        return False


def source_proposals(result):
    """One bounded replacement per literal assignment, retaining unrelated code.

    Unattributed proposal outcomes make zero-yield pruning unsafe: show candidates
    but withhold removal blocks until links are resolved. Never remove every source.
    """
    proposals = []
    if not result.get("target_root"):
        return proposals
    for name, entry in result["config"].items():
        old = entry["old"]
        value = entry["value"]
        evidence = []
        if name == "X_HANDLE_GROUPS":
            remove = {v for k, v in result["drops"] if k == "handle"} if not (result["unlinked"] or result["incomplete"]) else set()
            new = [[h for h in g if h.lower() not in remove] for g in value]
            new = [g for g in new if g]
            existing = {h.lower() for g in value for h in g}
            for h, proof in sorted(result["additions"].items()):
                if h in existing:
                    continue
                if not new or len(new[-1]) >= 10:
                    new.append([])
                new[-1].append(h)
                evidence.extend(sorted(proof))
            evidence.extend(f"@{h}: zero proposed in mature six-week observed history" for h in sorted(remove))
        else:
            remove = {v for k, v in result["drops"] if k == "feed"} if not (result["unlinked"] or result["incomplete"]) else set()
            new = [f for f in value if f[0] not in remove]
            evidence.extend(f"feed {f}: zero proposed in mature six-week observed history" for f in sorted(remove))
        if not new or new == value:
            continue
        replacement = name + " = " + repr(new)
        ast.parse(replacement)
        proposals.append({"kind": "plugin_rule", "tier": "auto", "target": result["target_root"].rstrip("/") + "/" + entry["file"],
                          "title": "Adjust scout source yield: " + name, "risk": "low",
                          "evidence": "; ".join(evidence), "change": {"old_string": old, "new_string": replacement}})
    return proposals


def section_yield(data, now, sources_dir=None, source_target_root=None):
    r = aggregate(data, now, sources_dir, source_target_root)
    start = datetime.fromtimestamp(now - 42 * 86400, PT)
    end = datetime.fromtimestamp(now, PT)
    out = ["## Engine D: six-week source yield", "",
           f"Report window: {start:%Y-%m-%d %H:%M %Z}–{end:%Y-%m-%d %H:%M %Z} (PT).",
           "Distinct scouted URLs (first observation), distinct ideas by idea date; joined outcomes as of report time. "
           "Multi-source ideas credit each source, so rows are not additive. Applied is historical; kept is latest applied/kept state. "
           "Trial carded counts as initiated, not proof of a completed evaluation.", "",
           "| dimension | source | scouted | ideas | proposed | trialed | applied | kept |",
           "|---|---|---:|---:|---:|---:|---:|---:|"]
    for (dim, source), counts in sorted(r["counts"].items()):
        out.append(f"| {dim} | {source.replace('|', '/')} | " + " | ".join(str(counts[s]) for s in STAGES) + " |")
    out += ["", "### Spend: seven daily UTC budget buckets", "",
            "Caps: scout $3, fixer $7, trial $10; total $20/day and $140/week. "
            "The ledger `date` is the UTC budget day (backfills may have a later `at`). "
            "UTC midnight falls at 16:00 PST / 17:00 PDT on the preceding PT date. Today is partial. "
            "Missing lane/day ledger entries are unknown, NOT known zero; recorded totals are lower bounds.", "",
            "| UTC budget day | PT start → end (exclusive) | scout / 3 | fixer / 7 | trial / 10 | bot | recorded total / 20 |",
            "|---|---|---:|---:|---:|---:|---:|"]
    buckets, total, flags = spend_buckets(r["spend"], now)
    for day, lanes in buckets.items():
        begin = datetime.fromisoformat(day).replace(tzinfo=UTC)
        boundary = f"{begin.astimezone(PT):%m-%d %H:%M %Z} → {(begin + timedelta(days=1)).astimezone(PT):%m-%d %H:%M %Z}"
        out.append(f"| {day} | {boundary} | " + " | ".join("unknown" if v is None else f"${v:.2f}" for v in lanes.values())
                   + f" | ≥${sum(v or 0 for v in lanes.values()):.2f} |")
    out += [f"Recorded weekly total: ≥${total:.2f} / $140; missing coverage prevents an under-budget claim."]
    out.extend("- " + flag for flag in flags)
    out += ["", "### Enforced budget stops (engine_budget watchdog)", ""] + enforced_stops(data, buckets)
    out += ["", "### Source adjustments (candidates, not live edits)",
            "Drop requires zero PROPOSED ideas in six weeks, an observation at least six weeks old and another in the last week. "
            "Fresh/unobserved sources are never pruned. Config is read via AST only; no scripts are imported. "
            "Replacement blocks touch only the named literal list, preserve other code, and never empty a source list."]
    out.extend(f"- Drop candidate: {kind} {name}" for kind, name in r["drops"])
    if (r["unlinked"] or r["incomplete"]) and r["drops"]:
        out.append("Removal blocks withheld: unresolved proposal links or incomplete ledgers could hide yield; repair attribution first.")
    for h, proof in sorted(r["additions"].items()):
        out.append(f"- Add candidate @{h}: " + "; ".join(sorted(proof)))
    blocks = source_proposals(r)
    for p in blocks:
        out += ["", "```coach-proposal", json.dumps(p, indent=2), "```"]
    if not blocks:
        out.append("No safe concrete source changes this window.")
    out += ["", "Coverage / linkage notes:"] + ["- " + n for n in sorted(r["notes"])]
    return out
