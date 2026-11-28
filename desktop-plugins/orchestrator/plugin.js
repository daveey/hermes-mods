import {
  host,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  PALETTE_AREA,
  useQuery,
} from "@hermes/plugin-sdk";
import { useEffect, useRef, useState } from "react";
import { jsx, jsxs } from "react/jsx-runtime";

// Static sibling arrays use jsxs; mapped rows carry explicit stable keys.
const h = (type, props, key) => {
  const { key: propKey, ...rest } = props || {};
  return (Array.isArray(rest.children) ? jsxs : jsx)(
    type,
    rest,
    key ?? propKey,
  );
};

const show = (value) => (value == null ? "unknown" : String(value));
const pct = (value) =>
  value == null ? "unknown" : `${Math.round(value * 100)}%`;
const money = (value) =>
  value == null ? "unknown" : `$${Number(value).toFixed(2)}`;
const BASE = "/api/plugins/user-steps/v1";
export function applyFrame(previous, frame) {
  if (frame.type === "snapshot") return frame.snapshot;
  if (frame.type !== "delta" || previous?.cursor !== frame.base_cursor)
    throw Error("Stream cursor changed; reconnecting");
  return { ...previous, ...frame.changes, cursor: frame.cursor };
}
export async function resolveRoute(owner) {
  const routes = await host.profileRoutes();
  const connections = await host.connections();
  const slug = (text) =>
    String(text || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");
  const candidates = routes.filter(
    (r) =>
      r.targetProfile === owner.targetProfile &&
      (owner.connectionId
        ? r.connectionId === owner.connectionId
        : connections.some(
            (c) =>
              c.id === r.connectionId &&
              (c.gatewayId === owner.gatewayId ||
                c.id === owner.gatewayId ||
                slug(c.label) === owner.gatewayId),
          )),
  );
  if (candidates.length !== 1)
    throw Error("Session owner route unavailable or ambiguous");
  return candidates[0];
}
export async function openRow(row) {
  if (!row.session_id) throw Error("Session owner unavailable");
  return host.openSession(row.session_id, {
    route: await resolveRoute(row.route),
  });
}
const call = (path, route, options = {}) => {
  if (!route?.connectionId || !route?.targetProfile)
    throw Error("Owner route unavailable");
  return window.hermesDesktop.api({
    path,
    connectionId: route.connectionId,
    profile: route.targetProfile,
    timeoutMs: 10000,
    ...options,
  });
};
const decisions = new Map();
export async function decide(ctx, row, choice) {
  const request = row.request;
  if (
    !request ||
    request.state !== "open" ||
    !request.scope?.complete ||
    !request.scope?.markdown?.trim() ||
    !request.choices?.some(
      (c) => c.id === choice.id && !["answer", "revise"].includes(c.effect),
    )
  )
    throw Error("Request is not actionable; open the owning session");
  const key = `decision:${request.owner.gatewayId}:${request.owner.profile}:${request.requestId}:${request.revision}`;
  if (decisions.has(key)) return decisions.get(key);
  const pending = submitDecision(ctx, row, choice, key);
  decisions.set(key, pending);
  try {
    return await pending;
  } finally {
    decisions.delete(key);
  }
}
async function submitDecision(ctx, row, choice, key) {
  const request = row.request;
  let intent = await ctx.storage.get(key, null);
  if (!intent) {
    intent = {
      session: request.owner.storedSessionId,
      expectedRevision: request.revision,
      choiceId: choice.id,
      idempotencyKey: crypto.randomUUID(),
    };
    await ctx.storage.set(key, intent); // fail closed before POST if durable retry storage fails
  }
  const route = await resolveRoute(row.route),
    query = `?session=${encodeURIComponent(intent.session)}`;
  let result = await call(
    `${BASE}/operations${query}&idempotencyKey=${encodeURIComponent(intent.idempotencyKey)}`,
    route,
  ).catch((error) => {
    if (/404/.test(String(error))) return null;
    throw error;
  });
  if (!result?.operation)
    result = await call(
      `${BASE}/requests/${encodeURIComponent(request.requestId)}/decisions`,
      route,
      { method: "POST", body: intent },
    );
  const id = result?.operation?.operationId;
  if (!id) throw Error("No operation receipt; retain intent and check again");
  const receipt = await call(
    `${BASE}/operations/${encodeURIComponent(id)}${query}`,
    route,
  );
  if (
    receipt.operation?.operationId !== id ||
    receipt.request?.requestId !== request.requestId ||
    receipt.request?.owner?.storedSessionId !== intent.session ||
    receipt.request?.revision < request.revision
  )
    throw Error("Invalid decision readback");
  return receipt;
}
export async function steer(route, lane, field, value) {
  const authority = await call("/api/plugins/lanes/v1/lanes/steering", route);
  if (authority?.steering !== true)
    throw Error("Lane edits require steering-hub authority");
  await call(`/api/plugins/lanes/v1/lanes/${encodeURIComponent(lane)}`, route, {
    method: "PUT",
    body: { [field]: value },
  });
  const readback = await call(
    "/api/plugins/lanes/v1/lanes?hours=6&expanded=true",
    route,
  );
  const actual =
    field === "max_workers"
      ? readback.lanes?.[lane]?.limits?.max_workers
      : readback.lanes?.[lane]?.[field];
  if (actual !== value)
    throw Error("Awaiting producer readback; write not yet confirmed");
  return readback;
}
export function createSource(ctx) {
  let route;
  return {
    async read(query) {
      if (!route) {
        const routes = await host.profileRoutes();
        for (const candidate of routes) {
          try {
            const authority = await call(
              "/api/plugins/lanes/v1/lanes/steering",
              candidate,
            );
            if (authority?.steering) {
              route = candidate;
              break;
            }
          } catch {
            /* continue discovery; never infer write authority */
          }
        }
        if (!route) throw Error("Steering hub unavailable");
      }
      return ctx.rest(`/v1/snapshot?q=${encodeURIComponent(query)}`, {
        connectionId: route.connectionId,
        profile: route.targetProfile,
      });
    },
    stream(query, onFrame) {
      // SDK socket is ambient-only. Never subscribe to a different gateway than REST.
      if (
        !route ||
        host.activeConnectionId?.() !== route.connectionId ||
        host.state.profile.get() !== route.targetProfile
      )
        return () => {};
      return ctx.socket(`/v1/stream?q=${encodeURIComponent(query)}`, onFrame);
    },
    route: () => route,
  };
}
function Highlight({ text, query }) {
  const value = String(text || ""),
    index = query ? value.toLowerCase().indexOf(query.toLowerCase()) : -1;
  return index < 0
    ? value
    : [
        value.slice(0, index),
        h(
          "mark",
          { children: value.slice(index, index + query.length) },
          "mark",
        ),
        value.slice(index + query.length),
      ];
}
function Row({ row, nested = false, query, activeKey, onPick }) {
  return h(
    "button",
    {
      type: "button",
      className: `oc-row ${nested ? "nested" : ""} ${row.state === "running" ? "running" : row.state === "human" ? "human" : "waiting"} ${activeKey === row.key ? "cursor" : ""}`,
      "data-row": row.key,
      onClick: () => onPick(row),
      children: [
        h("span", { className: "oc-dot" }),
        h("span", {
          className: "oc-row-title",
          children: [
            h("strong", { children: h(Highlight, { text: row.title, query }) }),
            h("small", { children: row.label || row.state }),
          ],
        }),
        h("small", { children: row.route?.profile || "owner unknown" }),
      ],
    },
    row.key,
  );
}
export function FlightDeck({ ctx, source: supplied }) {
  const source = useRef(supplied || createSource(ctx)).current;
  const [query, setQuery] = useState(""),
    [snap, setSnap] = useState(null),
    [live, setLive] = useState(false);
  const [selected, select] = useState(null),
    [cursor, setCursor] = useState(0),
    [dialog, setDialog] = useState(null);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [receipt, setReceipt] = useState("");
  const root = useRef(null),
    filter = useRef(null),
    restore = useRef(null),
    frame = useRef(null),
    seenAt = useRef(0);
  const read = useQuery({
    queryKey: ["orchestrator-v1", query],
    queryFn: () => source.read(query),
    refetchInterval: 15000,
    retry: false,
  });
  useEffect(() => {
    if (read.data) {
      frame.current = read.data;
      setSnap(read.data);
    }
  }, [read.data]);
  useEffect(() => {
    setLive(false);
    let active = true;
    const dispose = source.stream(query, (message) => {
      if (!active) return;
      try {
        frame.current = applyFrame(frame.current, message);
        setSnap(frame.current);
        seenAt.current = Date.now();
        setLive(true);
      } catch (e) {
        setLive(false);
        setError(e.message);
        void read.refetch();
      }
    });
    const timer = setInterval(() => {
      if (Date.now() - seenAt.current > 20000) setLive(false);
    }, 5000);
    return () => {
      active = false;
      dispose?.();
      clearInterval(timer);
    };
  }, [query, source, !!read.data]);
  const human = snap?.human || [],
    machinery = snap?.machinery || [],
    chains = snap?.chains || [];
  // Identity-only deduplication is layout, not accounting. Totals always come from the hub.
  const claimed = new Set(
    [
      ...human,
      ...machinery,
      ...chains.flatMap((c) => [c.prerequisite, ...c.waiters]),
    ].map((r) => r.key),
  );
  const sessions = (snap?.sessions || []).filter((r) => !claimed.has(r.key));
  const rows = [
    ...human,
    ...machinery,
    ...chains.flatMap((c) => [c.prerequisite, ...c.waiters]),
    ...sessions,
  ];
  const current = useRef({});
  current.current = { query, selected, dialog, rows, cursor };
  const closeDialog = () => {
    setDialog(null);
    queueMicrotask(() => restore.current?.focus());
  };
  const openDialog = (kind) => {
    restore.current = document.activeElement;
    setDialog(kind);
  };
  useEffect(() => {
    const key = (e) => {
      const s = current.current,
        editing =
          /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) ||
          e.target.isContentEditable;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        openDialog("palette");
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        if (s.dialog) closeDialog();
        else if (s.selected) {
          select(null);
          root.current?.focus();
        } else {
          setQuery("");
          filter.current?.focus();
        }
        return;
      }
      if (s.dialog) return;
      if (e.key === "?" && !editing) {
        e.preventDefault();
        openDialog("help");
        return;
      }
      if (e.key === "/" && !editing) {
        e.preventDefault();
        filter.current?.focus();
        return;
      }
      if (["ArrowDown", "ArrowUp"].includes(e.key)) {
        e.preventDefault();
        setCursor((n) =>
          Math.max(
            0,
            Math.min(s.rows.length - 1, n + (e.key === "ArrowDown" ? 1 : -1)),
          ),
        );
        return;
      }
      if (
        e.key === "Enter" &&
        (!["BUTTON", "SELECT"].includes(e.target.tagName) ||
          e.target.hasAttribute("data-row"))
      ) {
        e.preventDefault();
        select(s.rows[s.cursor] || s.rows[0] || null);
        return;
      }
      if (
        !editing &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey &&
        e.key.length === 1
      ) {
        e.preventDefault();
        setQuery((q) => q + e.key);
        filter.current?.focus();
      }
    };
    const node = root.current;
    node.addEventListener("keydown", key);
    return () => node.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {
    setCursor(0);
  }, [query]);
  useEffect(() => {
    if (dialog) root.current?.querySelector("[role=dialog] button")?.focus();
  }, [dialog]);
  const act = async (fn) => {
    setBusy(true);
    setError("");
    try {
      const result = await fn();
      setReceipt(
        result?.operation
          ? `Recorded · ${result.operation.dispatchState || "pending"}`
          : "Producer readback confirmed",
      );
      await read.refetch();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const rowProps = {
    query,
    activeKey: rows[cursor]?.key,
    onPick: (row) => {
      select(row);
      setReceipt("");
    },
  };
  const section = (title, count, list) =>
    h("section", {
      className: "oc-section",
      children: [
        h("h2", { children: [title, h("span", { children: show(count) })] }),
        list.length
          ? list.map((row) => h(Row, { ...rowProps, row }, row.key))
          : h("p", {
              className: "oc-empty",
              children:
                count == null
                  ? "Source unavailable — not a zero count"
                  : "No matching items",
            }),
      ],
    });
  const tele = snap?.telemetry || {},
    governors = tele.governors || {};
  return h("div", {
    className: "oc",
    ref: root,
    tabIndex: -1,
    children: [
      h("style", { children: CSS }),
      h("header", {
        className: "oc-top",
        children: [
          h("strong", { children: "Operator Flight Deck" }),
          h("span", {
            className: live ? "oc-live" : "oc-warn",
            children: live ? "live" : "not live · bounded poll",
          }),
          h("div", {
            className: "oc-filter",
            children: [
              h("input", {
                ref: filter,
                value: query,
                placeholder: "Filter chats, cards, lanes…",
                "aria-label": "Filter items",
                onChange: (e) => setQuery(e.target.value),
              }),
              h("span", {
                id: "fcount",
                children: query
                  ? `On you ${show(snap?.totals?.filtered?.human)} · Machinery ${show(snap?.totals?.filtered?.machinery)} · Sessions ${show(snap?.totals?.filtered?.sessions)}`
                  : "/",
              }),
            ],
          }),
          h("button", {
            onClick: () => openDialog("palette"),
            children: "Palette ⌘K",
          }),
          h("button", {
            onClick: () => openDialog("help"),
            "aria-label": "Keyboard shortcuts",
            children: "?",
          }),
        ],
      }),
      h("div", {
        className: "oc-telemetry",
        children: [
          h("span", {
            className: "oc-hub",
            title: tele.hub?.reason || tele.hub?.current,
            children: [
              "Hub 24h ",
              tele.hub?.band?.slots
                ? h("span", {
                    className: "oc-band",
                    children: tele.hub.band.slots.map((slot, index) =>
                      h(
                        "i",
                        {
                          className: `oc-history ${slot.state}`,
                          title: slot.state,
                          style: { flexGrow: slot.end - slot.start },
                        },
                        index,
                      ),
                    ),
                  })
                : "unknown",
            ],
          }),
          h("span", {
            children: [
              "Fleet ",
              ...(tele.fleet?.slots || []).map((s, i) =>
                h(
                  "i",
                  {
                    className: `oc-pip ${s.busy === true ? "busy" : s.busy == null ? "unknown" : ""}`,
                    title: s.state,
                  },
                  i,
                ),
              ),
              show(tele.fleet?.label),
            ],
          }),
          h("span", {
            children: [
              "CI ",
              ...(tele.ci?.slots || []).map((s, i) =>
                h(
                  "i",
                  {
                    className: `oc-pip ${s.busy === true ? "busy" : s.busy == null ? "unknown" : ""}`,
                    title: s.state,
                  },
                  i,
                ),
              ),
              show(tele.ci?.label),
            ],
          }),
          h("span", {
            children: `Subscription forecast · ${show(tele.subscription?.label)}`,
          }),
          h("span", { children: `Sync / Auth · ${show(tele.alert_label)}` }),
        ],
      }),
      h("div", {
        className: "oc-totals",
        children: [
          h("span", {
            children: `All sessions ${show(snap?.totals?.sessions)}`,
          }),
          h("span", {
            className: "human",
            children: `On you ${show(snap?.totals?.human)}`,
          }),
          h("span", { children: `Machinery ${show(snap?.totals?.machinery)}` }),
          h("span", {
            className: "running",
            children: `Running ${show(snap?.totals?.running)}`,
          }),
        ],
      }),
      h("section", {
        className: "oc-lanes",
        "aria-label": "Semantic Lane Steering",
        children: [
          h("div", {
            className: "oc-lane oc-lane-head",
            children: [
              "Lane",
              "Share",
              "6h Actual vs Target",
              "On You",
              "Run",
              "Gate / Ready",
              "6h Cost",
              "Pool Cap",
            ].map((label) => h("span", { children: label }, label)),
          }),
          ...(snap?.lanes || []).map((lane) =>
            h(
              "div",
              {
                className: "oc-lane",
                "data-lane": lane.key,
                children: [
                  h("strong", { children: lane.label }),
                  h("input", {
                    type: "number",
                    min: 0,
                    "aria-label": `${lane.label} share`,
                    defaultValue: lane.share,
                    key: `${lane.key}:${lane.share}`,
                    disabled: busy || lane.share == null,
                    onBlur: (e) => {
                      if (Number(e.target.value) !== lane.share)
                        void act(() =>
                          steer(
                            source.route(),
                            lane.key,
                            "share",
                            Number(e.target.value),
                          ),
                        );
                    },
                    onKeyDown: (e) => {
                      if (e.key === "Enter") e.target.blur();
                    },
                  }),
                  h("span", {
                    children: `${pct(lane.actual_share)} / ${pct(lane.target_share)}`,
                  }),
                  h("span", {
                    className: "human",
                    children: show(lane.blocked_on_you),
                  }),
                  h("span", {
                    className: "running",
                    children: [
                      show(lane.running),
                      lane.over > 0
                        ? h("small", {
                            className: "oc-over",
                            children: `+${lane.over} over`,
                          })
                        : null,
                    ],
                  }),
                  h("span", {
                    children: `${lane.label} lane gate · ${show(lane.governor_held)} held (${show(lane.gate?.admitted)}/${show(lane.gate?.budget)} admitted/budget) · ${show(lane.ready)} ready`,
                  }),
                  h("span", { children: money(lane.cost?.window) }),
                  h("input", {
                    type: "number",
                    min: 0,
                    "aria-label": `${lane.label} pool cap`,
                    defaultValue: lane.cap,
                    key: `${lane.key}:${lane.cap}`,
                    disabled: busy || lane.cap == null,
                    onBlur: (e) => {
                      if (Number(e.target.value) !== lane.cap)
                        void act(() =>
                          steer(
                            source.route(),
                            lane.key,
                            "max_workers",
                            Number(e.target.value),
                          ),
                        );
                    },
                    onKeyDown: (e) => {
                      if (e.key === "Enter") e.target.blur();
                    },
                  }),
                ],
              },
              lane.key,
            ),
          ),
        ],
      }),
      (error || read.error) &&
        h("div", { role: "alert", children: error || read.error.message }),
      h("div", {
        className: `oc-work ${selected ? "inspecting" : ""}`,
        children: [
          h("main", {
            className: "oc-main",
            children: [
              h("div", {
                children: [
                  section(
                    "1 · Needs David",
                    snap?.totals?.filtered?.human,
                    human,
                  ),
                  section(
                    "2 · Machinery Exceptions",
                    snap?.totals?.filtered?.machinery,
                    machinery,
                  ),
                ],
              }),
              h("section", {
                className: "oc-section",
                children: [
                  h("h2", {
                    children: [
                      "3 · Cross-Gateway Sessions & Wait Chains",
                      h("span", {
                        children: show(snap?.totals?.filtered?.sessions),
                      }),
                    ],
                  }),
                  ...chains.map((c) =>
                    h(
                      "div",
                      {
                        className: "oc-chain",
                        children: [
                          h(Row, { ...rowProps, row: c.prerequisite }),
                          ...c.waiters.map((row) =>
                            h(Row, { ...rowProps, row, nested: true }, row.key),
                          ),
                        ],
                      },
                      c.key,
                    ),
                  ),
                  ...sessions.map((row) =>
                    h(Row, { ...rowProps, row }, row.key),
                  ),
                ],
              }),
            ],
          }),
          selected &&
            h("aside", {
              className: "oc-inspector",
              "aria-label": "Detail inspector",
              children: [
                h("button", {
                  onClick: () => {
                    select(null);
                    root.current?.focus();
                  },
                  children: "Back",
                }),
                h("h2", { children: selected.title }),
                h("p", { children: selected.label || selected.state }),
                h("p", {
                  children: `${show(selected.route?.connectionId)} / ${show(selected.route?.profile)}`,
                }),
                h("button", {
                  disabled: !selected.session_id,
                  onClick: () => void act(() => openRow(selected)),
                  children: "Open session",
                }),
                selected.request &&
                  h("div", {
                    children: [
                      h("p", {
                        children:
                          selected.request.scope?.markdown ||
                          "Review this request in its owning session.",
                      }),
                      ...(selected.request.choices || []).map((choice) =>
                        h(
                          "button",
                          {
                            disabled:
                              busy ||
                              !!receipt ||
                              !selected.request.scope?.complete ||
                              ["answer", "revise"].includes(choice.effect),
                            onClick: () =>
                              void act(() => decide(ctx, selected, choice)),
                            children: choice.label,
                          },
                          choice.id,
                        ),
                      ),
                    ],
                  }),
                h("p", { role: "status", children: receipt }),
              ],
            }),
        ],
      }),
      h("footer", {
        children:
          (snap?.sources || [])
            .filter((s) => s.state !== "healthy")
            .map((s) => `${s.name}: unknown`)
            .join(" · ") || "Producer snapshots reconciled",
      }),
      dialog &&
        h("div", {
          className: "oc-overlay",
          children: h("section", {
            role: "dialog",
            "aria-modal": true,
            "aria-label":
              dialog === "help" ? "Keyboard shortcuts" : "Command palette",
            onKeyDown: (e) => {
              if (e.key === "Tab") {
                const buttons = [...e.currentTarget.querySelectorAll("button")];
                if (e.shiftKey && document.activeElement === buttons[0]) {
                  e.preventDefault();
                  buttons.at(-1)?.focus();
                } else if (
                  !e.shiftKey &&
                  document.activeElement === buttons.at(-1)
                ) {
                  e.preventDefault();
                  buttons[0]?.focus();
                }
              }
            },
            children: [
              h("button", { onClick: closeDialog, children: "Close" }),
              h("h2", {
                children:
                  dialog === "help" ? "Keyboard shortcuts" : "Jump to object",
              }),
              dialog === "help"
                ? h("p", {
                    children:
                      "/ Filter · Type anywhere to search · ↑↓ Move · Enter Inspect · Esc Back then clear · ⌘K Palette · ? Help",
                  })
                : rows.map((row) =>
                    h(
                      "button",
                      {
                        onClick: () => {
                          closeDialog();
                          select(row);
                        },
                        children: row.title,
                      },
                      row.key,
                    ),
                  ),
            ],
          }),
        }),
    ],
  });
}
export default {
  id: "orchestrator",
  name: "Orchestration Console",
  defaultEnabled: false,
  register(ctx) {
    ctx.register({
      id: "flight-deck",
      area: ROUTES_AREA,
      data: { path: "/orchestrator" },
      render: () => h(FlightDeck, { ctx }),
    });
    ctx.register({
      id: "nav",
      area: SIDEBAR_NAV_AREA,
      data: {
        path: "/orchestrator",
        label: "Orchestration",
        codicon: "dashboard",
      },
    });
    ctx.register({
      id: "open",
      area: PALETTE_AREA,
      data: {
        title: "Open Orchestration Console",
        onSelect: () => host.navigate("/orchestrator"),
      },
    });
  },
};
const CSS = `
.oc{--oc-line:var(--ui-stroke-secondary);--oc-muted:var(--ui-text-tertiary);font:12px/1.45 system-ui;color:var(--ui-text-primary);height:100%;display:flex;flex-direction:column;min-width:0;outline:none}.oc *{box-sizing:border-box}.oc button,.oc input{font:inherit;color:inherit;background:var(--ui-bg-secondary);border:1px solid var(--oc-line);border-radius:4px}.oc button{cursor:pointer;padding:5px 8px;text-align:left}.oc button:focus-visible,.oc .cursor{outline:1px solid var(--ui-accent);outline-offset:-1px}.oc-top{display:flex;gap:8px;align-items:center;padding:10px;border-bottom:1px solid var(--oc-line);flex-wrap:wrap}.oc-filter{margin-left:auto;display:flex;flex-wrap:wrap;gap:5px;align-items:center}.oc-filter input{padding:5px;min-width:160px}.oc #fcount{font-size:10px;color:var(--oc-muted)}.oc-live{color:var(--ui-success)}.oc-warn{color:var(--ui-warning)}.oc-telemetry,.oc-totals{display:flex;gap:16px;flex-wrap:wrap;padding:8px 10px;border-bottom:1px solid var(--oc-line)}.oc-telemetry{font-size:10px;color:var(--oc-muted)}.oc-pip{display:inline-block;width:4px;height:12px;margin:0 1px;background:var(--oc-line)}.oc-pip.unknown{outline:1px dashed var(--oc-muted);background:transparent}.oc-band{display:inline-flex;width:84px;height:8px;gap:1px;vertical-align:middle}.oc-history{min-width:2px;background:var(--oc-muted)}.oc-history.up{background:var(--ui-success)}.oc-history.down{background:var(--ui-danger)}.oc-history.restarting{background:var(--ui-warning)}.oc-pip.busy{background:var(--ui-accent)}.oc-lanes{padding:0 10px}.oc-lane{display:grid;grid-template-columns:minmax(70px,1fr) 55px minmax(95px,1fr) 50px 70px minmax(155px,2fr) 65px 60px;gap:8px;padding:8px 0;border-bottom:1px solid var(--oc-line);align-items:center}.oc-lane>*{min-width:0;overflow-wrap:anywhere}.oc-lane input{width:100%;padding:3px}.oc-lane-head{font-size:10px;color:var(--oc-muted)}.oc-over{display:block;color:var(--ui-warning);font-size:10px}.oc-work{display:grid;grid-template-columns:minmax(0,1fr) 0;min-height:0;flex:1;overflow:auto}.oc-work.inspecting{grid-template-columns:minmax(0,1fr) 380px}.oc-main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:12px;padding:12px;min-width:0;align-content:start}.oc-main>*{min-width:0}.oc-section{border:1px solid var(--oc-line);margin-bottom:12px;border-radius:4px;overflow:hidden}.oc h2{font-size:12px;line-height:1.5;margin:0;padding:9px;border-bottom:1px solid var(--oc-line)}.oc h2>span{float:right;color:var(--oc-muted);margin-left:8px}.oc-row{display:flex!important;align-items:center;gap:8px;width:100%;border:0!important;border-bottom:1px solid var(--oc-line)!important;border-radius:0!important;padding:10px!important;min-width:0;background:transparent!important}.oc-row-title{flex:1;min-width:0}.oc-row strong{display:block;font-weight:500;overflow-wrap:anywhere}.oc-row small{font-size:10px;color:var(--oc-muted);display:block;overflow-wrap:anywhere}.oc-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;background:currentColor}.oc .running{color:var(--ui-accent)}.oc .human{color:var(--ui-danger)}.oc .waiting{color:var(--ui-warning)}.oc .nested{padding-left:28px!important}.oc-empty{padding:8px;color:var(--oc-muted)}.oc-inspector{width:380px;min-width:0;border-left:1px solid var(--oc-line);padding:12px;overflow-wrap:anywhere}.oc-inspector p{white-space:pre-wrap}.oc-inspector button{margin:3px}.oc footer{font-size:10px;color:var(--oc-muted);padding:6px 10px;border-top:1px solid var(--oc-line)}.oc [role=alert]{padding:8px;color:var(--ui-danger)}.oc mark{background:var(--ui-warning);color:var(--ui-bg-primary)}.oc-overlay{position:fixed;inset:0;display:grid;place-items:center;background:var(--ui-bg-overlay);z-index:30;padding:20px}.oc [role=dialog]{width:min(520px,100%);max-height:80vh;overflow:auto;padding:16px;border:1px solid var(--oc-line);background:var(--ui-bg-primary)}.oc [role=dialog]>button{display:block;margin:5px 0}
@media(max-width:1100px){.oc-lane{grid-template-columns:minmax(70px,1fr) 55px minmax(95px,1fr) 50px 70px minmax(155px,2fr) 65px 60px;gap:4px;font-size:11px}.oc-work.inspecting .oc-main{grid-template-columns:1fr}}
@media(max-width:800px){.oc-lane-head{display:none}.oc-lane{grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.oc-lane>*:nth-child(6){grid-column:span 2}.oc-lane>*:nth-child(2):before{content:'Share'}.oc-main{grid-template-columns:1fr}.oc-work.inspecting{grid-template-columns:minmax(0,1fr)}.oc-work.inspecting .oc-main{display:none}.oc-inspector{width:min(380px,100%);justify-self:end}.oc-filter{margin-left:0;flex:1}.oc-filter input{width:100%}}
`;
