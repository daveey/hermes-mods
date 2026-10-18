# Motion, voice sync and real-browser audio tests

Depth for steps 4 and 7 of the workflow. The shell already implements cover/autoplay/pause/mute; this is about
writing scenes that move well and proving the sync works.

## Drawing the subject
- Draw the main character as a JS function returning an SVG string in a fixed local box with feet on a known
  baseline (e.g. `kd({sex, nymph, pads, wingCls})`, 300×140, feet at y=140). Place it with an outer
  `<g transform="translate() scale()">` and animate an inner `<g class="...">`. Reuse it for every variant
  (male/female, juvenile stages, flipped with `scale(-s s)`), so all chapters stay on-model.
- Scenery from small helpers (`leaf(x,y,len,angle,fill)`) plus a seeded RNG reset inside `svg()`, so a scene
  renders identically every time (cover and chapter, screenshots).
- Kids: bigger eye with a highlight, small smile, cheek blush; on-stage counters/labels ("Nom nom: 3",
  "There it is!"); simple words with the real term once ("feelers (antennae)", "egg sword" for ovipositor).

## CSS motion rules
- `transform-box: fill-box` + a `%` `transform-origin` (e.g. `100% 100%` for an antenna base) instead of
  hand-computed pivots inside scaled groups.
- A CSS `transform` overrides the SVG `transform` attribute: never animate the element that carries the placement
  transform; nest a `<g>` for the animation.
- JS-moved actors: `el.style.transform = 'translate(Xpx,Ypx) scale(s)'` + a CSS `transition` (px = user units
  inside the SVG). Retrigger a one-shot class: remove it, `void el.getBBox()`, add it again.
- Ripple/sound rings: animate `transform: scale()` with `vector-effect: non-scaling-stroke`, not `r`.
- Every infinite loop has a purpose (calling, hopping, searching). The shell stops animation under
  `prefers-reduced-motion` and freezes it on pause.

## Syncing visuals to the narration
- `ctx.cue(phrase)` = `indexOf(phrase) / text length × clip duration`: character-proportional, accurate to ~1 s
  because tts.py trims leading silence. Pick phrases that start sentences.
- In `enter(ctx)`: `ctx.every(render, 150)`, where `render` derives a state key from `ctx.clock()` vs the cue marks
  and only touches the DOM when the key changes. Spread N steps evenly inside one sentence span.
- CSS loop with a timed reveal at 50 % of the loop: set `animationDuration = 2 × ctx.cue('There it is')`.
- An interactive scene (tap hotspots) stops following the voice once the child taps (`auto = false`).

## Testing audio without sound (chrome-devtools MCP)
1. Before starting playback: `evaluate_script` that wraps `HTMLMediaElement.prototype.play` to set
   `this.muted = true` and stash `window.__a = this`, plus an `error` collector. Without it the narration plays out
   loud on the user's machine.
2. Start with a real click (`click` on the cover's snapshot uid, or `#cover` click inside the same script). A
   reload drops the wrapper: re-install it after every `navigate_page`.
3. Seek `__a.currentTime` to cue times and read `#title`, `#cap .on`, stage labels; seek to `duration - 0.3` to
   check auto-advance and the last-chapter replay state.
4. A tab opened in the background has throttled timers: `select_page bringToFront:true` and check
   `document.visibilityState` before judging timer-driven behaviour.

## Narration QA
- Transcribe each mp3 (inline `audio/mpeg`) with a current Gemini flash text model in parallel; ask for verbatim
  text plus skipped/mispronounced words and any preamble. Model names rotate: if one 404s, list
  `GET /v1beta/models?key=…` and pick the newest flash.
