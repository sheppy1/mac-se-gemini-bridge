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
  `gemini-flash-latest`) using only Python's built-in `urllib` — **no Node.js,
  npm, or the `gemini` CLI are required**. (We started out shelling out to the
  official `gemini` CLI; it turned out to hang unpredictably for reasons
  unrelated to the API itself, so the bridge now calls the REST endpoint
  directly. Simpler and far more reliable.)
- The reply is converted to Mac Roman / CRLF line endings and written back
  down the same TCP connection.

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

# bridge.env holds secrets — kept out of the systemd unit file itself so
# they're not visible via `systemctl cat` or the unit file's permissions
cat > /tmp/bridge.env <<EOF
GEMINI_API_KEY=your-key-here
BRIDGE_PASSWORD=the-password-from-step-1
EOF
scp /tmp/bridge.env azureuser@$VM_IP:~/bridge.env

ssh azureuser@$VM_IP bash <<'REMOTE'
sudo mkdir -p /opt/mac-se-bridge
sudo cp /home/azureuser/bridge.py /opt/mac-se-bridge/bridge.py
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
