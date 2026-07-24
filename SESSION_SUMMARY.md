# Mac SE Retro Project — Session Summary

Goal: get a real 1987 Macintosh SE (System 7.1, BlueSCSI SD-card storage) running
NetPresenz as an FTP server, then extend it with a Gemini/Claude AI chat bridge
over Telnet.

## Status: working end-to-end

As of the latest session, the whole thing works: the Mac SE runs NetPresenz
(FTP server) and BetterTelnet, and can chat with Gemini through `bridge.py`
running on this PC. See "Post-summary developments" further down for how the
remaining issues below this line were resolved, and `README.md` for the
setup/operations reference.

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

All done except the loose ends listed above ("Loose ends still worth tidying
up") — persistent-run setup and the security reverts.

## Key files

| File | Purpose |
|---|---|
| `NetPresenz 4.1\extracted\...` | Extracted original archive contents |
| `NetPresenz 4.1\NetPresenz.dsk` | Disk Copy 4.2 image, NetPresenz app (correct build) |
| `NetPresenz 4.1\NetPresenz.dsk.bin` | Same, MacBinary-wrapped for FAT transfer |
| `mac-se-gemini-bridge\bridge.py` | Telnet-to-Gemini bridge server (now calls the Gemini REST API directly — no Node/CLI dependency, see below) |
| `mac-se-gemini-bridge\BetterTelnet.bin` | Telnet client already uploaded and running on the SE |
| `mac-se-gemini-bridge\README.md` | **Setup/operations reference** — firewall rule, PowerShell commands, how to start/stop/move the bridge |
| `mac-se-gemini-bridge\tools\` | Reusable packaging scripts (MacBinary encoder, AppleDouble parser, Disk Copy 4.2 wrapper, resource-fork sanity checker) |

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
