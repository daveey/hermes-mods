# Preserve stopped work in a separate chat

Use when the user wants to stop an attempt without losing its changes and asks to spin it into another session. A preservation request is not authorization to restart implementation.

1. Read the original card's current state and newest handoff. Identify the source session, exact published revision, retained uncommitted worktrees and source/log backup. Verify the backup checksum against its recorded value; copied conversation history alone does not preserve code.
2. Check installed CLI help, then copy history with `hermes sessions branch <source-session> --title '<descriptive title — not deployed>' --source desktop --json`. Use the returned ID. Branching copies history, not worktrees or task ownership.
3. Make the copy discoverable and durable with `hermes sessions reveal <new-session>` and `hermes sessions pin <new-session>`.
4. Send a compact handoff using `cross_session(action='send')`: original goal, exact source/backup locations, published versus uncommitted changes, failed acceptance, and the user's stop decision. For a preservation-only handoff, explicitly exclude implementation, merge, deployment and repair spend until directed; ask only for acknowledgment. Read back the exact message using `cross_session(action='read')`. Stored delivery is not proof of native UI visibility or executed repairs.
5. Record the user's decision and new session link on the existing card. If the user authorized stopping it, archive the execution card as stopped/unmet while preserving source, worktrees, draft PR and evidence; read back its status. For stopped unfinished work, archival is not delivery acceptance or permission to delete artifacts.
6. Return the new session link and a short report of what was copied, which backup was verified, what stopped and what was not deployed. When the original chat also has a separate small request, distinguish its disposition from the preserved broad project rather than silently transferring it.
