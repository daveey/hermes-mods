# CLI detector proof chats

A live check that deliberately makes a Hermes detector fire (a failing command run 3x, a fake complaint) must start the session's first prompt with `[selfheal-probe]`, for example `hermes chat -q '[selfheal-probe] run <cmd> three times'`. The coach self-heal lane logs `selfheal_detect` with `probe: true` plus `selfheal_skip why=probe` and files no defect card; read both lines as proof. Never delete `selfheal_pending` rows by hand: a deferred retry can refile the probe later.

CLI proof chats started under agent ancestry are hidden automatically, including SSH-forwarded `LC_HERMES_AGENT`; `--visible` / `HERMES_CHAT_VISIBLE=1` explicitly opts out, and is not needed for terminal relay or `--resume`. Verify the target's installed build supports this behavior before relying on it. Hidden agent CLI proofs retire on completion or 24 h idle independently of `sessions.auto_archive`; still archive each live probe when finished.
