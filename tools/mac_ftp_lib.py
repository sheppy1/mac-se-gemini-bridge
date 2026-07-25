#!/usr/bin/env python3
"""
Reusable library for managing files on the Mac SE's FTP server (NetPresenz
on System 7.1, NCSA Telnet's built-in server on System 6.0.8).

Bakes in everything learned the hard way about this specific setup:
  - MUST use active-mode FTP. This Mac runs classic MacTCP (not Open
    Transport -- the SE's 68000 CPU is below OT's 68030 minimum), which
    handles passive-mode FTP unreliably (stalls/hangs). Python's ftplib
    defaults to passive; this module explicitly forces active mode.
  - NetPresenz auto-encodes/decodes MacBinary on the fly based on filename
    suffix: request/send "name.bin" and it transparently converts between
    a plain Unix-style file and a full Mac file (data fork + resource fork
    + type/creator) using MacBinary as the wire format. That's used here
    for upload_app so a real application's resource fork survives the trip
    without needing a separate encode/decode step against a local HFS
    volume.
  - Connection is genuinely flaky on this vintage hardware/network path,
    and a failed operation can leave a control connection in a broken,
    unrecoverable state (observed: further reads on the same connection
    fail immediately with "cannot read from timed out object" even though
    the server side is fine). So every operation retries with a *fresh*
    connection each attempt, not a retry on the same connection.

This module has no CLI of its own -- see mac_ftp.py for that. Every
function here takes host/user/password explicitly so callers (the CLI,
or bridge.py's tool-calling code) don't share global state.
"""
import datetime
import ftplib
import io
import os
import sys
import time

RETRIES = 4
RETRY_DELAY_S = 3
TIMEOUT_S = 30


def connect_once(host, user, password):
    ftp = ftplib.FTP(timeout=TIMEOUT_S)
    ftp.connect(host, 21)
    ftp.login(user, password)
    ftp.set_pasv(False)  # active mode -- required, see module docstring
    return ftp


def with_retry(desc, host, user, password, fn):
    """Run fn(ftp) against a *freshly established* connection, retrying
    with a brand new connection each time on failure. Deliberately does not
    reuse a connection across attempts -- see module docstring."""
    last_err = None
    for attempt in range(1, RETRIES + 1):
        ftp = None
        try:
            ftp = connect_once(host, user, password)
            result = fn(ftp)
            try:
                ftp.quit()
            except Exception:
                ftp.close()
            return result
        except (OSError, *ftplib.all_errors) as e:
            last_err = e
            print(f"  [{desc} attempt {attempt}/{RETRIES} failed: {e}, retrying]",
                  file=sys.stderr)
            if ftp is not None:
                try:
                    ftp.close()
                except Exception:
                    pass
            time.sleep(RETRY_DELAY_S)
    raise SystemExit(f"{desc} failed after {RETRIES} attempts: {last_err}")


def list_dir(host, user, password, path=""):
    """Return the LIST output for path (or the login directory) as a list
    of raw lines (Unix-style ls -l format, as NetPresenz/NCSA emit it)."""
    def do(ftp):
        lines = []
        ftp.retrlines(f"LIST {path}".strip(), lines.append)
        return lines

    return with_retry("LIST", host, user, password, do)


def pwd(host, user, password):
    return with_retry("PWD", host, user, password, lambda ftp: ftp.pwd())


def download(host, user, password, remote, local, macbinary=False):
    """Download remote to local. If macbinary and remote doesn't already
    look like a MacBinary filename, request "remote.bin" instead so the
    server auto-encodes it (preserves resource fork/type/creator)."""
    if macbinary and not remote.lower().endswith((".bin", ".mb", ".macbin", ".macbinary")):
        remote = remote + ".bin"

    def do(ftp):
        with open(local, "wb") as f:
            ftp.retrbinary(f"RETR {remote}", f.write)

    with_retry(f"GET {remote}", host, user, password, do)
    return os.path.getsize(local)


def upload(host, user, password, local, remote=None):
    """Upload a plain file (data fork only, no Mac metadata)."""
    remote = remote or os.path.basename(local)

    def do(ftp):
        with open(local, "rb") as f:
            ftp.storbinary(f"STOR {remote}", f)

    with_retry(f"PUT {remote}", host, user, password, do)
    return remote


def upload_bytes(host, user, password, data: bytes, remote):
    """Upload raw bytes (e.g. an already-built MacBinary blob) as remote."""
    def do(ftp):
        ftp.storbinary(f"STOR {remote}", io.BytesIO(data))

    with_retry(f"PUT {remote}", host, user, password, do)
    return remote


def upload_app(host, user, password, data_path, rsrc_path, name, file_type,
                creator, flags=0x2100, when=None, remote_dir=""):
    """Build a MacBinary file from separate data/resource fork files and
    upload it as "name.bin" so NetPresenz/NCSA Telnet auto-decodes it
    server-side into a real Mac file (see module docstring)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from make_macbinary import make_macbinary  # noqa: E402

    def read_or_empty(path):
        if not path:
            return b""
        with open(path, "rb") as f:
            return f.read()

    when = when or datetime.datetime.now()
    data_fork = read_or_empty(data_path)
    rsrc_fork = read_or_empty(rsrc_path)
    blob = make_macbinary(name, file_type, creator, flags, data_fork, rsrc_fork, when)

    remote_dir = (remote_dir.rstrip("/") + "/") if remote_dir else ""
    remote = f"{remote_dir}{name}.bin"
    upload_bytes(host, user, password, blob, remote)
    return remote, len(data_fork), len(rsrc_fork)


def delete_file(host, user, password, remote):
    with_retry(f"DELE {remote}", host, user, password, lambda ftp: ftp.delete(remote))


def make_dir(host, user, password, remote):
    with_retry(f"MKD {remote}", host, user, password, lambda ftp: ftp.mkd(remote))


def remove_dir(host, user, password, remote):
    with_retry(f"RMD {remote}", host, user, password, lambda ftp: ftp.rmd(remote))


def rename(host, user, password, old, new):
    with_retry(f"RNFR/RNTO {old} -> {new}", host, user, password,
               lambda ftp: ftp.rename(old, new))
