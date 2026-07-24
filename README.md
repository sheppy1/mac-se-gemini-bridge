# Mac SE Retro Project

Getting a real 1987 Macintosh SE (68000 CPU, BlueSCSI SD-card storage in
place of a SCSI hard disk) running as a live FTP server on a modern LAN,
then extending it so it can "chat with AI" through a Telnet client talking
to a small bridge that calls the Gemini API. The SE dual-boots **System
7.1** (NetPresenz FTP server, BetterTelnet, the Gemini bridge) and
**System 6.0.8** (BlueSCSI WiFi DaynaPORT networking, BetterTelnet with its
own built-in FTP server as the System-6-compatible alternative to
NetPresenz — see below for why NetPresenz specifically can't run there).

Full narrative of how all of this actually got built — including several
wrong turns and how they were diagnosed — is in
[`SESSION_SUMMARY.md`](./SESSION_SUMMARY.md). For a visual overview with
architecture diagrams, see [`ARCHITECTURE.md`](./ARCHITECTURE.md). This
README is the map of the repo and the practical "how do I run/rebuild
this" reference.

## Layout

```
netpresenz/   NetPresenz 4.1 (FTP/WWW/Gopher server) — build outputs and
              original source archive
bridge/       The Gemini Telnet bridge that runs on a modern PC
tools/        Reusable Python scripts for packaging classic Mac software
              (shared by both of the above)
```

## `netpresenz/` — the FTP server running on the SE

- **`netpresenz-41.sit`** — the original 1997 StuffIt archive (NetPresenz was
  later made free by its author, Stairways Software / Peter Lewis).
- **`netpresenz.pdf`**, **`No serial needed`** — original documentation.
- **`NetPresenz.dsk`** — a Disk Copy 4.2 disk image containing a properly
  rebuilt NetPresenz application (real resource fork, correct type/creator
  codes, `SIZE`/`CODE`/`BNDL` resources verified present) — mountable on a
  classic Mac with "Mount Image," Disk Copy, ShrinkWrap, etc.
- **`NetPresenz.dsk.bin`** — the same `.dsk`, wrapped in MacBinary (type
  `dImg`, creator `dCpy`) so it survives being copied across a plain FAT
  volume (e.g. a BlueSCSI SD card's shared drive) without losing its Mac
  metadata, then gets its type/creator restored by StuffIt Expander on the
  Mac side.

This is running live on the SE's **System 7.1** side, serving FTP (and
could serve WWW/Gopher too) over the LAN via **MacTCP** (this SE's 68000
CPU is below Open Transport's official 68030 minimum, so MacTCP is the only
option). See `SESSION_SUMMARY.md` for the whole saga of getting this
working reliably — short version: the config was always correct, the real
culprit was MacTCP's poor handling of passive-mode FTP, fixed by using
active mode.

**Important: NetPresenz cannot run on System 6.** Its own documentation
states it requires System 7, because it depends entirely on System 7's
Personal File Sharing for authentication and file access — a feature that
doesn't exist in System 6 at all. This isn't a packaging or compatibility
quirk to work around; don't spend time trying. For FTP serving on the
System 6.0.8 side, use **BetterTelnet's built-in FTP server** instead (see
below) — the period-correct System-6-era equivalent, inherited from its
NCSA Telnet lineage.

## `bridge/` — the AI chat bridge

See [`bridge/README.md`](./bridge/README.md) for full setup/operations docs
(architecture, Azure deployment commands, the Windows firewall rule needed
for the local fallback option, how to move it to a new machine). Short
version: a Python script (stdlib only, no dependencies) listens on a plain
TCP port and relays whatever's typed into a Telnet client on the SE to the
Gemini API and back. **Runs on an always-on Azure VM** (recommended — the
SE can reach it from anywhere it has internet access, no LAN required) or
on a Windows PC on the same LAN as a local/dev fallback. Password-protected
(`BRIDGE_PASSWORD`) since the Azure deployment is reachable from the whole
internet, not just a trusted LAN.

**BetterTelnet** is the Gemini chat client on the **System 7.1** side —
works great there. It does *not* work on the System 6.0.8 side on this
particular stock Mac SE: launches, but the cursor never renders and it
crashes with a "coprocessor not installed" error on interaction (leading
theory: it has color-cursor resources, and this SE's ROM has no Color
QuickDraw support at all — see `SESSION_SUMMARY.md` for the full
investigation). Same binary, same physical CPU, only fails on 6.0.8, so
it's environment-dependent, not a hard CPU-instruction wall.

For **System 6.0.8**, use **`NCSA Telnet.bin`** instead (also included
here) — the genuine original 1995 program BetterTelnet was built on,
sourced via the Wayback Machine after every live download source turned
out to be bot-gated (see `SESSION_SUMMARY.md` for that whole detour). It
launched with no crash and works as both the Gemini bridge client *and*
an FTP server (Edit menu → Preferences → FTP Users to set up an account,
then FTP Server to toggle it on) — the period-correct System-6-era
equivalent to NetPresenz, since NetPresenz itself cannot run on System 6
at all (see above).

## `tools/` — reusable classic Mac packaging scripts

Came out of the process of getting NetPresenz and BetterTelnet onto the SE
correctly and are generically reusable for packaging *other* classic Mac
software. All pure Python 3 stdlib, no dependencies.

- **`extract_appledouble.py`** — parses an AppleDouble container (magic
  `00 05 16 07`). Needed because `unar -forks visible` doesn't write a bare
  resource fork to its `.rsrc` sidecar files — it wraps the resource fork
  *and* Finder Info together in an AppleDouble container. Treating that
  sidecar as a raw resource fork (an easy mistake to make — it happened once
  during this project and silently corrupted an app, causing Finder error
  `-50` at launch) will produce a broken build. Usage:
  `python extract_appledouble.py file.rsrc` prints the entries found; import
  `parse_appledouble(path)` to get a dict of `{entry_id: bytes}` (id 2 =
  resource fork, id 9 = Finder Info).

- **`make_macbinary.py`** — encodes a data fork + resource fork (+
  type/creator/Finder flags) into a MacBinary II (`.bin`) file.
  Hand-verified against hfsutils' own C source so `hcopy -m` (and
  NetPresenz's automatic `.bin`-upload decoding) accept it.
  ```
  python make_macbinary.py --name "AppName" --type APPL --creator XXXX \
    --flags 0x2100 --data path/to/datafork --rsrc path/to/resourcefork \
    --out AppName.bin
  ```

- **`make_dc42.py`** — wraps a raw floppy-sized (400K/800K/720K/1440K) HFS
  volume image in an Apple Disk Copy 4.2 header, for mounting with classic
  Mac disk-image tools ("Mount Image," Disk Copy, ShrinkWrap, etc.).
  ```
  python make_dc42.py --name "Volume Name" --in raw_hfs.img --out volume.dsk
  ```

- **`parse_rsrc.py`** — sanity-checks a *real* (already-unwrapped) resource
  fork: prints the header offsets and every resource type/ID found. Useful
  to confirm a `SIZE`/`CODE`/`BNDL` resource is actually present and the
  header offsets are sane before trusting a build.
  `python parse_rsrc.py file.rsrc [file2.rsrc ...]`

- **`mac_ftp.py`** — CLI for managing files on the SE over FTP without
  relearning this project's hard-won lessons every time: forces
  **active-mode FTP** (required for this MacTCP setup — passive mode
  stalls/hangs), and reconnects fresh on every retry instead of retrying on
  a connection a previous failure may have left in a broken state. Supports
  `ls`, `get`, `put`, `put-app` (builds+uploads a MacBinary `.bin` on the
  fly for NetPresenz's auto-decode), `rm`, `mkdir`, `rmdir`, `rename`.
  ```
  python mac_ftp.py ls
  python mac_ftp.py put-app --data App --rsrc App.rsrc --name App --type APPL --creator XXXX
  ```
  **Note**: only `ls`/`mkdir`/`put` are confirmed working live against the
  SE so far; `get`/`rm`/`rmdir`/`rename`/`put-app` are implemented but
  untested. Also: don't hammer this hardware with rapid automated retries —
  a burst of ~10 connection attempts in quick succession crashed NetPresenz
  once already (Address Error, traced to connection volume on a
  maxed-out-4MB 1997-era-software combination, not a tool bug).

### Typical workflow for packaging a new piece of classic Mac software

Given something downloaded as a `.sit`/`.hqx`:

1. Extract with `unar -forks visible -o outdir archive.sit`.
2. For each app, run `extract_appledouble.py` on its `.rsrc` sidecar to get
   the true resource fork + Finder Info (type/creator/flags).
3. Sanity-check the true resource fork with `parse_rsrc.py`.
4. Build a `.bin` with `make_macbinary.py`.
5. Either FTP it to NetPresenz as `Name.bin` (auto-decoded server-side, per
   NetPresenz's documented `.bin`-upload behavior), or use `hfsutils`
   (`hformat`/`hmount`/`hcopy -m`) to put it into an HFS volume and wrap that
   with `make_dc42.py` for a proper `.dsk`.

`hfsutils` itself isn't vendored here — it's the standard open-source
HFS-volume toolkit (`apt install hfsutils` on Debian/Ubuntu; this project
ran it inside a throwaway Docker container to avoid needing root on the
host).

## Hardware/software involved

- Macintosh SE (1987), 68000 CPU, dual-booting System 7.1 and System 6.0.8
- BlueSCSI (SD card as SCSI storage, in place of a real hard disk), including
  its WiFi DaynaPORT network emulation feature on the System 6.0.8 side
- MacTCP on both OS sides (Open Transport isn't available — this CPU is
  below its official 68030 minimum)
- **System 7.1**: NetPresenz 4.1 (FTP/WWW/Gopher server), BetterTelnet
  (Telnet client, freeware successor to NCSA Telnet) as the Gemini bridge
  client
- **System 6.0.8**: NCSA Telnet 2.7b4 (the original BetterTelnet is built
  on) as both FTP server and Gemini bridge client — NetPresenz and
  BetterTelnet both turned out to be non-starters here, see
  `SESSION_SUMMARY.md`
- An Azure VM (Standard_B1ls, Ubuntu 22.04, ~$3.80/month) running the
  Python bridge to Gemini as a systemd service — or a Windows PC on the
  same LAN as a local fallback
