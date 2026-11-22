# macOS permission asks: read the grants before relaying

A macOS permission ask (Screen Recording, Accessibility, "grant it at the Mac") is a premise to read, not to relay.

1. Read the Mac's existing grants over `tailnet_exec`:
   `sqlite3 '/Library/Application Support/com.apple.TCC/TCC.db' "select service,client,auth_value from access where service in ('kTCCServiceScreenCapture','kTCCServiceAccessibility')"`
   An `auth_value` of 2 means allowed. Use the system TCC.db: the user-level one is unreadable over ssh.
2. A worker that only probed `screencapture` over ssh has not checked these grants.
3. Often some client already holds the permission: sshd for input, or an installed app for capture. Then re-plan the
   card onto that client, with no new grant.

Never forward the ask to David before this read.
