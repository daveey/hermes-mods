---
name: ambitious-requests
description: "Use when asked to make a request more ambitious or improve a prompt. Applies Altitude/Depth/Range lenses."
version: 1.0.0
author: daveey
license: MIT
metadata:
  hermes:
    tags: [prompting, ambition, planning, coaching]
    related_skills: [writing-plans, grounded-citations]
---

# Ambitious requests: Altitude, Depth, Range

Source: Felix Rieseberg, "How to be more ambitious: A tutorial for humans with models"
(felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models). The `prompt-coach`
plugin applies the same lenses automatically after each send. Use this skill when the user asks to
"make this more ambitious", "improve my request", or wants the weekly prompt-coach digest explained.

## The lenses (pick the ONE with most upside)

**Altitude: ask for the outcome, not the step.** Ask "what should happen next? what's the goal
beyond the goal?" "Fix this crash" becomes "keep the app crash-free". Best when a real end outcome
exists (a tell: there will be meetings about it). Weak for purely creative work. It usually needs
setup first: give the agent the access it needs to reproduce and verify on its own (logs, machines,
data, version→commit mapping). That setup is the first step of the rewrite.

**Depth: do it the way a field-leading expert would.**
- Start from the rawest material, not something already summarized.
- Use the experts' actual instruments (databases, statistical models, profilers, simulations).
  Not markdown tables. Models switch vocabulary without switching methods, so name the method.
- Set an unreasonable target and say it's reasonable (200ms, not 5s). If the agent says it's
  impossible, tell it again that it's solvable and to keep going.
- Test the user's assumptions against raw data ("we believe churn is onboarding; try to reproduce that").
- Produce new knowledge: find the blind spot and show it predicts on held-out data.
- Use an outside judge: a test, metric, held-out set or tool the agent can run. Never self-grading.

Felix's depth rewrite prompt:
> I want a deeper version of this task, not a bigger one. Before rewriting it: (1) say what a top
> professional in this field would do here that I'm not asking for; (2) name the rawest material
> they would start from instead of what has already been digested for me; (3) name the specific
> methods and tools they would use; (4) say what, other than you or me, would show the result is
> right. Then rewrite my prompt to ask for exactly that.

**Range: bring in other fields.** Restate nouns as verbs: a QBR is not "a deck", it's "getting busy
people to believe a cause-and-effect story and decide under uncertainty in 30 minutes". Then assemble
the crew (analyst, journalist, editor, short seller, forensic accountant...). Use those roles to find
methods, and have the agent *do* what they do, never role-play them. Subagents work well for this.

## Writing the rewrite

- Write it in the user's voice, ready to send to a fresh session. Keep every fact, path and
  constraint. At most ~150 words.
- Cover, where relevant: the real outcome, the raw inputs, the methods and tools, a stretch target,
  the outside check, and any access setup needed first.
- Stay realistic about what the agent can reach. If the original is already outcome-level and
  well-scoped, say so and don't rewrite it.

## Pitfalls

- Bigger is not deeper. Adding scope without changing method or validation is noise.
- Role-play makes the transcript weirder and the result no better.
- Altitude without access fails: "keep X healthy" only works when the agent can observe X.
