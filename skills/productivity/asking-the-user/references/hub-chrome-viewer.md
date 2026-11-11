# Showing the hub's agent Chrome on the user's Mac (noVNC over SSH)

The hub's `browser_exec` Chrome draws on Xvfb `:99`; the stock x11vnc on `:5900` needs a password file the user
doesn't have. Use a throwaway localhost-only, no-password VNC: SSH is the only way in, so it stays private.

1. Hub (terminal):
   - `x11vnc -display :99 -localhost -rfbport 5901 -nopw -shared -forever -timeout 1800 -bg -o $TMPDIR/x11vnc-5901.log`
   - background: `timeout 2400 websockify --web /usr/share/novnc 127.0.0.1:6082 127.0.0.1:5901`
   - check `ss -ltn | grep -E ':5901|:6082'`.
2. User's Mac (`run_in_user_terminal`, reason = what they will sign in to):
   `(nc -z localhost 6082 2>/dev/null || ssh -o ExitOnForwardFailure=yes -f -N -L 6082:127.0.0.1:6082 ubuntu@<hub tailnet DNS name>) && open "http://localhost:6082/vnc.html?autoconnect=1&resize=scale"`
   The `nc -z` guard makes the same command reopen the view ("show again") without a second tunnel.
   If ssh prints a new host-key prompt, compare its fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the hub.
   The websockify log shows `connecting to: 127.0.0.1:5901` once the tab attached.
3. While the user acts, read the tab with `js("location.href")`; don't navigate it mid-login (a Google
   `challenge/dp` URL means a phone prompt is pending).
4. When done: verify via URL/cookies, then `pkill -f 'websockify --web /usr/share/novnc 127.0.0.1:6082'` and
   kill the `rfbport 5901` x11vnc (pkill from a shell whose own command line matches kills that shell too:
   run it as its own command and re-check `ss -ltn` separately). The Mac's ssh tunnel then forwards to nothing.
