#!/usr/bin/env python3
"""
A tiny Telnet-to-Gemini bridge for talking to Gemini from a vintage Mac
terminal client (e.g. BetterTelnet/NCSA Telnet on a Mac SE over MacTCP).

Listens on a plain TCP port. Strips/answers just enough Telnet IAC
negotiation to keep real telnet clients happy, reads lines of text, and
forwards each line (with growing conversation context) directly to the
Gemini API over HTTPS (google's REST endpoint, no CLI/Node dependency).

Also exposes a small set of Gemini function-calling tools that let natural
language requests ("delete X", "get me a copy of ResEdit") turn into real
FTP operations against the Mac SE, via tools/mac_ftp_lib.py. Destructive
tools always require an explicit "yes" typed at the terminal before they
actually run -- see call_gemini_with_tools().
"""
import hmac
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = "0.0.0.0"
PORT = 6023

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-flash-lite-latest"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

# If set, clients must enter this password before they can chat. Strongly
# recommended when the bridge is reachable beyond a trusted LAN (e.g.
# hosted on a public cloud VM) -- without it, anyone who finds the IP/port
# can burn your Gemini API quota. Leave unset for local-LAN-only use.
BRIDGE_PASSWORD = os.environ.get("BRIDGE_PASSWORD", "")
MAX_PASSWORD_ATTEMPTS = 3
FAILED_LOGIN_DELAY_S = 2  # slow down brute-forcing a little

# FTP credentials for the file-management tools (list_files, delete_file,
# download_and_install_software). Leave MAC_FTP_USER/MAC_FTP_PASS unset to
# disable file management entirely -- the tools will still be offered to
# Gemini but will report themselves as "not configured" rather than
# attempting a connection.
MAC_FTP_HOST = os.environ.get("MAC_FTP_HOST", "192.168.1.210")
MAC_FTP_USER = os.environ.get("MAC_FTP_USER", "")
MAC_FTP_PASS = os.environ.get("MAC_FTP_PASS", "")

# mac_ftp_lib.py's location varies by deployment layout: alongside
# bridge.py (flat /opt/mac-se-bridge/ Azure deployment), as a sibling of a
# bridge/ directory (repo layout: bridge/bridge.py + tools/), or as a child
# tools/ directory (local working-copy layout: bridge.py + tools/) -- try
# all three.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (_THIS_DIR,
                   os.path.join(_THIS_DIR, "..", "tools"),
                   os.path.join(_THIS_DIR, "tools")):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)
try:
    import mac_ftp_lib
except ImportError:
    mac_ftp_lib = None

IAC = 0xFF
WILL, WONT, DO, DONT = 0xFB, 0xFC, 0xFD, 0xFE
SB, SE = 0xFA, 0xF0

BANNER = (
    "\r\n"
    "=== Gemini terminal bridge ===\r\n"
    "Type a message and press Return. Type /quit to disconnect,\r\n"
    "/reset to clear conversation history.\r\n\r\n> "
)

SYSTEM_PROMPT = (
    "You are chatting over a slow 1987 Macintosh SE terminal connection. "
    "Keep replies short and plain: no markdown, no code fences, no emoji, "
    "prefer plain ASCII. Wrap naturally; the terminal is roughly 80 columns. "
    "You have tools to list/delete files on the SE and to search for and "
    "install classic Macintosh software. delete_file only works when the "
    "SE's current OS supports it -- if it reports unsupported, tell the "
    "user plainly rather than retrying. Always run search_software first "
    "and let the user pick a specific result before ever calling "
    "download_and_install_software -- never guess a URL yourself."
)


# ---------------------------------------------------------------------------
# Telnet plumbing (unchanged from before tool-calling was added)
# ---------------------------------------------------------------------------

def strip_telnet_negotiation(sock, data: bytes) -> bytes:
    """Remove IAC sequences from incoming data, replying DONT/WONT to any
    option negotiation so the client stops offering options."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b != IAC:
            out.append(b)
            i += 1
            continue

        if i + 1 >= n:
            break
        cmd = data[i + 1]

        if cmd in (WILL, WONT, DO, DONT):
            if i + 2 >= n:
                break
            opt = data[i + 2]
            reply_cmd = DONT if cmd in (WILL, WONT) else WONT
            try:
                sock.sendall(bytes([IAC, reply_cmd, opt]))
            except OSError:
                pass
            i += 3
        elif cmd == SB:
            j = i + 2
            while j + 1 < n and not (data[j] == IAC and data[j + 1] == SE):
                j += 1
            i = j + 2
        else:
            i += 2

    return bytes(out)


def to_mac_lines(text: str) -> bytes:
    # Send CRLF for telnet-protocol correctness; NCSA Telnet renders it fine.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "\r\n").encode("mac_roman", errors="replace")


def iter_lines(conn: socket.socket):
    """Yield decoded, stripped lines from conn as they arrive, handling
    Telnet IAC negotiation and buffering across recv() calls. Stops when
    the connection closes."""
    buf = bytearray()
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            return

        clean = strip_telnet_negotiation(conn, chunk)
        buf.extend(clean)

        while True:
            idx = None
            for sep in (b"\r\n", b"\n", b"\r"):
                p = buf.find(sep)
                if p != -1 and (idx is None or p < idx[0]):
                    idx = (p, len(sep))
            if idx is None:
                break
            pos, seplen = idx
            line_bytes = bytes(buf[:pos])
            del buf[: pos + seplen]

            try:
                line = line_bytes.decode("mac_roman", errors="replace").strip()
            except Exception:
                line = line_bytes.decode("ascii", errors="replace").strip()

            yield line


def authenticate(conn: socket.socket, lines) -> bool:
    """If BRIDGE_PASSWORD is set, prompt for it and check up to
    MAX_PASSWORD_ATTEMPTS times. Returns True if the client may proceed."""
    if not BRIDGE_PASSWORD:
        return True

    for attempt in range(1, MAX_PASSWORD_ATTEMPTS + 1):
        conn.sendall(b"Password: ")
        try:
            entered = next(lines)
        except StopIteration:
            return False

        if hmac.compare_digest(entered, BRIDGE_PASSWORD):
            return True

        time.sleep(FAILED_LOGIN_DELAY_S)
        if attempt < MAX_PASSWORD_ATTEMPTS:
            conn.sendall(b"\r\nIncorrect.\r\n")

    conn.sendall(b"\r\nToo many incorrect attempts.\r\n")
    return False


# ---------------------------------------------------------------------------
# File-management tools (backed by mac_ftp_lib.py)
# ---------------------------------------------------------------------------

# Confirmed by live testing against this SE's current FTP server (NCSA
# Telnet, System 6.0.8): MKD and RNFR/RNTO crash the server outright, DELE
# is cleanly rejected as unsupported. System 7.1/NetPresenz is too unstable
# to run day to day, so mkdir/rmdir/rename are NOT exposed as tools at all
# -- only the operations proven safe to attempt are here.
PROTECTED_PATH_PREFIXES = ("System Folder", "System", "Finder")

ARCHIVE_FILE_EXTS = (".sit", ".sea", ".hqx", ".zip", ".dsk", ".img", ".dc42")


def _is_protected_path(path: str) -> bool:
    normalized = path.strip("/").lower()
    return any(normalized == p.lower() or normalized.startswith(p.lower() + "/")
               for p in PROTECTED_PATH_PREFIXES)


def _ftp_not_configured():
    return {"error": "file management is not configured on this bridge deployment"}


def tool_list_files(args):
    if mac_ftp_lib is None:
        return _ftp_not_configured()
    if not MAC_FTP_USER or not MAC_FTP_PASS:
        return _ftp_not_configured()
    path = args.get("path") or ""
    try:
        lines = mac_ftp_lib.list_dir(MAC_FTP_HOST, MAC_FTP_USER, MAC_FTP_PASS, path)
    except SystemExit as e:
        return {"error": str(e)}
    return {"files": lines}


def tool_delete_file(args):
    if mac_ftp_lib is None:
        return _ftp_not_configured()
    if not MAC_FTP_USER or not MAC_FTP_PASS:
        return _ftp_not_configured()
    path = args.get("path") or ""
    if not path:
        return {"success": False, "message": "no path given"}
    if _is_protected_path(path):
        return {"success": False,
                "message": f"'{path}' is a protected system path and cannot be deleted via chat"}
    try:
        mac_ftp_lib.delete_file(MAC_FTP_HOST, MAC_FTP_USER, MAC_FTP_PASS, path)
    except SystemExit as e:
        return {"success": False,
                "message": f"delete failed -- the SE's current OS/FTP server likely "
                            f"doesn't support deleting files ({e})"}
    return {"success": True, "message": f"deleted {path}"}


def tool_search_software(args):
    """Search archive.org's public advancedsearch API (no key required) for
    classic Mac software. Macintosh Garden was considered too but its site
    actively 403s automated requests (confirmed while building this), so
    it's not included -- archive.org alone is the reliable source here."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"results": [], "message": "no search query given"}

    params = [
        ("q", f"({query}) AND mediatype:(software)"),
        ("fl[]", "identifier"),
        ("fl[]", "title"),
        ("fl[]", "description"),
        ("rows", "5"),
        ("page", "1"),
        ("output", "json"),
    ]
    search_url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": "mac-se-gemini-bridge/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"results": [], "message": f"archive.org search failed: {e}"}

    docs = data.get("response", {}).get("docs", [])
    results = []
    for d in docs:
        identifier = d.get("identifier", "")
        description = d.get("description") or ""
        if isinstance(description, list):
            description = " ".join(description)
        results.append({
            "title": d.get("title", identifier),
            "source": "archive.org",
            "url": f"https://archive.org/details/{identifier}",
            "description": description[:200],
        })
    if not results:
        return {"results": [], "message": "no results found on archive.org for this query"}
    return {"results": results}


def _resolve_archive_org_download_url(url_or_id: str) -> str:
    """Turn an archive.org details URL (or bare identifier) into a direct,
    downloadable file URL by picking a likely-relevant file from the item's
    metadata. Passes through unchanged if it doesn't look like an
    archive.org reference at all."""
    m = re.search(r"archive\.org/details/([^/?#]+)", url_or_id)
    identifier = m.group(1) if m else None
    if identifier is None and re.fullmatch(r"[A-Za-z0-9_.-]+", url_or_id):
        identifier = url_or_id
    if identifier is None:
        return url_or_id  # already a direct URL (or something we don't understand)

    meta_url = f"https://archive.org/metadata/{identifier}"
    req = urllib.request.Request(meta_url, headers={"User-Agent": "mac-se-gemini-bridge/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        meta = json.loads(resp.read().decode("utf-8"))

    files = meta.get("files", [])
    for f in files:
        name = f.get("name", "")
        if name.lower().endswith(ARCHIVE_FILE_EXTS):
            return f"https://archive.org/download/{identifier}/{name}"
    if files:
        return f"https://archive.org/download/{identifier}/{files[0].get('name', '')}"
    raise ValueError(f"no downloadable files found for archive.org item '{identifier}'")


def _write_temp(tmpdir, data: bytes, name="resource.rsrc"):
    path = os.path.join(tmpdir, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _pick_data_and_rsrc(outdir):
    """Walk outdir after `unar -forks visible` extraction, pairing each data
    file with its AppleDouble .rsrc sidecar (if any). Returns a list of
    (name, data_path, rsrc_sidecar_path_or_None)."""
    pairs = []
    for root, _dirs, files in os.walk(outdir):
        for fn in files:
            if fn.endswith(".rsrc"):
                continue
            data_path = os.path.join(root, fn)
            rsrc_path = data_path + ".rsrc"
            pairs.append((fn, data_path, rsrc_path if os.path.exists(rsrc_path) else None))
    return pairs


def _finder_info_from_appledouble(rsrc_sidecar_path):
    """Returns (resource_fork_bytes, file_type, creator, finder_flags) from
    an AppleDouble sidecar, using extract_appledouble.py's parser -- entry
    id 2 is the true resource fork, entry id 9 is the classic FInfo struct
    (type/creator/flags in its first 10 bytes)."""
    from extract_appledouble import parse_appledouble
    entries = parse_appledouble(rsrc_sidecar_path)
    rsrc_fork = entries.get(2, b"")
    finfo = entries.get(9, b"")
    if len(finfo) >= 10:
        file_type = finfo[0:4].decode("mac_roman", errors="replace")
        creator = finfo[4:8].decode("mac_roman", errors="replace")
        flags = struct.unpack(">H", finfo[8:10])[0]
    else:
        file_type, creator, flags = "????", "????", 0
    return rsrc_fork, file_type, creator, flags


def _sanity_check_resource_fork(rsrc_bytes, tmpdir):
    """Make sure a resource fork actually parses as one before trusting it
    enough to upload -- this project has hit a real, silent-corruption bug
    here before (an AppleDouble container misidentified as a raw resource
    fork). Raises on structural failure; doesn't require any specific
    resource type to be present since not every file needs SIZE/CODE/BNDL."""
    from parse_rsrc import parse
    check_path = _write_temp(tmpdir, rsrc_bytes, "sanity_check.rsrc")
    parse(check_path)  # raises on structural failure (bad offsets etc.)


def tool_download_and_install_software(args):
    if mac_ftp_lib is None:
        return _ftp_not_configured()
    if not MAC_FTP_USER or not MAC_FTP_PASS:
        return _ftp_not_configured()

    url = (args.get("url") or "").strip()
    suggested_name = (args.get("suggested_name") or "").strip()
    if not url:
        return {"success": False, "message": "no url given"}

    try:
        url = _resolve_archive_org_download_url(url)
    except Exception as e:
        return {"success": False, "message": f"couldn't resolve download link: {e}"}

    with tempfile.TemporaryDirectory(prefix="macse-dl-") as tmpdir:
        local_path = os.path.join(tmpdir, os.path.basename(urllib.parse.urlparse(url).path) or "download")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mac-se-gemini-bridge/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read(50 * 1024 * 1024)  # 50MB sanity cap
        except Exception as e:
            return {"success": False, "message": f"download failed: {e}"}
        with open(local_path, "wb") as f:
            f.write(data)

        lower = local_path.lower()

        if lower.endswith((".dsk", ".img", ".dc42")):
            remote_name = suggested_name or os.path.basename(local_path)
            try:
                remote = mac_ftp_lib.upload(MAC_FTP_HOST, MAC_FTP_USER, MAC_FTP_PASS,
                                             local_path, remote_name)
            except SystemExit as e:
                return {"success": False, "message": f"upload failed: {e}"}
            return {"success": True, "message": f"uploaded disk image as {remote}"}

        if lower.endswith((".sit", ".sea", ".hqx", ".zip")):
            outdir = os.path.join(tmpdir, "extracted")
            os.makedirs(outdir, exist_ok=True)
            try:
                subprocess.run(["unar", "-forks", "visible", "-o", outdir, local_path],
                                check=True, capture_output=True, timeout=120)
            except Exception as e:
                return {"success": False, "message": f"couldn't extract archive: {e}"}

            pairs = _pick_data_and_rsrc(outdir)
            if not pairs:
                return {"success": False, "message": "archive extracted but no files found inside"}

            name, data_path, rsrc_sidecar = max(pairs, key=lambda p: os.path.getsize(p[1]))

            rsrc_path = None
            file_type, creator, flags = "????", "????", 0
            if rsrc_sidecar:
                try:
                    rsrc_fork, file_type, creator, flags = _finder_info_from_appledouble(rsrc_sidecar)
                except Exception as e:
                    return {"success": False, "message": f"couldn't parse resource fork/Finder info: {e}"}
                if rsrc_fork:
                    try:
                        _sanity_check_resource_fork(rsrc_fork, tmpdir)
                    except Exception as e:
                        return {"success": False,
                                "message": f"resource fork failed a sanity check, not uploading: {e}"}
                    rsrc_path = _write_temp(tmpdir, rsrc_fork, "app.rsrc")

            final_name = suggested_name or os.path.splitext(name)[0]
            try:
                remote, data_len, rsrc_len = mac_ftp_lib.upload_app(
                    MAC_FTP_HOST, MAC_FTP_USER, MAC_FTP_PASS,
                    data_path, rsrc_path, final_name, file_type, creator, flags=flags)
            except SystemExit as e:
                return {"success": False, "message": f"upload failed: {e}"}
            return {"success": True,
                    "message": f"installed '{final_name}' as {remote} "
                                f"({data_len}B data, {rsrc_len}B resource fork)"}

        # Plain file (not a recognized archive/disk-image format) -- upload
        # data-fork-only, no packaging attempted.
        remote_name = suggested_name or os.path.basename(local_path)
        try:
            remote = mac_ftp_lib.upload(MAC_FTP_HOST, MAC_FTP_USER, MAC_FTP_PASS,
                                         local_path, remote_name)
        except SystemExit as e:
            return {"success": False, "message": f"upload failed: {e}"}
        return {"success": True, "message": f"uploaded {remote}"}


TOOL_DISPATCH = {
    "list_files": tool_list_files,
    "delete_file": tool_delete_file,
    "search_software": tool_search_software,
    "download_and_install_software": tool_download_and_install_software,
}

DESTRUCTIVE_TOOLS = {"delete_file", "download_and_install_software"}

TOOLS = [{
    "functionDeclarations": [
        {
            "name": "list_files",
            "description": ("List files and folders on the Mac SE at the given path "
                             "(or the root/login directory if path is omitted)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                              "description": "Directory path on the SE to list. Leave empty for the root."},
                },
            },
        },
        {
            "name": "delete_file",
            "description": ("Delete a single file on the Mac SE. Only works when the SE's "
                             "current OS/FTP server supports deletion -- if it doesn't, this "
                             "reports that clearly rather than deleting anything."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file to delete."},
                },
                "required": ["path"],
            },
        },
        {
            "name": "search_software",
            "description": ("Search archive.org for classic Macintosh software matching a name "
                             "or description. Returns a shortlist -- present these to the user "
                             "and wait for them to pick one before calling "
                             "download_and_install_software."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "What to search for, e.g. 'ResEdit' or 'MacPaint'."},
                },
                "required": ["query"],
            },
        },
        {
            "name": "download_and_install_software",
            "description": ("Download a specific piece of software (a URL or archive.org "
                             "identifier/details-URL from a prior search_software result) and "
                             "install it on the Mac SE. Only call this after the user has "
                             "explicitly picked one specific result -- never call this "
                             "speculatively or with a guessed URL."),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": ("Direct download URL, or an archive.org details "
                                             "URL/identifier from a prior search_software result.")},
                    "suggested_name": {"type": "string",
                                        "description": "Optional short name for the installed file/app."},
                },
                "required": ["url"],
            },
        },
    ],
}]


# ---------------------------------------------------------------------------
# Gemini API calls, with function-calling loop
# ---------------------------------------------------------------------------

MAX_TOOL_ROUNDS = 5

_DESCRIBE_CALL = {
    "delete_file": lambda a: f"delete '{a.get('path', '?')}'",
    "download_and_install_software":
        lambda a: f"download and install '{a.get('suggested_name') or a.get('url', '?')}'",
}


def _describe_tool_call(name, fargs):
    describer = _DESCRIBE_CALL.get(name)
    if describer:
        try:
            return describer(fargs)
        except Exception:
            pass
    return f"call {name}({fargs})"


def _generate_content(history):
    """POST history + tool declarations to Gemini. Returns the parsed JSON
    response dict on success, or a ready-to-display error string on
    failure (callers check isinstance(result, str))."""
    payload = {
        "contents": history,
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "tools": TOOLS,
    }

    req = urllib.request.Request(
        GEMINI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-goog-api-key": GEMINI_API_KEY,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return f"[gemini API error {e.code}: {body}]"
    except urllib.error.URLError as e:
        return f"[gemini API unreachable: {e.reason}]"
    except socket.timeout:
        return "[gemini API timed out]"


def _call_gemini_with_tools_inner(conn, lines, history) -> str:
    if not GEMINI_API_KEY:
        return "[GEMINI_API_KEY is not set in the bridge's environment]"

    for _ in range(MAX_TOOL_ROUNDS):
        response = _generate_content(history)
        if isinstance(response, str):
            return response

        try:
            parts = response["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            return f"[unexpected API response: {json.dumps(response)[:300]}]"

        fc_part = next((p for p in parts if "functionCall" in p), None)
        if fc_part is None:
            text = "".join(p.get("text", "") for p in parts).strip()
            return text or "[empty response]"

        fc = fc_part["functionCall"]
        name = fc.get("name", "")
        fargs = fc.get("args") or {}
        history.append({"role": "model", "parts": [fc_part]})

        if name in DESTRUCTIVE_TOOLS:
            desc = _describe_tool_call(name, fargs)
            conn.sendall(to_mac_lines(
                f"\r\nGemini wants to: {desc}. Type yes to confirm, "
                f"anything else to cancel.\r\n"))
            try:
                answer = next(lines)
            except StopIteration:
                answer = ""
            if answer.strip().lower() not in ("yes", "y"):
                result = {"success": False,
                          "message": ("the user explicitly declined this action when asked to "
                                       "confirm -- simply acknowledge the cancellation, do not "
                                       "invent or guess a technical reason for it")}
            else:
                fn = TOOL_DISPATCH.get(name)
                result = fn(fargs) if fn else {"error": f"unknown tool {name}"}
        else:
            fn = TOOL_DISPATCH.get(name)
            result = fn(fargs) if fn else {"error": f"unknown tool {name}"}

        history.append({"role": "user", "parts": [
            {"functionResponse": {"name": name, "response": result}}]})

    return "[stopped after too many tool calls in a row -- try rephrasing]"


def call_gemini_with_tools(conn, lines, history) -> str:
    reply_text = _call_gemini_with_tools_inner(conn, lines, history)
    history.append({"role": "model", "parts": [{"text": reply_text}]})
    return reply_text


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

def handle_client(conn: socket.socket, addr):
    print(f"[+] connection from {addr}")
    lines = iter_lines(conn)

    if not authenticate(conn, lines):
        print(f"    {addr} failed authentication")
        conn.close()
        return

    conn.sendall(BANNER.encode("ascii"))
    history = []

    try:
        for line in lines:
            if not line:
                conn.sendall(b"\r\n> ")
                continue

            print(f"    {addr} > {line}")

            if line.lower() in ("/quit", "/exit", "quit", "exit"):
                conn.sendall(b"\r\nbye!\r\n")
                conn.close()
                return

            if line.lower() == "/reset":
                history.clear()
                conn.sendall(b"\r\n[history cleared]\r\n> ")
                continue

            history.append({"role": "user", "parts": [{"text": line}]})
            reply = call_gemini_with_tools(conn, lines, history)
            if len(history) > 20:
                history[:] = history[-20:]

            conn.sendall(b"\r\n")
            conn.sendall(to_mac_lines(reply))
            conn.sendall(b"\r\n\r\n> ")

    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        print(f"[-] disconnected {addr}")
        conn.close()


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(5)
    print(f"Listening on {HOST}:{PORT} ... (Ctrl+C to stop)")

    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.close()
        sys.exit(0)


if __name__ == "__main__":
    main()
