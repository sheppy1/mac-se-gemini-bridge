# Architecture

Visual companion to [`README.md`](./README.md) (the practical reference)
and [`SESSION_SUMMARY.md`](./SESSION_SUMMARY.md) (the full build narrative,
including every wrong turn). This file focuses on how the finished system
actually fits together.

## System overview

```mermaid
flowchart TB
    subgraph SE["Macintosh SE (1987, 68000 CPU)"]
        direction TB
        subgraph OS71["System 7.1"]
            NP["NetPresenz 4.1<br/>FTP/WWW/Gopher server"]
            BT["BetterTelnet<br/>Gemini bridge client"]
        end
        subgraph OS608["System 6.0.8"]
            NCSA["NCSA Telnet 2.7b4<br/>FTP server + Gemini bridge client"]
        end
    end

    subgraph BlueSCSI["BlueSCSI (SD card, replaces SCSI hard disk)"]
        HD00["HD00: System 7.1 boot volume"]
        HD10["HD10: System 6.0.8 boot volume (80MB, Silverlining-formatted)"]
        DPORT["WiFi DaynaPORT emulation<br/>(NE4.hda placeholder)"]
    end

    SE -- "boots from" --> BlueSCSI
    OS608 -. "networking via" .-> DPORT
    DPORT -- "WiFi, ~60KB/sec ceiling" --> Router["Home WiFi Router"]
    OS71 -- "MacTCP, wired-equivalent" --> Router

    Router -- "internet" --> Azure["Azure VM (Standard_B1ls)<br/>bridge.py as systemd service<br/>~£3/month, 11pm auto-shutdown"]
    Azure -- "HTTPS REST" --> Gemini["Google Gemini API<br/>generativelanguage.googleapis.com"]
    Azure -. "Tailscale tunnel<br/>(planned, not yet built)" .-> Pi["Raspberry Pi 4 (1GB)<br/>Tailscale subnet router<br/>joins existing home LAN as-is"]
    Pi -. "advertises home subnet,<br/>no SE reconfiguration" .-> Router

    PC["Windows PC<br/>(local fallback bridge host,<br/>+ dev/build tooling)"] -. "LAN, local-only fallback" .-> Router

    NP -. "FTP :21, active mode" .-> PC
    BT -- "Telnet :6023" --> Azure
    NCSA -- "Telnet :6023 + FTP :21" --> Azure
    NCSA -. "FTP :21, via Tailscale<br/>(file-mgmt tools, planned)" .-> Azure

    style SE fill:#e8d5c4,stroke:#333,color:#1a1a1a
    style BlueSCSI fill:#d4e6f1,stroke:#333,color:#1a1a1a
    style Azure fill:#d5f5d5,stroke:#333,color:#1a1a1a
    style Gemini fill:#fce8b2,stroke:#333,color:#1a1a1a
    style PC fill:#e6e6e6,stroke:#333,color:#1a1a1a
    style Pi fill:#e6e6e6,stroke:#333,color:#1a1a1a
    style NP fill:#fdf6ec,stroke:#333,color:#1a1a1a
    style BT fill:#fdf6ec,stroke:#333,color:#1a1a1a
    style NCSA fill:#fdf6ec,stroke:#333,color:#1a1a1a
    style HD00 fill:#eef6fc,stroke:#333,color:#1a1a1a
    style HD10 fill:#eef6fc,stroke:#333,color:#1a1a1a
    style DPORT fill:#eef6fc,stroke:#333,color:#1a1a1a
    style Router fill:#ffffff,stroke:#333,color:#1a1a1a
```

Key points this diagram is making:
- The SE **dual-boots** two separate classic Mac OS versions from the same
  BlueSCSI SD card, each with its own boot volume and its own Telnet
  client/FTP server combination — NetPresenz can't run on System 6 at all
  (needs Personal File Sharing, a 7.x-only feature), and BetterTelnet
  crashes on this specific SE under System 6.0.8 (likely a Color QuickDraw
  dependency the SE's ROM doesn't have), so each OS ended up with a
  different, non-interchangeable pair of tools.
- Networking on the **System 6.0.8** side is entirely virtual — BlueSCSI's
  WiFi DaynaPORT feature emulates a SCSI-attached network card over the
  Pico W's onboard WiFi radio, with a fixed ~60KB/sec throughput ceiling
  regardless of which classic Mac OS version sits on top of it.
- The **Gemini bridge** (`bridge.py`) can run on either an Azure VM
  (recommended — reachable from anywhere the SE has internet, no LAN
  dependency) or a Windows PC on the same LAN (local/dev fallback, also
  where all the build/packaging tooling lives).
- The **Raspberry Pi relay** is planned, not yet built: Azure has no path
  to the SE's FTP server today (it's a private LAN IP behind the home
  router). A Pi 4 running Tailscale as a subnet router would advertise the
  existing home LAN unchanged — no SE reconfiguration, no exposing the
  vintage FTP server to the public internet — so Azure can reach it
  directly for the file-management tools. Until this exists, those tools
  only work when running the bridge locally on the Windows PC fallback.

## SD card SCSI layout

```mermaid
flowchart LR
    SD["BlueSCSI SD card<br/>(bluescsi.ini, NE4.hda, shared/)"]
    SD --> ID0["SCSI ID 0<br/>HD00_SYSTEM_7.dsk<br/>~2GB, System 7.1 boot"]
    SD --> ID1["SCSI ID 1<br/>HD10 (80MB)<br/>System 6.0.8 boot<br/>(Silverlining-formatted,<br/>after 3 other tools failed)"]
    SD --> ID2["SCSI ID 2<br/>HD20_BlueSCSI_Toolbox.hda<br/>Utility apps"]
    SD --> ID3["SCSI ID 3<br/>HD30_BlueSCSI_Bootstrap.hda<br/>512MB, driver installers<br/>(MacTCP 2.1, DaynaPORT,<br/>MountImage, formatting tools)"]
    SD --> NE4["NE4.hda (0 bytes)<br/>reserves the WiFi DaynaPORT<br/>network device slot"]
    SD --> SHARED["shared/ folder<br/>plain FAT, no Mac metadata —<br/>drop zone for individual files<br/>(not folders) transferred from<br/>the Windows side"]

    style SD fill:#d4e6f1,stroke:#333,color:#1a1a1a
    style ID0 fill:#eef6fc,stroke:#333,color:#1a1a1a
    style ID1 fill:#eef6fc,stroke:#333,color:#1a1a1a
    style ID2 fill:#eef6fc,stroke:#333,color:#1a1a1a
    style ID3 fill:#eef6fc,stroke:#333,color:#1a1a1a
    style NE4 fill:#eef6fc,stroke:#333,color:#1a1a1a
    style SHARED fill:#eef6fc,stroke:#333,color:#1a1a1a
```

Every configured SCSI ID mounts **simultaneously** regardless of which one
the Mac actually boots from — a real gotcha during the DaynaPORT saga (see
`SESSION_SUMMARY.md`), since old installers that enumerate all mounted
volumes could choke on an oversized one that had nothing to do with the
actual install target.

## Chat flow (Gemini bridge)

```mermaid
sequenceDiagram
    participant User as Mac SE user
    participant Telnet as BetterTelnet /<br/>NCSA Telnet
    participant Bridge as bridge.py<br/>(Azure VM)
    participant Gemini as Gemini API

    User->>Telnet: types message, presses Return
    Telnet->>Bridge: TCP :6023 connect
    Bridge->>Telnet: "Password: "
    Telnet->>Bridge: password
    Bridge->>Bridge: hmac.compare_digest()
    Bridge->>Telnet: banner + "> " prompt
    User->>Telnet: message text
    Telnet->>Bridge: line (Telnet IAC stripped)
    Bridge->>Bridge: append to conversation history
    Bridge->>Gemini: POST generateContent<br/>(full history + system prompt)
    Gemini->>Bridge: JSON response
    Bridge->>Bridge: extract text, convert to<br/>Mac Roman + CRLF
    Bridge->>Telnet: reply text + "> " prompt
    Telnet->>User: displays reply
```

## File-management tool calling (with confirmation gate)

Natural-language requests like "delete X" or "get me a copy of ResEdit" go
through Gemini's function-calling API rather than being parsed directly.
Destructive tools (`delete_file`, `download_and_install_software`) always
pause for an explicit "yes" typed at the SE's own terminal before actually
touching the filesystem — Gemini deciding to do something isn't enough on
its own:

```mermaid
sequenceDiagram
    participant User as Mac SE user
    participant Bridge as bridge.py
    participant Gemini as Gemini API
    participant SE as SE's FTP server

    User->>Bridge: "delete claude_test.txt"
    Bridge->>Gemini: POST generateContent (+ tool declarations)
    Gemini->>Bridge: functionCall: delete_file(path=...)
    Bridge->>User: "Gemini wants to: delete '...'.<br/>Type yes to confirm, anything else to cancel."
    User->>Bridge: "yes" (or anything else)
    alt confirmed
        Bridge->>SE: DELE claude_test.txt (via mac_ftp_lib)
        SE->>Bridge: result (success, or "not supported")
    else declined
        Bridge->>Bridge: synthesize "user declined" result,<br/>no real FTP call made
    end
    Bridge->>Gemini: POST generateContent<br/>(+ functionResponse)
    Gemini->>Bridge: final text reply
    Bridge->>User: reply + "> " prompt
```

Key point: the "declined" path never reaches the SE's FTP server at all —
the decline is handled entirely inside `bridge.py` before any network call
to the SE is made, verified via the bridge's own connection log during
testing (a confirmed decline shows zero `DELE` attempts; a confirmed "yes"
shows the real attempt).

## FTP file transfer flow

Two different servers depending on which OS is booted, but the same
active-mode requirement either way (classic MacTCP handles passive-mode
FTP unreliably — confirmed via NetPresenz Setup's own Summary panel
reporting "Open Transport is not installed"):

```mermaid
sequenceDiagram
    participant PC as Windows PC<br/>(mac_ftp.py / curl)
    participant Server as NetPresenz (7.1) or<br/>NCSA Telnet server (6.0.8)
    participant Vol as SE boot volume

    PC->>Server: connect :21, active mode (--ftp-port -)
    Server->>PC: 220 banner
    PC->>Server: USER / PASS
    Server->>PC: 230 logged in
    PC->>Server: PORT (tells server which port to connect back to)
    Server->>PC: connects back for data channel
    PC->>Server: STOR filename (or filename.bin for<br/>NetPresenz's auto-MacBinary decode)
    Server->>Vol: writes file with real resource fork,<br/>type/creator restored from MacBinary
    Server->>PC: 226 transfer complete
```

## Classic Mac software packaging pipeline

The repeatable process (`tools/`) used to get NetPresenz, BetterTelnet,
and NCSA Telnet onto the SE correctly — i.e. with real resource forks and
type/creator codes intact, not just raw data:

```mermaid
flowchart LR
    A["Downloaded archive<br/>(.sit / .hqx)"] -->|"unar -forks visible"| B["Data fork +<br/>AppleDouble .rsrc sidecar"]
    B -->|"extract_appledouble.py"| C["True resource fork +<br/>Finder Info<br/>(type/creator/flags)"]
    C -->|"parse_rsrc.py"| D{"Sane header?<br/>SIZE/CODE/BNDL<br/>present?"}
    D -->|no| B
    D -->|yes| E["make_macbinary.py"]
    E --> F[".bin file<br/>(MacBinary II)"]
    F -->|"FTP STOR name.bin"| G["NetPresenz auto-decodes<br/>server-side"]
    F -->|"shared/ folder +<br/>StuffIt Expander"| H["Manual decode on the Mac"]
    F -->|"hfsutils hcopy -m +<br/>make_dc42.py"| I[".dsk Disk Copy 4.2 image<br/>(for Mount Image / MountImage cdev)"]

    style D fill:#fce8b2,stroke:#333,color:#1a1a1a
    style A fill:#ffffff,stroke:#333,color:#1a1a1a
    style B fill:#eef6fc,stroke:#333,color:#1a1a1a
    style C fill:#eef6fc,stroke:#333,color:#1a1a1a
    style E fill:#eef6fc,stroke:#333,color:#1a1a1a
    style F fill:#d5f5d5,stroke:#333,color:#1a1a1a
    style G fill:#ffffff,stroke:#333,color:#1a1a1a
    style H fill:#ffffff,stroke:#333,color:#1a1a1a
    style I fill:#ffffff,stroke:#333,color:#1a1a1a
```

The `extract_appledouble.py` step exists because of a real bug hit early
in this project: `unar -forks visible` doesn't write a bare resource fork
to its `.rsrc` sidecar — it wraps the resource fork *and* Finder Info
together in an AppleDouble container (magic `00 05 16 07`). Treating that
sidecar as a raw resource fork silently corrupts the app (extra header
bytes shift every resource offset), which is exactly what happened once —
Finder showed a generic icon and the app failed to launch with error `-50`
until it was diagnosed and fixed.

## Design decisions worth noting

- **Why `bridge.py` calls Gemini's REST API directly instead of the
  `gemini` CLI**: the CLI hung unpredictably for reasons unrelated to the
  API itself (confirmed independently — direct CLI invocations hung too,
  while the raw REST endpoint responded in ~1-2 seconds every time). Using
  `urllib` directly removed the Node.js/npm dependency entirely as a side
  effect.
- **Why active-mode FTP everywhere**: classic MacTCP (not Open
  Transport — this SE's 68000 CPU is below OT's official 68030 minimum, so
  it can never be upgraded to it) handles passive-mode FTP unreliably.
  Every FTP client interaction in this project (`mac_ftp.py`, manual
  `curl` testing) forces active mode for this reason.
- **Why the Azure VM is the cheapest tier (`Standard_B1ls`)**: `bridge.py`
  is pure-stdlib Python with a negligible memory footprint — there's
  nothing here that benefits from more than 1 vCPU / 0.5GB RAM.
- **Why `BRIDGE_PASSWORD` exists at all**: the local-LAN version of the
  bridge had zero authentication, which was fine when only trusted home
  devices could reach it. Moving to a public Azure VM changed the threat
  model — anyone who found the IP/port could otherwise chat using the
  owner's Gemini API quota. Constant-time comparison
  (`hmac.compare_digest`) and a short delay between attempts were added to
  avoid the most trivial abuse, without building out a full auth system
  for what's still fundamentally a personal project.
- **Why `mkdir`/`rmdir`/`rename` are not exposed as Gemini tools**: live
  testing against the SE's actual FTP server (NCSA Telnet, System 6.0.8)
  found that `MKD` and `RNFR`/`RNTO` crash the server outright rather than
  being cleanly rejected — confirmed twice, each requiring a physical
  restart. `DELE` degrades safely (clean `500` rejection), so `delete_file`
  stayed in the tool set, but the bridge has no reliable way to know which
  OS is currently booted before attempting a command, so anything with
  confirmed crash behavior was dropped entirely rather than risk it.
- **Why `search_software` only uses archive.org**: Macintosh Garden was
  the obvious second source to add, but its site actively returns HTTP 403
  to automated requests — the same bot-gating pattern this project already
  hit once before with an NCSA Telnet download source (worked around that
  time via the Wayback Machine's CDX API, which doesn't have an equivalent
  for live site search). archive.org's public `advancedsearch.php` API
  needs no key and isn't gated, so it's the sole source for now.
