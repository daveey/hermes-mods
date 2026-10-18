---
name: animated-explainer
description: "Use when asked for an illustrated/animated explainer. Narrated, responsive single-file HTML."
version: 1.0.0
author: daveey
license: MIT
metadata:
  hermes:
    tags: [explainer, animation, svg, kids, narration, tts, html, education]
    related_skills: [claude-design]
---

# Animated explainer

An "explainer about X" (a bug, a planet, how a heart works) = a short illustrated story: 4–7 chapters of animated SVG
scenes, narrated (any language, Gemini TTS), with a karaoke caption, chapter list and big touch controls. One
self-contained HTML file (audio inlined as base64, ~0.3 MB per narrated minute) that works on an iPhone, a desktop,
offline, from `file://`, in a Hermes `::preview`, or on any static host.

```
templates/shell.html     the player: cover, stage, progress segments, karaoke caption, story card, chapter list, dock
scripts/tts.py <dir>     narration.json -> audio/n<i>.mp3 + audio/manifest.json (cached per line hash)
scripts/build.py <dir>   explainer.js + explainer.css + narration + audio -> <dir>/dist/<dir>.html
examples/fork-tailed-bush-katydid/   complete reference (6 chapters, Russian narration, 6-year-old audience)
```

## Defaults (don't ask)
- Audience: a curious kid (~6) unless told otherwise. Short sentences, one idea per chapter, a game or a
  "can you find it?" moment, a friendly face on the main character. For adults, same shell with denser body text.
- Narration language = the language asked for; UI text (titles, story card) in English unless asked otherwise. The
  caption shows the narration text, highlighted sentence by sentence.
- Project dir: `~/design/explainers/<slug>/` (durable, not scratch).

## Workflow
1. **Facts first.** Pull 2–3 real sources (web_extract), keep only what they say, list them in `sources`. Kid
   phrasing may simplify, never invent. Note anything from general knowledge in the report.
2. **Outline** 4–7 chapters: hello/meet → body/parts (tap-to-explore hotspots) → how it lives/grows → behaviour →
   a game → people/fun fact. Each chapter = one visual idea that *moves*.
3. **narration.json** (write it before the scenes, since scenes sync to it):
   `{"lang":"ru","ui_lang":"en","voice":"Aoede","style":"<read-aloud instruction, in the narration language>","lines":[one per chapter]}`.
   ~20–55 s per line. Use distinctive phrases ("Номер один…", "А весной…") that scenes can `cue()` on.
4. **explainer.js** defines `const EXPLAINER = {...}` (copy the example's structure):
   `title, coverTitle, coverKick, coverViewBox (crop of scene 1 for the square phone cover), playLabel, playHint,
   langLabel, narrationLang, accent, meta, sources[], defs (SVG gradients/patterns), scenes[]`.
   Scene: `{kick, title, body (HTML), chips[], svg(ctx) -> SVG string in a 640×360 viewBox, enter(ctx)?}`.
   `ctx`: `$(id)`, `cue(phrase) -> seconds into this chapter's audio`, `clock()` (audio time, or a timer when muted),
   `every(fn,ms)`/`later(fn,ms)` (auto-cleared on chapter change), `overlay` (HTML buttons over the stage, e.g. a
   boy/girl toggle), `set({title, body})` (rewrite the story card), `dur`.
   Sync pattern: in `enter`, poll `ctx.clock()` every 150 ms against `ctx.cue(...)` marks and switch visual state
   only when a key changes (see the katydid life-cycle chapter). Keep ambient motion in CSS (`explainer.css`).
5. **Narrate:** `GEMINI_API_KEY=… python3 scripts/tts.py <dir>`; re-run after editing a line (only changed lines are
   re-spoken). Verify the audio by transcribing each clip with a Gemini text model (inline `audio/mpeg`) and
   comparing to the script: no skipped words, no preamble.
6. **Build:** `python3 scripts/build.py <dir>`.
7. **Verify** with chrome-devtools MCP (headless Chrome CLI clamps narrow windows, so it can't show a phone):
   `new_page file://…`, `emulate viewport 390x844x3,mobile,touch`, screenshot the cover; patch
   `HTMLMediaElement.prototype.play` to mute, click `#cover`, step chapters (`#next`, `#toc button`), set
   `audio.currentTime` to cue points and assert the scene state; check `document.documentElement.scrollWidth == 390`
   and no `error` events; repeat at `1440x900x1`. Look at every screenshot. `close_page` at the end.
8. **Deliver:** `::preview{file=".../dist/<slug>.html"}` plus `MEDIA:` of the file. If a publish command is
   configured (below), publish and give the link.

## Publishing (optional, per host)
If `~/.hermes/explainers.json` exists, it is `{"publish": "<command>"}`; run `<command> <built html> --title "<title>"`
and report the URL it prints. Without it, deliver the file only.

## Shell behaviour (so you don't re-implement it)
Cover (full-screen poster on phones, overlay on desktop) → first tap starts audio (iOS needs a user gesture).
Audio end auto-advances; last chapter ends on a replay button. Pause also freezes CSS animation. Swipe left/right on
the stage, ←/→/space/m keys. Muted mode runs chapters on a timer sized to the narration. `?s=<n>` opens chapter n
(no cover) for screenshots. Desktop ≥920 px: two columns with a chapter list; phones: stacked with a sticky dock.

## Pitfalls
- SVG ids are global: prefix per-scene ids if two scenes could coexist (the cover renders scene 1 a second time).
- `transform-box: fill-box` + `transform-origin` for any CSS-rotated SVG part, or it pivots around the canvas.
- A scene that hides something (camouflage game) needs enough foreground covering to be hard, and a reveal.
- Gemini TTS sometimes reads the style instruction aloud; the transcription check catches it. Re-run that line.
- Don't put the narration in `body`: `body` is the readable English card; the caption already shows narration.
