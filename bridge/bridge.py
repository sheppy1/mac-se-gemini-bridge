#!/usr/bin/env python3
"""
A tiny Telnet-to-Gemini bridge for talking to Gemini from a vintage Mac
terminal client (e.g. BetterTelnet/NCSA Telnet on a Mac SE over MacTCP).

Listens on a plain TCP port. Strips/answers just enough Telnet IAC
negotiation to keep real telnet clients happy, reads lines of text, and
forwards each line (with growing conversation context) directly to the
Gemini API over HTTPS (google's REST endpoint, no CLI/Node dependency).
"""
import hmac
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import urllib.error

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

IAC = 0xFF
WILL, WONT, DO, DONT = 0xFB, 0xFC, 0xFD, 0xFE
SB, SE = 0xFA, 0xF0

BANNER = (
    "\r\n"
    "=== Gemini terminal bridge ===\r\n"
    "Type a message and press Return. Type /quit to disconnect,\r\n"
    "/reset to clear conversation history.\r\n\r\n> "
)


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


SYSTEM_PROMPT = (
    "You are chatting over a slow 1987 Macintosh SE terminal connection. "
    "Keep replies short and plain: no markdown, no code fences, no emoji, "
    "prefer plain ASCII. Wrap naturally; the terminal is roughly 80 columns."
)


def call_gemini(history) -> str:
    if not GEMINI_API_KEY:
        return "[GEMINI_API_KEY is not set in the bridge's environment]"

    contents = []
    for role, text in history:
        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": text}],
        })

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
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
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return f"[gemini API error {e.code}: {body}]"
    except urllib.error.URLError as e:
        return f"[gemini API unreachable: {e.reason}]"
    except socket.timeout:
        return "[gemini API timed out]"

    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or "[empty response]"
    except (KeyError, IndexError):
        return f"[unexpected API response: {json.dumps(data)[:300]}]"


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

            history.append(("user", line))
            reply = call_gemini(history)
            history.append(("assistant", reply))
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
