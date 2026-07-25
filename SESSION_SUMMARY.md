# Mac SE Retro Project — Session Summary

Goal: get a real 1987 Macintosh SE (System 7.1, BlueSCSI SD-card storage) running
NetPresenz as an FTP server, then extend it with a Gemini/Claude AI chat bridge
over Telnet.

## ⏸ CHECKPOINT — Phase 2 (file management tools) mostly built

**Phase 2 is implemented and locally verified, but not yet live on Azure.**
Full narrative in "Phase 2: Gemini-driven file management" near the end of
this file. Short version:

- `tools/mac_ftp.py` refactored into `tools/mac_ftp_lib.py` (reusable
  functions) + a thin CLI wrapper, no behavior change for existing manual
  use.
- Live-tested the previously-untested operations against the real SE. Big
  finding: the SE is now stuck on **System 6.0.8** day-to-day (7.1/
  NetPresenz is too flaky to run) and its NCSA Telnet FTP server only
  implements `LIST`/`RETR`/`STOR` — `DELE` is cleanly rejected, but `MKD`
  and `RNFR`/`RNTO` **crash the server outright** (confirmed twice, each
  needing a physical restart). So `mkdir`/`rmdir`/`rename` are **not**
  exposed as Gemini tools at all — too risky given the bridge can't know
  which OS is booted before trying.
- `bridge.py` now does real Gemini function-calling: `list_files`,
  `delete_file` (degrades gracefully — currently always reports
  "unsupported" given the 6.0.8 reality above), `search_software`
  (archive.org only — Macintosh Garden 403s automated requests),
  `download_and_install_software` (reuses the existing packaging
  pipeline). Destructive tools always pause for an explicit "yes" typed at
  the SE's terminal before executing — verified via the bridge's own
  connection log that a decline never reaches the SE's FTP server at all.
- Verified end-to-end against the **local Windows fallback** bridge with a
  headless test client. Found and fixed a real bug during this: `bridge.py`
  copied into the local working copy has `tools/` as a *child* directory
  (`mac-se-gemini-bridge\bridge.py` + `mac-se-gemini-bridge\tools\`), a
  third layout the original two-candidate `sys.path` logic didn't cover
  (only handled the flat-Azure-deploy and repo `bridge/`+`../tools`
  layouts) — silently failed to import `mac_ftp_lib`, producing a
  misleading "not configured" message that looked like a credentials
  problem. Fixed by adding the third candidate path.

**Not yet done**:
- **Not deployed to Azure** — needs `mac_ftp_lib.py` + its dependencies
  copied alongside `bridge.py` on the VM, `apt-get install unar`, and
  `MAC_FTP_HOST`/`USER`/`PASS` set in `bridge.env` (see
  `bridge/README.md`'s updated deploy steps).
- **Azure still can't reach the SE at all** even once deployed — it's a
  private LAN IP behind the home router. Decided approach: a Raspberry Pi
  4 (1GB) running Tailscale as a subnet router, joining the existing home
  LAN unchanged (no SE reconfiguration, no exposing the vintage FTP server
  to the internet). Not physically set up yet — this is the next concrete
  step before file management works from anywhere but the LAN.
- `download_and_install_software` implemented but not live-tested (needs
  `unar` on a Linux host — Windows doesn't have it readily available;
  deferred to testing on the Azure VM once deployed).

**Phase 3** (scheduled VM auto-shutdown/auto-start) has been dropped
entirely, not just deferred — user decision, the VM's running cost is
minimal enough against the free Visual Studio subscription credit that
scheduling isn't worth the complexity. See "Outstanding TODO" for detail.

## Status: working end-to-end, now on two OSes

The Mac SE dual-boots **System 7.1** (NetPresenz FTP server, BetterTelnet,
Gemini bridge — all working) and, as of this session, **System 6.0.8** too
(networking via BlueSCSI's WiFi DaynaPORT emulation, also now working, plus
NCSA Telnet 2.7b4 with its built-in FTP server as the System-6-compatible
alternative to NetPresenz, which cannot run on System 6 at all — BetterTelnet
itself crashes under 6.0.8 on this SE, see "System 6.0.8 + DaynaPORT"
section below for the full story and why).
See "Post-summary developments" and the new System 6.0.8 section further
down for how everything below this line got resolved, and `README.md` for
the setup/operations reference.

## Loose ends still worth tidying up

- **Windows TCP stack settings were changed and left changed:**
  `autotuninglevel=disabled`, `timestamps=disabled`, `rss=disabled` (global,
  via `netsh`). Reversible with `autotuninglevel=normal`,
  `timestamps=default`, `rss=enabled` — probably fine to leave as-is, but
  noting it since it's a systemwide change made mid-troubleshooting.
- **Security loose ends:** anonymous/guest FTP access on the Mac is currently
  "Full" (read+write), and a folder's Finder sharing privileges are set to
  "Everyone: Make Changes" — both worth reverting to read-only in NetPresenz
  Setup / the Finder if the Mac is reachable beyond your own LAN. A live
  Gemini API key was also pasted into a chat session in plaintext and should
  be regenerated at aistudio.google.com.
- **`bridge.py` doesn't yet run persistently across reboots/logoff** — see
  README's "Run persistently" section for the Scheduled Task approach
  (documented but not yet applied).
- **`mac_ftp.py` is only partially tested** (`ls`/`mkdir`/`put` confirmed,
  `get`/`rm`/`rmdir`/`rename`/`put-app` not yet exercised) — see its section
  below before hammering the SE with it again.
- **A GitHub personal access token was pasted into a chat session in
  plaintext** to authenticate `gh` — it had far broader scopes than needed
  (`admin:org`, `delete_repo`, `admin:enterprise`, etc.) and should be
  regenerated with minimal (`repo`-only) scope at
  github.com/settings/tokens.

## 1. Extracting NetPresenz from the original StuffIt archive

- Source: `C:\Users\shepp\Downloads\NetPresenz 4.1\netpresenz-41.sit` (classic
  StuffIt 1.5.1 `SIT!` format from 1997) — modern StuffIt Expander on the Mac
  couldn't open it.
- Installed `unar`/`lsar` (The Unarchiver CLI) inside WSL Ubuntu — a real,
  well-tested open-source implementation, in preference to an unverified
  "Unarchiver" clone found on the Microsoft Store (installed then removed it).
- Extracted cleanly: 229 files verified OK. Output at
  `NetPresenz 4.1\extracted\netpresenz-41\`.

## 2. Building a Mac-native disk image (multiple iterations)

Needed a `.dsk` (Disk Copy 4.2 format) containing the NetPresenz app with a
real HFS resource fork, mountable via "Mount Image" on the SE and transferable
via a BlueSCSI SD card (which only exposes a plain FAT volume — no Mac
metadata support).

Toolchain, all run inside a Debian container (via Docker Desktop, to avoid
needing `sudo` on the host WSL):
- `hfsutils` (hformat/hmount/hcopy) — builds a real 800K HFS volume.
- Custom Python **MacBinary II encoder** (`make_macbinary.py`) — hand-verified
  byte-for-byte against hfsutils' actual C source (`copyin.c`, `crc.c`) so
  `hcopy -m` would accept it, since MacBinary requires an exact CRC-16/CCITT
  header checksum.
- Custom Python **Disk Copy 4.2 wrapper** (`make_dc42.py`) — header layout and
  checksum algorithm (32-bit rotate-right accumulator) verified against the
  CiderPress2/DiscFerret format docs and independently against a compiled
  copy of the open-source `undiskcopy` tool.

**Bug found and fixed:** `unar -forks visible` doesn't write a bare resource
fork to the `.rsrc` sidecar file — it wraps it in an **AppleDouble container**
(magic `00 05 16 07`). The first build mistakenly treated the whole container
as the raw resource fork, corrupting every app by prefixing ~82 bytes of
AppleDouble header before the real resource data. This caused Finder to show
a generic icon and fail to launch with error **-50 (paramErr)** — the Resource
Manager couldn't find `SIZE`/`BNDL`/`CODE` resources. Fixed by writing a proper
AppleDouble parser (`extract_appledouble.py`) to pull out the real Finder Info
(entry 9) and resource fork (entry 2), then rebuilding. Verified via SHA-256
round-trip hashing (extract-back-out-of-the-volume vs. original) and by
parsing the resulting resource fork structure to confirm sane header offsets
and the presence of a real `SIZE` resource.

**Transfer to the Mac:** Since BlueSCSI's SD card is plain FAT (no Mac
metadata support), the final `.dsk` itself also needed wrapping in MacBinary
(type `dImg`, creator `dCpy` — confirmed via research, not guessed) so its
type/creator would survive the FAT hop and be restored by StuffIt Expander on
the Mac side.

Final working files: `NetPresenz.dsk` and `NetPresenz.dsk.bin` in
`NetPresenz 4.1\`.

## 3. Getting it running

- User copied `NetPresenz.dsk.bin` via BlueSCSI's shared FAT volume, unpacked
  it with StuffIt Expander (confirmed MacBinary decoding works fine even
  though the original SIT-compression-based archive didn't), mounted the
  `.dsk` with "Mount Image", and it **worked**.
- Configured via **NetPresenz Setup** (FTP access levels, permissions) per
  its own documentation, then launched **NetPresenz** (the actual server).
- Set to auto-start: alias of `NetPresenz` placed in
  `System Folder → Startup Items`.
- Verified end-to-end from this PC: connected via `curl ftp://192.168.1.210/`,
  anonymous login succeeded, pulled a real directory listing of the Mac's
  System 7.1 boot volume. Confirmed working FTP server on real vintage
  hardware.

## 4. Gemini CLI terminal bridge (in progress)

Goal: let the SE "chat with AI" via a Telnet client talking to a local bridge
that calls the Gemini API (originally scoped for Claude API, switched to
Gemini CLI per user request).

- Installed Node.js (winget) + `@google/gemini-cli` (npm).
- Gemini CLI's free "Login with Google" OAuth flow is deprecated
  (`IneligibleTierError` — Google is pushing users to "Antigravity"). Switched
  to API-key auth instead (`aistudio.google.com` key), and had to manually fix
  `C:\Users\shepp\.gemini\settings.json` (`selectedType` was stuck on
  `oauth-personal` from the failed login attempt) to `gemini-api-key`.
- Verified `gemini -p "..."` works headlessly.
- Wrote `bridge.py` (`C:\Users\shepp\mac-se-gemini-bridge\bridge.py`): a plain
  TCP server (port 6023) that speaks just enough Telnet IAC negotiation to
  satisfy a real client, reads lines, maintains a growing conversation
  transcript per connection, and shells out to `gemini -p` for each turn.
  Tested locally end-to-end successfully.
- **Not yet running persistently** — starting it as a long-lived background
  process got blocked by the permission classifier (opening a network
  listener with an API key in the environment). Still need to decide: run it
  as a proper Windows scheduled task/service, or have the user start/leave it
  running themselves in a terminal.
- Still need NCSA-Telnet-or-equivalent on the Mac side to actually connect to
  the bridge — see below.

## 5. Sourcing a Telnet client for the SE

- Couldn't get a clean scriptable download of NCSA Telnet itself (gated
  behind JS/anti-bot pages on Macintosh Repository, Higher Intellect, and
  Cloudflare-protected info-mac mirrors).
- Settled on **BetterTelnet** (`tucows_206669_BetterTelnet` on archive.org,
  direct non-gated download) — a well-regarded freeware successor built on
  the final NCSA Telnet codebase. Confirmed via its own internal strings
  ("Telnet requires at least System version 6.0", "Telnet requires at least
  128k ROMS") that it has no 68020+ requirement, so it should run on the SE's
  68000.
- Extracted with the same unar → AppleDouble-parsing pipeline as NetPresenz;
  independently sanity-checked the resulting resource fork (sane offsets,
  valid `SIZE`/`CODE`/`BNDL` resources) before packaging.
- Built `BetterTelnet.bin` (MacBinary, type `APPL`/creator `rlfT`) at
  `C:\Users\shepp\mac-se-gemini-bridge\BetterTelnet.bin`, ready to upload.

## 6. FTP upload troubleshooting (unresolved)

Plan was to use NetPresenz's documented feature that automatically decodes
incoming `.bin` (MacBinary) uploads server-side — no StuffIt Expander step
needed on the Mac.

Ran into serious connection instability:
- Anonymous FTP is read-only by design; user enabled "Everyone: Make Changes"
  sharing on a target folder to allow writes.
- Repeated intermittent failures: sometimes TCP wouldn't even connect,
  sometimes login would hang, sometimes it got through halfway. Diagnosed
  (partially) as a Windows TCP stack compatibility issue with the SE's old
  MacTCP/OpenTransport stack — disabled TCP window auto-tuning, RFC1323
  timestamps, and RSS via `netsh` (all reversible; re-enable with
  `autotuninglevel=normal`, `timestamps=default`, `rss=enabled` if needed).
  This helped intermittently but did not fully fix it.
- Wrote a 40-attempt automated retry loop (`upload_retry.sh`) — **this made
  things worse**: it left many abandoned/aborted connections on the Mac's
  side, and the SE eventually started throwing OpenTransport error
  **`-23016` (connectionDoesntExist)** — a strong sign the rapid retries were
  destabilizing the old machine's TCP stack rather than working around
  flakiness.

**Current recommendation (where we left off):** stop automated retries
entirely. Quit and relaunch NetPresenz on the Mac to clear its connection
state (lighter than a full reboot), let it sit idle a minute, then have the
user upload `BetterTelnet.bin` manually via WinSCP (which had already worked
for them once) rather than continuing to script it with `curl`.

**Update — root cause substantially identified:** the Mac is running classic
**MacTCP** (confirmed via NetPresenz Setup's own Summary panel — "Open
Transport is not installed"), not Open Transport. MacTCP is the older, less
robust of the two stacks. Per a classic-Mac-networking reference guide
(applefool.com/se30), active vs. passive FTP mode makes a real difference
with old stacks like this. Tested and **confirmed**: a `curl --ftp-port -`
(active mode) request for a directory listing completed successfully and
reliably, where the default passive-mode requests had been hanging/timing
out. A subsequent active-mode *upload* attempt still failed at the TCP
connect stage, so the underlying link also still has some raw intermittent
flakiness beyond just the passive-mode issue — but active mode is a
confirmed, meaningful improvement and should be used for all further
attempts (including in WinSCP: set its FTP transfer mode to Active).

Also confirmed via on-device screenshots that **permissions are not the
problem**: FTP Setup shows Guests at "Full" access with Remote Mounting
enabled, the FTP Users window shows the guest login directory correctly set,
and the volume's Finder sharing dialog shows Everyone with See
Folders/Files/Make Changes all checked. So any remaining failures are
connection-reliability issues (MacTCP quirks / possible physical link
flakiness), not misconfiguration.

## Outstanding TODO

Everything from the original scope is done except the loose ends listed
above ("Loose ends still worth tidying up") — persistent-run setup and the
security reverts.

### Phase 2 (mostly done): give the Gemini bridge SE filesystem access

**Implemented and locally verified** — see "Phase 2: Gemini-driven file
management" near the end of this file for the full narrative. Summary of
what's actually shipped vs. the original idea below (kept for context):

- ✅ `list_files`, `delete_file`, `search_software`,
  `download_and_install_software` implemented as real Gemini
  function-calling tools in `bridge.py`, backed by the new
  `tools/mac_ftp_lib.py`.
- ✅ Destructive-action confirmation gate ("yes" required at the SE's
  terminal before anything runs).
- ❌ `mkdir`/`rmdir`/`rename` — **deliberately not implemented**, not just
  deferred. Live testing found these crash the SE's current FTP server
  (NCSA Telnet, System 6.0.8) outright, not just get rejected.
- ⏳ Not yet deployed to Azure, and Azure still has no network path to the
  SE (Raspberry Pi + Tailscale relay planned, not built) — file management
  currently only works via the local Windows fallback bridge.

Original rough shape (steps 1-3 below are done; step 4 answered — yes,
confirmation required, implemented):

1. ~~Define each `mac_ftp.py` operation as a Gemini function-calling tool
   schema~~ — done, scoped down to the 4 tools above after live-testing
   revealed the mkdir/rename crash risk.
2. ~~In `bridge.py`'s request loop, pass the tool schemas alongside the
   conversation...~~ — done, see `call_gemini_with_tools()`.
3. ~~Finish testing `mac_ftp.py`'s untested operations first~~ — done, see
   the Phase 2 narrative section for exactly what was found.
4. ~~Worth deciding guardrails up front~~ — yes: confirmation required for
   all destructive tools, plus a `PROTECTED_PATH_PREFIXES` check so nothing
   can touch the System Folder even with confirmation.

### Phase 3 (dropped): scheduled auto-shutdown/auto-start for the Azure VM

Was a stretch goal (11pm shutdown / 8am start) to control costs. Auto-
shutdown was actually built (DST-aware, via a direct `az resource update`
on the `microsoft.devtestlab/schedules` resource) but then explicitly
disabled, and auto-start was never built (no Azure one-liner exists for
it — would've needed an Automation Account + scheduled runbook). **User
decision: skip this entirely** — the VM's running cost is minimal enough
against the Visual Studio subscription's free credit that scheduling isn't
worth the added complexity. The VM just runs continuously now. Manual
control still available if ever wanted: `az vm start`/`az vm deallocate
-g mac-se-gemini-bridge-rg -n mac-se-bridge-vm`.

## Key files

| File | Purpose |
|---|---|
| `NetPresenz 4.1\extracted\...` | Extracted original archive contents |
| `NetPresenz 4.1\NetPresenz.dsk` | Disk Copy 4.2 image, NetPresenz app (correct build) |
| `NetPresenz 4.1\NetPresenz.dsk.bin` | Same, MacBinary-wrapped for FAT transfer |
| `mac-se-gemini-bridge\bridge.py` | Telnet-to-Gemini bridge server (now calls the Gemini REST API directly — no Node/CLI dependency, see below) |
| `mac-se-gemini-bridge\BetterTelnet.bin` | Telnet client already uploaded and running on the SE |
| `mac-se-gemini-bridge\README.md` | **Setup/operations reference** — firewall rule, PowerShell commands, how to start/stop/move the bridge |
| `mac-se-gemini-bridge\tools\` | Reusable packaging scripts (MacBinary encoder, AppleDouble parser, Disk Copy 4.2 wrapper, resource-fork sanity checker, plus `mac_ftp.py` — see below) |
| `mac-se-gemini-bridge\tools\mac_ftp.py` | FTP-based remote filesystem CLI for the SE (active-mode, retry-safe) — partially tested, see its section below |

Scratch/investigation files from the session (downloaded archives, failed
download attempts, retry logs) have been cleaned up — see `README.md` for
what's actually needed to run or rebuild this.

## Post-summary developments

Everything below happened after this summary was first written and isn't
reflected in the numbered sections above:

- **Root cause of the FTP flakiness confirmed**: classic MacTCP (not Open
  Transport — the SE's 68000 CPU is below OT's official 68030 minimum, so
  this can't be upgraded) handles passive-mode FTP unreliably. Switching to
  **active mode** fixed it. Permissions were independently confirmed correct
  via on-device screenshots (FTP Setup, FTP Users, Finder sharing dialog all
  showed proper Full/Make-Changes access) — the problem was never
  misconfiguration.
- **`BetterTelnet.bin` uploaded successfully** (via Cyberduck, slow/flaky but
  it got there) and launched on the SE.
- **The Gemini CLI approach was abandoned.** `gemini -p` turned out to hang
  unpredictably (confirmed independent of the bridge — direct CLI
  invocations hung too, while the raw REST API responded in ~1-2s every
  time). `bridge.py` was rewritten to call
  `generativelanguage.googleapis.com` directly via `urllib`, removing the
  Node.js/npm/`gemini` CLI dependency entirely.
- **Windows Firewall was blocking the Mac's connection to the bridge** — this
  network's WiFi profile is "Public," which blocks unsolicited inbound
  connections by default. Fixed with an explicit `New-NetFirewallRule`
  (admin PowerShell) — see `README.md`.
- **First live connection failed** for a mundane reason: BetterTelnet was
  still pointed at its default port 23 instead of 6023. Once corrected, the
  end-to-end chat worked.
- **Working end-to-end as of now**: Mac SE → BetterTelnet → bridge.py (this
  PC) → Gemini API → back. Bridge is not yet set up to run persistently
  across reboots (see README's "Run persistently" section for the
  not-yet-applied Scheduled Task approach).

## `mac_ftp.py` — an FTP-based remote filesystem tool for the SE

Added `tools/mac_ftp.py`: a CLI wrapping Python's `ftplib`, purpose-built
around everything learned about this specific NetPresenz/MacTCP setup —
forces **active-mode FTP** (required, see the FTP troubleshooting section
above), and reconnects with a **fresh connection on every retry** rather
than retrying on a stale one (a real bug found and fixed during testing:
retrying on the same connection after a timeout left it in an unrecoverable
"cannot read from timed out object" state, even though the server side was
fine). Supports `ls`, `get`/`put` (plain files), `put-app` (builds a
MacBinary on the fly via `make_macbinary.py` and uploads as `.bin` for
NetPresenz's automatic server-side decode), `rm`, `mkdir`, `rmdir`,
`rename`.

**Testing status**: `ls`, `mkdir`, and `put` all confirmed working live
against the SE. `get`, `rm`, `rmdir`, `rename`, and `put-app` are
implemented but not yet exercised — testing was paused mid-way after
NetPresenz crashed with an Address Error, traced to sheer connection volume
(10+ full connect/login cycles in quick succession against 1997-era
software on a maxed-out 4MB machine) rather than a bug in the tool itself.
Lesson: this hardware cannot absorb rapid automated retries the way a
modern server can — go easy on it, one operation at a time, when resuming
this testing.

## System 6.0.8 + DaynaPORT: a whole separate saga

Motivation: System 6.0.8 is much lighter than 7.1 and noticeably snappier
on this 4MB machine; general performance, not networking specifically, was
the main driver (BlueSCSI's WiFi DaynaPORT emulation has a fixed ~60KB/sec
throughput ceiling regardless of OS version, per BlueSCSI's own docs, so
switching OS was never going to speed up the network itself).

**Setup, per BlueSCSI's own docs** (`bluescsi.com/docs/WiFi-DaynaPORT`):
needs MacTCP 2.1 specifically (not just "MacTCP") and the "DaynaPORT 7.5.3"
driver package (a version *label*, not an actual System 7.5.3 requirement —
same naming trap as below). BlueSCSI mounts every configured SCSI ID
simultaneously regardless of which one you booted from, which matters a lot
below.

### The installer size bug

The DaynaPORT installer kept failing with bogus "not enough space" errors
(e.g. "76k available, you will need 745k") on volumes that were actually
nearly empty (confirmed via Get Info — a fresh 264MB volume showing ~38MB
genuinely free was reported as having 257KB by the installer, and even a
**plain Finder file copy** hit the same wrong numbers). This is a
now-fixed-in-hindsight class of bug: 1990s Mac installers/Finder-adjacent
tools using undersized integer arithmetic for free-space calculations,
which breaks down well before the notorious 2GB ceiling — anywhere from
roughly 85MB up, depending on the specific software. (A related, distinct,
well-documented 1993 HFS bug affects volumes specifically in the 85–95MB
range at 1.5K allocation blocks — different mechanism, same causal
category: old arithmetic, small volumes.)

Ruled out one by one before finding the real fix:
- Hiding other large mounted volumes (HD00 at 2GB, later HD30 Bootstrap at
  536MB) — no effect.
- Disk First Aid — reported the volume as structurally clean (no repairs
  needed), so it wasn't catalog/extents corruption.
- Hiding the `NE4.hda` WiFi DaynaPORT device placeholder — no effect (rules
  out a suspected class of BlueSCSI firmware bug, documented in a
  TinkerDifferent thread, where disk-specific config bleeds into the
  network device's emulation).
- Confirmed via research this is a recognized *class* of problem in the
  BlueSCSI/DaynaPORT community (see e.g.
  `tinkerdifferent.com/threads/bluescsi-in-quadra-annihilating-system-folder-daynaport.3383`
  and a ZuluSCSI firmware discussion about disk config leaking into network
  device emulation) — never fully root-caused even by the people who hit it.

**What actually worked**: install DaynaPORT onto a target volume that
already has a valid System Folder on it (the installer requires this as a
precondition — it won't target a blank data volume), sized in a safe range
(80MB — comfortably clear of both the 85–95MB historical danger zone and
the multi-hundred-MB+ zone where the bogus-space bug reappeared), and
**freshly reformatted from scratch** rather than reusing/resizing an
existing volume, since a corrupted/stale free-space cache from however the
volume was originally created (not caught by Disk First Aid) was the
real, if never 100%-pinned-down, root cause.

Formatting tool compatibility turned out to matter a lot:
- **Drive Setup** (1.5 and 1.7.3): System 7-only, doesn't run on System 6
  at all.
- **HD SC Setup 7.3.5**: the "7.3.5" is a version *label*, not an OS
  requirement (this is Apple's actual System-6-era SCSI utility) — but it
  failed with "unable to mount volume" after seemingly writing the
  partition/driver, on this specific BlueSCSI setup.
- **LIDO7**: successfully wrote the partition map, but then initializing
  failed with "not enough memory for driver, try booting from floppy" — a
  genuine low-RAM issue (LIDO's driver-install step needs more free RAM
  than a loaded System 6.0.8 session had available on this 4MB machine).
- **Silverlining 5.6.3**: worked. Asked for a generic/manual drive-model
  selection (no real physical drive to match an emulated device against),
  then successfully partitioned and initialized the volume in its Volume
  Manager screen. This is now the recommended tool for this setup.
  (Its driver displays its own splash icon during boot, before the Finder
  loads — normal/expected behavior for any third-party SCSI driver, not a
  bug, and not worth risking the working setup to remove.)

Once formatted fresh with Silverlining, the DaynaPORT Custom install
(Customize → **SCSI/Link only**, never Easy Install — Easy Install tries to
overwrite newer Network software and errors out) completed successfully on
the first real attempt.

**Getting the driver files without mounting the whole Bootstrap image**:
the 536MB `HD30_BlueSCSI Bootstrap.hda` volume being mounted was an early
suspect for the size bug (later ruled out, but avoided anyway out of an
abundance of caution). Rather than mount it as a live SCSI device, its HFS
contents were read directly with `hfsutils` (same Docker-based approach
used throughout this project) and the four needed files extracted as
MacBinary straight into the SD card's `shared` folder (BlueSCSI's
plain-FAT drop-folder, readable directly from the Mac without needing a
SCSI mount):
`BlueSCSI PicoW Setup:DaynaPORT:DaynaPORT 7.5.3-DiskCopy4.img`,
`BlueSCSI PicoW Setup:MacTCP Setup:MacTCP`,
`BlueSCSI PicoW Setup:MacTCP Setup:MacTCP Ping`, and
`Stuff:Images:System 6:MountImage 1.2b2` (the last one needed specifically
because System 6, unlike 7.x, can't run an installer from inside a mounted
disk image without this cdev to mount it in the first place). Note:
BlueSCSI's shared-folder transfer only supports individual files, not
folders — everything has to sit loose at the folder root.

**Final gotcha**: after a successful install and restart, MacTCP still only
showed LocalTalk, no Ethernet option. Cause: the System Folder copied over
from backup already had an old **MacTCP 2.0.6** in it, and the required
**MacTCP 2.1** (extracted from the bootstrap image, sitting unused in
`shared`) had never actually been installed over it. Once 2.1 replaced
2.0.6 and the Mac was restarted again, "Ethernet Built-In" appeared, MacTCP
was configured (subnet `255.255.255.0`, router, DNS `1.1.1.1`), and MacTCP
Ping confirmed working connectivity. Note: the static IP ended up as
`192.168.1.210` — same address the 7.1 side uses, carried over from the
copied System Folder's MacTCP prefs. Not a real conflict since the SE only
runs one OS at a time (dual-boot, not simultaneous), but worth remembering
when connecting to it: whichever OS is actually booted owns that address.

### NetPresenz cannot run on System 6 — important, easy to forget

Spent some effort trying to get `NetPresenz.dsk.bin` (built for the 7.1
side) working on 6.0.8 via StuffIt Expander before remembering: NetPresenz's
own documentation states it **requires System 7** specifically, because it
depends entirely on System 7's Personal File Sharing for authentication and
file access — a feature that doesn't exist in System 6 at all. No amount of
successful file transfer would have made it run. This isn't fixable; don't
attempt it again for a System 6 target.

**First attempt — BetterTelnet's built-in FTP server**: it has one,
inherited directly from its NCSA Telnet lineage — confirmed via strings
search of its resource fork (`"220 Macintosh Resident FTP server, ready"`,
dedicated `FTP Server Prefs` / `FTP Server` menu items). `BetterTelnet.bin`
was copied into the SD card's `shared` folder (not previously there — the
7.1 copy was installed via direct FTP upload, which 6.0.8 had no equivalent
path for yet) and installed the same way as the DaynaPORT files.

**BetterTelnet crashed on the stock Mac SE under 6.0.8**, though — launches
fine, but the cursor never renders, and clicking anything eventually
crashes with a **"coprocessor not installed"** error and a restart. Same
exact binary works fine on the 7.1 side of the same physical machine, so
it's not a raw CPU-instruction incompatibility (the 68000 hardware is
identical either way) — the leading theory is Color QuickDraw: BetterTelnet's
resource fork has 3 color-cursor (`crsr`) resources, and a stock Mac SE's
ROM has no Color QuickDraw support at all (it predates any color-capable
Mac hardware). Something in a failed `SetCCursor`-style call likely cascades
into the coprocessor trap. Not investigated further/fixed — moved to
sourcing the real, older program instead.

**Second attempt — genuine NCSA Telnet — worked.** Sourcing it was its own
small detour: every direct download source (Macintosh Repository, Higher
Intellect/preterhuman.net, archive.info-mac.org) was gated behind
JS/anti-bot pages or Cloudflare blocking plain `curl`. Fix: the **Wayback
Machine bypasses this cleanly** — `web.archive.org`'s CDX API
(`web.archive.org/cdx/search/cdx?url=...`) can locate old snapshots of a
known blocked URL, and fetching the snapshot URL directly returns the
original file with a clean `200`, no bot-gating at all, since it's served
from Archive.org's own infrastructure rather than the live (blocked) site.
Got **NCSA Telnet 2.7b4** this way (a compiled binary of the true 2.6 was
not available in the same archive, only source code — 2.7b4 was the
closest real compiled option). It *also* has 3 `crsr` (color cursor)
resources in its resource fork, so the "avoid Color QuickDraw" theory isn't
airtight — but it was tested directly rather than over-analyzed further,
and it **launched and ran with no crash**, cursor and all. Same
extraction/repackaging pipeline as everything else in this project
(`unar` → `extract_appledouble.py` → `parse_rsrc.py` sanity check →
`make_macbinary.py`, type `APPL` creator `NCSA`), dropped into `shared`,
installed via StuffIt Expander.

FTP server setup in NCSA Telnet: Edit menu → Preferences → **FTP Users**
(set a username/password/directory — used `mac`/`admin` this session) →
**FTP Server** to toggle it on. Unlike NetPresenz, this needs **no Mac OS
Sharing Setup / File Sharing at all** — the FTP server is fully
self-contained inside the app, managing its own user list and speaking FTP
protocol directly, which is exactly why it can run on System 6 in the
first place (NetPresenz can't, precisely because it depends on File
Sharing). Tested and confirmed working end-to-end from this PC (active
mode, same as the 7.1/NetPresenz setup) — clean login, directory listing
returned (`Apps/`, `System Folder/`).

Both OS environments on the SE now have working FTP servers: **NetPresenz**
on 7.1, **NCSA Telnet's built-in server** on 6.0.8. Note both currently
report the same IP (`192.168.1.210`) since they're not running
simultaneously — see the note in the DaynaPORT section above.

NCSA Telnet was also confirmed working as the 6.0.8-side Gemini bridge
client (same `bridge.py`, host `192.168.1.158` port `6023`, no server-side
changes needed — the bridge is client-agnostic). **Both OS environments on
the SE are now fully working end-to-end**: FTP serving and Gemini chat, on
System 7.1 and System 6.0.8 alike.

## Moving the bridge to Azure

Goal: get `bridge.py` off the Windows laptop entirely and onto an
always-on cloud VM, so the Mac SE can chat with Gemini without needing the
laptop powered on. Feasible because the SE already had proven real
internet connectivity (not just LAN) — `MacTCP Ping` had already
successfully reached `1.1.1.1` during the DaynaPORT setup.

**Added password authentication to `bridge.py` first**, since this was
about to go from "only reachable on my home LAN" to "reachable by anyone
who finds the IP/port on the public internet." Without it, a stranger
could freely burn the owner's Gemini API quota. Implementation: an
optional `BRIDGE_PASSWORD` env var (unset = no auth, for local-LAN-only
use); if set, the client is prompted for it before the normal chat banner,
up to 3 attempts, constant-time comparison (`hmac.compare_digest`) to
avoid trivial timing attacks, a short delay between attempts to slow down
brute-forcing. Required refactoring the line-reading loop into a shared
`iter_lines()` generator so the same Telnet-IAC-aware line reader could be
used both for the password prompt and the main chat loop. Tested locally
(wrong password rejected, correct password proceeds, full chat still
works) before deploying anywhere.

**Azure setup** (subscription: Visual Studio Enterprise Subscription,
via `az` CLI — the Azure Claude Code plugin was also installed for MCP-based
interaction in future sessions, but wasn't live in *this* session, which
needed a restart to pick up; `az` CLI was used instead since it works
immediately):

1. `az login` initially failed with an MFA/conditional-access error
   (`AADSTS50076`) against two tenants — resolved by the user completing
   the interactive browser MFA challenge properly; this isn't something
   fixable from the CLI/agent side, it's inherently an interactive step
   tied to the account's own security policy.
2. Resource group `mac-se-gemini-bridge-rg` in `uksouth`.
3. VM `mac-se-bridge-vm`: **Standard_B1ls** (1 vCPU, 0.5GB RAM, ~$3.80/mo —
   cheapest available tier, explicitly requested; plenty for a stdlib-only
   Python socket server), Ubuntu 22.04 LTS, SSH key auth
   (`az vm create --generate-ssh-keys`).
4. Opened inbound port 6023 on the VM's network security group
   (`az vm open-port`) — port 22/SSH is allowed by default on VM creation.
5. Deployed `bridge.py` via `scp`, plus a `bridge.env` file (mode 600)
   holding `GEMINI_API_KEY` and the new `BRIDGE_PASSWORD` — kept out of the
   unit file itself so secrets aren't visible via `systemctl cat` or
   process listings of the unit file.
6. Set up as a systemd service (`/etc/systemd/system/mac-se-bridge.service`,
   `Restart=always`, `EnvironmentFile=`) so it survives reboots and
   auto-restarts on crash — enabled and started, confirmed running.
7. Tested end-to-end from this PC over the public internet (not LAN):
   password prompt, correct/incorrect rejection, and a full Gemini
   round-trip all worked identically to the local setup.
8. **Auto-shutdown at 11pm UK time**: `az vm auto-shutdown` only accepts a
   raw UTC time with no timezone parameter in its convenience-command form
   — setting a fixed UTC offset would silently drift by an hour whenever
   BST/GMT changes. Fixed by updating the underlying
   `microsoft.devtestlab/schedules` resource directly via `az resource
   update` to set `properties.timeZoneId` to `"GMT Standard Time"`
   (Azure's Windows-style zone ID for UK), so the schedule now correctly
   stays at 11pm local year-round without manual adjustment at DST
   changes.
9. **Auto-start at 8am was explicitly deferred** — see "Phase 3" in
   Outstanding TODO above. No Azure equivalent one-liner exists for this
   (unlike shutdown); it needs a separate always-on trigger (Automation
   Account + scheduled runbook). Manual start in the meantime:
   `az vm start -g mac-se-gemini-bridge-rg -n mac-se-bridge-vm`.

**Result**: the Mac SE can now reach the Gemini bridge at the VM's public
IP on port 6023 from either OS side, with the laptop completely out of the
loop once confirmed working from the actual hardware (verification was
still pending as of this write-up — check with the user before assuming
this is fully live end-to-end from the SE itself, as opposed to just
verified from this PC).

## Switching the Gemini model (rate limits)

Hit a Gemini API rate limit on `gemini-flash-latest` under the free tier.
Rather than enabling billing (would unlock a higher tier but isn't
necessary at this traffic volume — a single terminal, sporadic short
messages), switched `GEMINI_MODEL` to `gemini-flash-lite-latest`, which
gets a more generous free-tier quota. Deployed to the Azure VM and
verified live by connecting directly to the VM's public IP from a headless
Python test client (no need to involve the physical SE for this kind of
check) — auth + a full chat round-trip both worked with no rate-limit
error.

## Phase 2: Gemini-driven file management

Goal: let natural-language requests typed on the SE ("delete X", "get me a
copy of ResEdit") actually execute against the SE's filesystem, via
Gemini's function-calling API turning a chat message into a real FTP
operation.

### The connectivity problem, and picking a solution

`bridge.py` (on Azure) only ever talked to the Gemini API — it had no path
to the SE's FTP server, which sits on a private LAN IP (`192.168.1.210`)
behind the home router. Worked through the options with the user:

- **Port-forward the FTP server directly to the internet** — simplest
  technically, rejected: exposes 1990s unpatched FTP software (already
  known to be fragile, see the crash findings below) with cleartext
  credentials directly to internet scanners/bots.
- **A LAN-side relay running Tailscale**, tunneling Azure into the home
  network without exposing anything publicly. Landed here. Debated the
  actual relay device at length:
  - A **GL.iNet travel router** (e.g. GL-MT3000 "Beryl AX") was considered
    first — has an official Tailscale app built into its firmware.
    Initially assumed it could run in Access Point/bridge mode (joining
    the existing home network passively) while still running Tailscale,
    but checking GL.iNet's actual docs found this is **not possible** —
    Tailscale is explicitly unavailable in any non-Router network mode on
    their firmware. Working around this would mean putting the GL.iNet
    device in its own Router mode (creating a *new* sub-network) and
    **moving the SE onto that new WiFi network** instead of the existing
    home WiFi — workable, but more moving parts than necessary.
  - A **Raspberry Pi** running Tailscale directly was reconsidered instead
    — being a full Linux machine (not router firmware with GUI-imposed
    mode restrictions), it just joins the existing home LAN as an
    ordinary DHCP client (same as any laptop) and runs Tailscale as a
    completely normal background service, advertising the *existing* home
    subnet unchanged. No SE reconfiguration needed at all, unlike the
    GL.iNet path. Once the user noted the Pi was ~50% cheaper than a
    comparable GL.iNet router too, this became the clear choice.
  - Board picked after several rounds of price/tradeoff comparison: Pi
    Zero 2 W was the original pick but is essentially permanently out of
    stock; a Pi Pico 2 W was briefly considered but is a microcontroller
    (no OS, can't run Tailscale at all — different product line entirely
    despite the similar name); landed on a **Pi 4, 1GB RAM** — the 3B+ was
    only ~£3 cheaper (not worth the older board's USB-shared Ethernet and
    shorter support horizon for that little saving) and a Pi 5 was ruled
    out despite being the same price, since it needs active cooling for
    sustained use (a moving part/failure point for a device meant to run
    forgotten in a drawer for years) and its extra performance is
    completely wasted on this workload. 1GB RAM is plenty — Tailscale's
    daemon plus a headless `Raspberry Pi OS Lite` install is a tiny
    footprint, nothing like the embedded-router RAM constraints that
    ruled out some cheap GL.iNet models.
- **Not yet physically built** — this is documented as the plan, not a
  completed step. `bridge/README.md`'s file-management section and
  `ARCHITECTURE.md`'s system diagram both reflect this as planned.

### Live-testing `mac_ftp.py` against the real SE

Before wiring anything to an LLM, tested the previously-untested
operations (`get`, `rm`, `rmdir`, `rename`, `put-app`) directly via the
CLI. First hurdle: the SE was powered off, then once powered on the FTP
connection kept alternating between `530 Login failed` and connection
refused — turned out the SE was booted into **System 6.0.8**, not 7.1, so
NetPresenz (which the CLI's default anonymous credentials were tuned for)
wasn't even running; NCSA Telnet's FTP server was answering instead, which
needs its own account (set up via Edit → Preferences → FTP Users, not
anonymous access). Confirmed via WinSCP that the real credentials worked
outside `mac_ftp.py` too, ruling out a tool-specific bug.

With the right credentials, `get` and `put` (re-verified) worked cleanly.
Then two real, hardware-confirmed findings:

- **`mkdir` (`MKD`) crashed the FTP server outright** — not a clean
  rejection, the server stopped responding entirely and needed a physical
  restart. Confirmed a second time with **`rename` (`RNFR`/`RNTO`)** —
  same crash behavior. WinSCP hitting the same "Command not understood"
  wall on a manual delete attempt (unprompted, from the user) further
  confirmed this isn't an `ftplib`/`mac_ftp.py`-specific issue — it's
  NCSA Telnet's FTP server itself only implementing a minimal command set
  and choking on anything outside it.
- **`rm` (`DELE`) is unsupported but degrades safely** — cleanly rejected
  with `500 Command not understood` every time, no crash.

Also learned mid-session that **System 7.1 is staying off the table** for
day-to-day use — it's "flaky and kills performance," so NetPresenz's
fuller FTP command set (assumed but never actually verified) isn't
realistically available to re-test. Design decision made on that basis:
`mkdir`/`rmdir`/`rename` dropped entirely from the Gemini-callable tool
set (crash risk, and no reliable way for the bridge to know which OS is
booted before trying), while `delete_file` stayed in (degrades safely,
worth keeping in case NetPresenz/7.1 ever becomes usable again).

### Refactor: `mac_ftp_lib.py`

Split `tools/mac_ftp.py`'s CLI-only `cmd_*` functions (each took an
argparse `Namespace`) into a proper importable library,
`tools/mac_ftp_lib.py`: `connect_once`/`with_retry` unchanged (still
fresh-connection-per-retry, active-mode-forced), plus parameterized
functions (`list_dir`, `download`, `upload`, `upload_app`, `delete_file`,
`make_dir`, `remove_dir`, `rename`) that return plain data instead of
printing. `mac_ftp.py` itself became a thin CLI wrapper importing from the
library — same commands/flags/behavior for existing manual use, verified
via `--help` and a syntax/import check before any live testing resumed.

### Gemini function-calling in `bridge.py`

Before writing code, checked the actual Gemini REST API function-calling
contract rather than guessing — worth doing since `bridge.py` talks to the
raw REST endpoint directly (no SDK), and getting the `functionResponse`
turn's `role` field wrong would have silently broken the whole loop.
Confirmed via the official API reference (`ai.google.dev/api/generate-content`)
that valid `role` values are only `user`/`model`/`system` — `functionResponse`
parts go in a `role: "user"` content entry, not `role: "function"` as one
lower-confidence search result suggested.

Implemented:
- `TOOLS` (Gemini function declarations) for `list_files`, `delete_file`,
  `search_software`, `download_and_install_software`.
- `call_gemini_with_tools()`: a loop (capped at 5 rounds) that posts the
  conversation + tool declarations, and on a `functionCall` response,
  either executes the tool immediately (non-destructive) or — for
  `delete_file`/`download_and_install_software` — sends a plain-English
  description down the Telnet connection and waits for the next line to
  be `yes`/`y` before actually running it, reusing the same out-of-band
  `next(lines)` pattern `authenticate()` already used for the password
  prompt.
- `PROTECTED_PATH_PREFIXES` (`System Folder`, `System`, `Finder`) checked
  before any destructive op regardless of confirmation.
- `search_software`: archive.org's public `advancedsearch.php` API only.
  Macintosh Garden was planned as a second source too, but checking (both
  a direct fetch and a search) found the site actively returns HTTP 403 to
  automated requests — the same bot-gating pattern already hit once before
  in this project with a different retro-software download source. Not
  included rather than shipping something that silently never works.
- `download_and_install_software`: downloads via `urllib`, resolves
  archive.org identifiers/details-URLs to a real file URL via the
  metadata API, extracts archives with `unar -forks visible`, pulls the
  real resource fork + Finder info (type/creator/flags) from the
  AppleDouble sidecar, sanity-checks the resource fork actually parses
  before trusting it (guarding against the exact AppleDouble-misidentified-
  as-raw-resource-fork bug hit earlier in this project), then uploads a
  MacBinary `.bin` for NetPresenz/NCSA Telnet's auto-decode. Disk images
  and plain files skip straight to upload. Every failure path reports
  back in plain language rather than being swallowed.

### End-to-end testing, and a real bug found along the way

Restarted the local Windows-hosted bridge with the new code and drove it
with a headless Python test client (same technique used earlier for the
rate-limit verification). First attempt: every file-management tool
reported "not configured," which looked like a credentials problem —
several rounds of debugging Windows environment-variable passing
(PowerShell's `Start-Process` doesn't reliably inherit `$env:` the way
expected; `.NET ProcessStartInfo` with unread redirected stdout/stderr
streams caused a separate, confusing "connection accepted but nothing
sent back" symptom) before finding the real cause: an explicit debug
launcher script proved the environment variables *were* correctly set
inside the process, which meant the actual bug was elsewhere.

Turned out to be a `sys.path` bug in `bridge.py` itself: the fallback
logic for finding `mac_ftp_lib.py` only handled two deployment layouts
(flat, everything alongside `bridge.py` — the Azure plan; or repo layout,
`bridge/bridge.py` + sibling `../tools/`), but the actual local working
copy has a *third* layout — `bridge.py` at the project root with `tools/`
as an immediate child directory. The import silently failed and fell back
to a generic "not configured" message that looked exactly like a missing
credential rather than a missing module. Fixed by adding the third
candidate path.

With that fixed, verified via the bridge's own connection log (not just
the chat replies, which can't be fully trusted at face value — see below)
that everything works mechanically as designed: `list_files` returns the
real SE listing, a **declined** delete shows zero `DELE` attempts in the
log, a **confirmed** delete shows the real 4-attempt retry sequence
(failing gracefully, as expected given the 6.0.8 FTP server limitations
above), and `search_software` executes against archive.org without error.

One cosmetic-only issue found: on a declined delete, Gemini's reply said
"the OS doesn't support deletion" instead of accurately reporting "you
said no" — the model filling in a plausible-sounding reason rather than
the real one, likely primed by the system prompt's framing around
delete's usual failure mode. Improved the synthetic decline message to
explicitly tell the model not to invent a reason; left as a known minor
wording quirk rather than over-engineering prompt wording further, since
the actual safety property (confirmed via server log: no real delete
without explicit "yes") was never in question.

### Still open

- ✅ ~~Deploy to Azure~~ — done. `bridge.py` + `mac_ftp_lib.py` + deps +
  `unar` are on the VM (`/opt/mac-se-bridge/`), service restarted.
  `MAC_FTP_USER`/`MAC_FTP_PASS` deliberately left **unset** in
  `bridge.env` until the Pi relay exists — with them unset, the
  file-management tools report "not configured" instantly instead of
  hanging for minutes trying to reach the SE's unreachable private IP.
  Verified live: `search_software` works from the real bridge (no SE
  connectivity needed at all), `list_files` correctly reports "not
  configured" with no hang.
- ✅ ~~Live-test `download_and_install_software`~~ — dry-run succeeded
  against a real archive.org title (`tucows_204988_SimpleText_Color_Menu`,
  a genuine `.sit`, same sourcing pattern as BetterTelnet earlier in this
  project). Ran directly on the Azure VM with a deliberately fake local
  FTP host: download → archive.org details-URL resolution via the
  metadata API → `unar -forks visible` extraction → AppleDouble Finder
  Info + resource fork parsing → resource fork sanity check (passed,
  found a real `styl` resource) → MacBinary build all succeeded; only the
  final upload failed, exactly as expected given the fake host. Validates
  the whole pipeline short of the live FTP step, which needs the Pi.
- Build the Pi + Tailscale relay so Azure can actually reach the SE — the
  one remaining blocker. Once it exists: set `MAC_FTP_*` in the VM's
  `bridge.env`, restart the service, and everything above goes live for
  real.
- ✅ ~~`search_software`'s archive.org relevance is weak~~ — fixed.
  archive.org's own full-text search stayed weak (raw "MacPaint"/
  "ResEdit"/"golf game" queries kept returning mostly unrelated games and
  media dumps), so added a second, internal Gemini call inside
  `search_software` that judges genuine relevance against the raw
  candidates and filters before anything reaches the user, using
  structured JSON output (`responseMimeType`/`responseSchema` on
  `generationConfig` — verified this is the correct field structure for
  the `generateContent` endpoint specifically, since a couple of doc pages
  showed a different nested shape that turned out to belong to the newer
  Interactions API instead). Falls back to the unfiltered list only on an
  actual error, not just an empty filtered result -- "no genuine match"
  is a valid, useful answer on its own. Verified live: a "golf game"
  search and a re-test of "MacPaint" both now correctly report no genuine
  match and suggest better search terms, instead of showing the
  previously-seen irrelevant results. Deployed to Azure.
