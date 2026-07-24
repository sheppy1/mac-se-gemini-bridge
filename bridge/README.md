# Mac SE ↔ Gemini Telnet Bridge

Lets a real 1987 Macintosh SE "chat with AI" by typing into a Telnet client
(BetterTelnet), which talks to a small Python bridge running on a Windows PC
on the same LAN, which calls the Gemini API over HTTPS and relays the reply
back.

For the full story of how this was built (StuffIt/AppleDouble archaeology,
NetPresenz FTP setup, the MacTCP passive-mode saga, etc.), see
[`../SESSION_SUMMARY.md`](../SESSION_SUMMARY.md). For the packaging scripts
used to build `BetterTelnet.bin` (and `../netpresenz/NetPresenz.dsk[.bin]`),
see [`../tools/`](../tools/). This file is just the "how to run/rebuild the
bridge itself" reference.

## Architecture

```
 Macintosh SE                  Windows PC                    Internet
 (192.168.1.210)               (192.168.1.158)
 BetterTelnet          LAN     bridge.py                     Google
 ───────────────  TCP:6023 ──▶ (Python, stdlib only)  HTTPS ──▶ Gemini API
                                listens on 0.0.0.0:6023        generateContent
```

- The SE runs **BetterTelnet** and connects to the Windows PC's LAN IP on
  port 6023.
- `bridge.py` is a plain TCP server (not a real Telnet daemon — it just
  answers enough Telnet IAC option-negotiation to stop clients from hanging,
  see `strip_telnet_negotiation()`). Each line of text typed on the Mac
  becomes one turn in a growing conversation.
- Per line received, the bridge POSTs the whole conversation-so-far to
  Google's `generativelanguage.googleapis.com` REST API (model
  `gemini-flash-latest`) using only Python's built-in `urllib` — **no Node.js,
  npm, or the `gemini` CLI are required**. (We started out shelling out to the
  official `gemini` CLI; it turned out to hang unpredictably for reasons
  unrelated to the API itself, so the bridge now calls the REST endpoint
  directly. Simpler and far more reliable.)
- The reply is converted to Mac Roman / CRLF line endings and written back
  down the same TCP connection.

## Requirements

- Python 3 (any recent 3.x — uses only the standard library: `json`, `os`,
  `socket`, `sys`, `threading`, `urllib`). No `pip install` needed.
- A Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
  (free tier available). This is **separate** from a Claude/Anthropic
  subscription — different company, different billing.
- Windows Firewall inbound rule allowing TCP port 6023 (see below) — without
  it, connections from the Mac silently fail with a "host or gateway not
  responding"-style error in BetterTelnet, even though everything else looks
  fine.

## One-time setup (fresh machine)

1. **Set the API key** (persists across reboots, PowerShell restarts, etc.):
   ```powershell
   setx GEMINI_API_KEY "your-key-here"
   ```
   Open a **new** terminal after this — `setx` doesn't affect the terminal
   you ran it in.

2. **Add the firewall rule** (needs an elevated/Administrator PowerShell):
   ```powershell
   New-NetFirewallRule -DisplayName "Gemini Mac SE Bridge" -Direction Inbound -Protocol TCP -LocalPort 6023 -Action Allow -Profile Any
   ```
   This was necessary because this machine's WiFi network is classified as
   "Public" in Windows' firewall profile, which blocks unsolicited inbound
   connections by default — even from other devices on the same LAN.

3. Done. No other install steps — just `python bridge.py` from here.

## Starting the bridge

Simplest (foreground, for testing — leave the window open):
```powershell
$env:GEMINI_API_KEY = [System.Environment]::GetEnvironmentVariable("GEMINI_API_KEY","User")
python "C:\Users\shepp\mac-se-gemini-bridge\bridge.py"
```

Detached background process (survives the launching terminal closing, but
**not** a logoff/reboot — see "Run persistently" below if you want that):
```powershell
$env:GEMINI_API_KEY = [System.Environment]::GetEnvironmentVariable("GEMINI_API_KEY","User")
$pythonExe = "C:\Users\shepp\AppData\Local\Programs\Python\Python313\python.exe"
$script    = "C:\Users\shepp\mac-se-gemini-bridge\bridge.py"
$logOut    = "C:\Users\shepp\mac-se-gemini-bridge\bridge.log"

Start-Process -FilePath $pythonExe -ArgumentList $script -WindowStyle Hidden `
  -RedirectStandardOutput $logOut -RedirectStandardError "$logOut.err" `
  -WorkingDirectory "C:\Users\shepp\mac-se-gemini-bridge"
```

**Why the explicit `$env:GEMINI_API_KEY = [System.Environment]::...` line?**
`setx` writes the variable to the registry for *future* processes, but any
PowerShell/terminal session that was already open when you ran `setx` won't
see it until it's re-read explicitly like this (or the terminal is
restarted). Skipping this step is the most common reason the bridge starts
but every request fails.

### Verify it's listening
```powershell
Get-NetTCPConnection -LocalPort 6023 | Select-Object LocalAddress, LocalPort, State
```
Should show `0.0.0.0:6023 Listen`.

### Stop it
```powershell
Get-NetTCPConnection -LocalPort 6023 -ErrorAction SilentlyContinue | ForEach-Object {
  Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
}
```

### Run persistently (survives reboot/logoff)
Not currently set up — the bridge only runs when someone starts it as above.
If you want it always-on, the clean way is a Scheduled Task set to run at
log-on:
```powershell
$action  = New-ScheduledTaskAction -Execute "C:\Users\shepp\AppData\Local\Programs\Python\Python313\python.exe" -Argument '"C:\Users\shepp\mac-se-gemini-bridge\bridge.py"' -WorkingDirectory "C:\Users\shepp\mac-se-gemini-bridge"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "MacSEGeminiBridge" -Action $action -Trigger $trigger -Description "Telnet-to-Gemini bridge for the Mac SE"
```
(Not yet done as of this writing — the bridge has only been run ad hoc so
far. `GEMINI_API_KEY` being a User-level env var set via `setx` should be
inherited fine by a logon-triggered task, since those run in the user's
session.)

## Connecting from the Mac SE

In BetterTelnet: new session, **Host = this PC's LAN IP** (currently
`192.168.1.158` — check with `(Get-NetIPAddress -AddressFamily IPv4
-InterfaceAlias "WiFi").IPAddress` if it may have changed), **Port = 6023**
(easy to miss — BetterTelnet's default is 23, and leaving it there gives a
misleading "host not responding" error since nothing's listening on 23).

Once connected: type a message, press Return, wait for the reply. Commands:
- `/reset` — clear conversation history for this connection
- `/quit` or `/exit` — disconnect

## Moving to a new machine

1. Copy the whole `mac-se-gemini-bridge` folder over.
2. Install Python 3 if not already present (nothing else to install).
3. `setx GEMINI_API_KEY "..."` with the same or a new key.
4. Add the firewall rule (command above) — **only needed if the new
   machine's network is also on a Public profile**; check with
   `Get-NetConnectionProfile`.
5. Start the bridge as above.
6. Update the Host IP in BetterTelnet on the Mac to the new machine's LAN IP.

## Files in this directory

| File | Purpose |
|---|---|
| `bridge.py` | The bridge server itself. Pure stdlib, no deps. |
| `bridge.log` / `bridge.log.err` | stdout/stderr when started via `Start-Process` as above. Empty is normal (the script's `print()` calls are line-buffered but infrequent). Not checked into git (see `.gitignore`) — regenerated on each run. |
| `BetterTelnet.bin` | MacBinary-packaged Telnet client for the SE. Works fine on System 7.1; crashes on this SE's System 6.0.8 (see `../SESSION_SUMMARY.md`). |
| `NCSA Telnet.bin` | MacBinary-packaged genuine NCSA Telnet 2.7b4 — the System 6.0.8-compatible alternative (bridge client + built-in FTP server). |

See [`../SESSION_SUMMARY.md`](../SESSION_SUMMARY.md) for the full narrative
and [`../tools/`](../tools/) for the reusable packaging scripts.

## Known system changes made on this PC (for troubleshooting FTP, not the bridge)

These were applied while diagnosing the NetPresenz FTP upload flakiness
(separate problem from the bridge, but done on the same machine, so noting
them here for completeness / in case anything needs reverting):

```powershell
netsh interface tcp set global autotuninglevel=disabled
netsh interface tcp set global timestamps=disabled
netsh interface tcp set global rss=disabled
```
Reversible with `autotuninglevel=normal`, `timestamps=default`,
`rss=enabled`. Helped intermittently but the real fix for FTP was switching
clients to **active mode** (the Mac's classic MacTCP stack — confirmed via
NetPresenz Setup's own Summary panel, "Open Transport is not installed" —
handles passive-mode FTP unreliably). Not required for the bridge itself,
which is unaffected by FTP mode.

## Security / cleanup notes (not yet done)

- NetPresenz's Guest/Anonymous FTP access was bumped to "Full" (read+write)
  to allow uploading files — worth reverting to read-only in NetPresenz
  Setup's FTP Setup window if the Mac is reachable beyond your own LAN.
- A folder's Finder sharing privileges were set to "Everyone: Make Changes"
  for the same reason — same consideration.
- A Gemini API key was pasted in plaintext into an earlier chat session by
  mistake — worth rotating at aistudio.google.com if that's a concern.
