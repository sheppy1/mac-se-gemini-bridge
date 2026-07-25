# Mac SE ↔ Gemini Telnet Bridge

Lets a real vintage Macintosh SE "chat with AI" by typing into a Telnet
client (BetterTelnet on System 7.1, NCSA Telnet on System 6.0.8), which
talks to a small Python bridge that calls the Gemini API over HTTPS and
relays the reply back. The bridge can run either on a Windows PC on the
same LAN, or — the current recommended setup — on an always-on Azure VM,
so nothing on the SE's home network needs to stay powered on.

For the full story of how this was built (StuffIt/AppleDouble archaeology,
NetPresenz FTP setup, the MacTCP passive-mode saga, the System 6.0.8 +
DaynaPORT saga, the Azure migration, etc.), see
[`../SESSION_SUMMARY.md`](../SESSION_SUMMARY.md). For a visual architecture
overview with diagrams, see [`../ARCHITECTURE.md`](../ARCHITECTURE.md). For
the packaging scripts used to build `BetterTelnet.bin`/`NCSA Telnet.bin`
(and `../netpresenz/NetPresenz.dsk[.bin]`), see [`../tools/`](../tools/).
This file is the "how to run/rebuild the bridge itself" reference.

## Architecture

```
 Macintosh SE                  Bridge host                   Internet
 (either OS side)              (Azure VM, or a Windows PC
                                 on the same LAN)
 BetterTelnet/         TCP     bridge.py                     Google
 NCSA Telnet     :6023 ──────▶ (Python, stdlib only)  HTTPS ──▶ Gemini API
                                listens on 0.0.0.0:6023        generateContent
```

- The SE runs a Telnet client and connects to the bridge host's IP on port
  6023 — either a Windows PC's LAN IP (same network only) or an Azure VM's
  public IP (reachable from anywhere the SE has internet access, which was
  confirmed via `MacTCP Ping` reaching `1.1.1.1` during the DaynaPORT setup).
- `bridge.py` is a plain TCP server (not a real Telnet daemon — it just
  answers enough Telnet IAC option-negotiation to stop clients from hanging,
  see `strip_telnet_negotiation()`). Each line of text typed on the Mac
  becomes one turn in a growing conversation.
- If `BRIDGE_PASSWORD` is set, the client is prompted for it before the
  chat banner (up to 3 attempts, constant-time comparison, a short delay
  between attempts). **Required** for any deployment reachable beyond a
  trusted LAN — see the Azure section below for why.
- Per line received, the bridge POSTs the whole conversation-so-far to
  Google's `generativelanguage.googleapis.com` REST API (model
  `gemini-flash-lite-latest`) using only Python's built-in `urllib` — **no Node.js,
  npm, or the `gemini` CLI are required**. (We started out shelling out to the
  official `gemini` CLI; it turned out to hang unpredictably for reasons
  unrelated to the API itself, so the bridge now calls the REST endpoint
  directly. Simpler and far more reliable.)
- The reply is converted to Mac Roman / CRLF line endings and written back
  down the same TCP connection.
- The bridge also exposes a small set of Gemini function-calling tools —
  list/delete files on the SE, search for and install classic Mac software —
  so natural-language requests like "delete X" or "get me a copy of ResEdit"
  can turn into real FTP operations. See "File management & software
  installation" below for what's actually supported and the safety model.

## Requirements

- Python 3 (any recent 3.x — uses only the standard library: `hmac`, `json`,
  `os`, `socket`, `sys`, `threading`, `time`, `urllib`). No `pip install`
  needed.
- A Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
  (free tier available). This is **separate** from a Claude/Anthropic
  subscription — different company, different billing.
- A firewall rule allowing inbound TCP port 6023 — on Windows this needs an
  explicit rule (see below); on the Azure VM this is an NSG rule (see the
  Azure section).
- If reachable beyond a trusted LAN: a `BRIDGE_PASSWORD` set to something
  random (see below) — without it, anyone who finds the IP/port can chat
  using your Gemini API quota.
- Optional, for file-management tools: `MAC_FTP_HOST`/`MAC_FTP_USER`/
  `MAC_FTP_PASS` (the SE's FTP credentials), `mac_ftp_lib.py` +
  `make_macbinary.py` + `extract_appledouble.py` + `parse_rsrc.py` from
  `../tools/` alongside `bridge.py`, and the `unar` command-line tool
  (`apt-get install unar` on the Azure VM) for the software-install
  pipeline. Leave `MAC_FTP_USER`/`MAC_FTP_PASS` unset to disable file
  management entirely — the tools still exist but report themselves as "not
  configured" rather than attempting a connection. See "File management &
  software installation" below.

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

**This local-Windows setup is now the fallback/dev option** — the
recommended way to run this is the Azure deployment below, which doesn't
need any machine on the SE's home network to stay on.

## Cloud deployment (Azure) — recommended

Runs `bridge.py` as a systemd service on a small always-on Ubuntu VM, so
the Mac SE can reach it from anywhere it has internet access, with no
Windows PC needed at all. Deployed via the `az` CLI (`winget install
Microsoft.AzureCLI`); an official Azure Claude Code plugin
(`claude plugin install azure@claude-plugins-official`) is also available
for MCP-based interaction, but requires a fresh session to pick up — `az`
CLI works immediately in any session.

### 1. Generate a bridge password

**Required** — this VM is reachable from the whole internet, not just your
LAN. Anything random works:
```powershell
python -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(20)))"
```

### 2. Provision the VM

```bash
az login   # interactive browser login; if it fails with an MFA/AADSTS50076
           # error, that's your tenant's conditional access policy — complete
           # the MFA challenge in the browser, not fixable from the CLI side

az group create --name mac-se-gemini-bridge-rg --location uksouth

az vm create \
  --resource-group mac-se-gemini-bridge-rg \
  --name mac-se-bridge-vm \
  --image Ubuntu2204 \
  --size Standard_B1ls \
  --admin-username azureuser \
  --generate-ssh-keys \
  --public-ip-sku Standard

az vm open-port --resource-group mac-se-gemini-bridge-rg --name mac-se-bridge-vm --port 6023 --priority 900
```
`Standard_B1ls` (1 vCPU, 0.5GB RAM, ~$3.80/month) is Azure's cheapest
tier — plenty for a stdlib-only Python socket server. Port 22/SSH is open
by default on VM creation; 6023 needs the explicit `open-port` call.

### 3. Deploy the bridge

```bash
VM_IP=$(az vm show -d -g mac-se-gemini-bridge-rg -n mac-se-bridge-vm --query publicIps -o tsv)

scp bridge.py azureuser@$VM_IP:~/bridge.py

# File-management/software-install tools -- only needed if MAC_FTP_USER/
# MAC_FTP_PASS will be set below. Deployed flat, alongside bridge.py, not
# in a tools/ subdirectory (bridge.py's sys.path handling covers this
# layout specifically -- see the comment at the top of bridge.py).
scp ../tools/mac_ftp_lib.py ../tools/make_macbinary.py \
    ../tools/extract_appledouble.py ../tools/parse_rsrc.py \
    azureuser@$VM_IP:~/

# unar is needed for the software-install pipeline (StuffIt/BinHex/Zip
# extraction) -- skip if MAC_FTP_USER/MAC_FTP_PASS won't be set.
ssh azureuser@$VM_IP sudo apt-get update -qq
ssh azureuser@$VM_IP sudo apt-get install -y unar

# bridge.env holds secrets — kept out of the systemd unit file itself so
# they're not visible via `systemctl cat` or the unit file's permissions
cat > /tmp/bridge.env <<EOF
GEMINI_API_KEY=your-key-here
BRIDGE_PASSWORD=the-password-from-step-1
MAC_FTP_HOST=192.168.1.210
MAC_FTP_USER=your-se-ftp-username
MAC_FTP_PASS=your-se-ftp-password
EOF
scp /tmp/bridge.env azureuser@$VM_IP:~/bridge.env

ssh azureuser@$VM_IP bash <<'REMOTE'
sudo mkdir -p /opt/mac-se-bridge
sudo cp /home/azureuser/bridge.py /opt/mac-se-bridge/bridge.py
sudo cp /home/azureuser/mac_ftp_lib.py /home/azureuser/make_macbinary.py \
        /home/azureuser/extract_appledouble.py /home/azureuser/parse_rsrc.py \
        /opt/mac-se-bridge/ 2>/dev/null || true
sudo cp /home/azureuser/bridge.env /opt/mac-se-bridge/bridge.env
sudo chmod 600 /opt/mac-se-bridge/bridge.env

sudo tee /etc/systemd/system/mac-se-bridge.service > /dev/null <<'UNIT'
[Unit]
Description=Mac SE Gemini Telnet Bridge
After=network.target

[Service]
Type=simple
EnvironmentFile=/opt/mac-se-bridge/bridge.env
ExecStart=/usr/bin/python3 /opt/mac-se-bridge/bridge.py
Restart=always
RestartSec=5
User=azureuser

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable mac-se-bridge.service
sudo systemctl start mac-se-bridge.service
sudo systemctl status mac-se-bridge.service --no-pager
REMOTE
```
`Restart=always` + `enable` means it survives both crashes and VM reboots
with no further action needed.

### 4. Auto-shutdown schedule (cost control)

```bash
az vm auto-shutdown -g mac-se-gemini-bridge-rg -n mac-se-bridge-vm --time 2300

# az vm auto-shutdown's --time is UTC-only with no timezone parameter --
# setting a fixed UTC offset would drift by an hour at every BST/GMT
# change. Fix: update the underlying resource directly to set a proper
# Windows-style timezone ID, so Azure handles DST automatically:
SCHEDULE_ID=$(az resource list --resource-group mac-se-gemini-bridge-rg \
  --resource-type microsoft.devtestlab/schedules --query "[0].id" -o tsv)
az resource update --ids "$SCHEDULE_ID" \
  --set properties.timeZoneId="GMT Standard Time" properties.dailyRecurrence.time="2300"
```
This only handles scheduled **shutdown** (11pm UK time in this example).
There's no Azure equivalent one-liner for scheduled **start** — the VM
can't wake itself while off, so auto-start at a fixed morning time needs a
separate always-on trigger (an Azure Automation Account with a scheduled
runbook calling `Start-AzVM`/`az vm start`). **Deferred as a stretch
goal** — manual start in the meantime:
```bash
az vm start -g mac-se-gemini-bridge-rg -n mac-se-bridge-vm
```

### Useful commands

```bash
# check the bridge's own logs
ssh azureuser@$VM_IP sudo journalctl -u mac-se-bridge.service -f

# restart after changing bridge.py or bridge.env
ssh azureuser@$VM_IP sudo systemctl restart mac-se-bridge.service

# stop billing entirely (destroys the VM disk too -- re-provision from
# scratch to bring it back; use `az vm deallocate` instead if you just
# want to pause without deleting anything)
az group delete --name mac-se-gemini-bridge-rg
```

## Connecting from the Mac SE

In BetterTelnet (System 7.1) or NCSA Telnet (System 6.0.8): new session,
**Host** = either the Azure VM's public IP (recommended — works from
anywhere) or a Windows PC's LAN IP if using the local fallback setup
(check with `(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias
"WiFi").IPAddress` if it may have changed), **Port = 6023** (easy to miss —
both clients default to port 23, and leaving it there gives a misleading
"host not responding" error since nothing's listening on 23).

If `BRIDGE_PASSWORD` is set, you'll be prompted for it immediately after
connecting, before the chat banner appears.

Once connected: type a message, press Return, wait for the reply. Commands:
- `/reset` — clear conversation history for this connection
- `/quit` or `/exit` — disconnect

## File management & software installation (Gemini tools)

The bridge gives Gemini four function-calling tools, backed by
[`../tools/mac_ftp_lib.py`](../tools/mac_ftp_lib.py):

| Tool | What it does | Confirmation required? |
|---|---|---|
| `list_files` | Lists a directory on the SE | No |
| `delete_file` | Deletes a file on the SE | **Yes** |
| `search_software` | Searches archive.org for classic Mac software, returns a shortlist | No |
| `download_and_install_software` | Downloads a specific result and installs it on the SE | **Yes** |

**Destructive actions always require an explicit "yes" typed at the SE's own
terminal** before they run, even after Gemini decides to do them — the
bridge describes exactly what it's about to do and waits for your
confirmation as a normal line of chat. This is deliberate: the bridge is
reachable from the whole internet (password-gated, but still), and a
misparsed request or a leaked password shouldn't be able to touch the SE's
filesystem without you explicitly saying yes in the moment.

**Not implemented: `make_folder`/`delete_folder`/`rename_or_move`.** Live
testing against this SE's actual FTP server (NCSA Telnet, System 6.0.8 —
System 7.1/NetPresenz is too unstable to run day-to-day, so this isn't a
temporary state) found that `MKD` and `RNFR`/`RNTO` **crash the server
outright** (confirmed twice, needed a physical restart both times), not
just a clean protocol rejection. `DELE` is at least rejected cleanly when
unsupported, so `delete_file` stays in the tool set — worst case it reports
"not supported" rather than doing anything. Given the bridge has no
reliable way to know which OS is currently booted before trying, mkdir/
rmdir/rename were dropped entirely rather than risk crashing the SE's FTP
server from a chat message.

**`search_software` only queries archive.org's public `advancedsearch.php`
API** — Macintosh Garden was considered too, but its site actively 403s
automated requests (confirmed while building this), so it isn't included.
Gemini is instructed to always search first, present the shortlist, and
wait for you to pick a specific result before ever calling
`download_and_install_software` — it's told never to guess a URL itself.

archive.org's full-text search has weak relevance for specific classic-
software titles on its own (a raw "MacPaint" search returns mostly
unrelated games/media dumps that just share a word). To fix this,
`search_software` makes a second, internal Gemini call after getting the
raw results — passing the original request and the candidate
titles/descriptions, asking it to judge genuine relevance (not just
keyword overlap) and return only real matches, via structured JSON output
(`generationConfig.responseMimeType`/`responseSchema`). If that filtering
call fails for any reason, it falls back to the unfiltered list rather
than breaking search entirely; if it succeeds but finds nothing genuinely
relevant, that's reported honestly rather than showing junk.

**The install pipeline** reuses this project's existing packaging tools:
download → detect format → `unar -forks visible` extraction for archives →
pull the real resource fork + Finder info (type/creator/flags) from the
AppleDouble sidecar → sanity-check the resource fork actually parses
(this project hit a real silent-corruption bug here once, with an
AppleDouble container misidentified as a raw resource fork) → build a
MacBinary `.bin` and upload it for NetPresenz/NCSA Telnet's auto-decode.
Disk images and plain files skip straight to upload. Any failure at any
step is reported back in plain language over the Telnet session rather
than swallowed or silently producing a broken file.

**Connectivity note**: none of this works yet from the Azure deployment —
`MAC_FTP_HOST` is the SE's private LAN IP, and Azure has no path to it
without a home-network relay (Raspberry Pi + Tailscale subnet router is the
planned approach; see `../SESSION_SUMMARY.md`). Until that's set up, file
management only works when running the bridge locally on a PC on the same
LAN as the SE.

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
