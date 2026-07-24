#!/usr/bin/env python3
"""
CLI for managing files on the Mac SE's NetPresenz FTP server.

Bakes in everything learned the hard way about this specific setup:
  - MUST use active-mode FTP. This Mac runs classic MacTCP (not Open
    Transport -- the SE's 68000 CPU is below OT's 68030 minimum), which
    handles passive-mode FTP unreliably (stalls/hangs). Python's ftplib
    defaults to passive; this script explicitly forces active mode.
  - NetPresenz auto-encodes/decodes MacBinary on the fly based on filename
    suffix: request/send "name.bin" and it transparently converts between
    a plain Unix-style file and a full Mac file (data fork + resource fork
    + type/creator) using MacBinary as the wire format. That's used here
    for put-app/get-app so a real application's resource fork survives the
    trip without needing a separate encode/decode step against a local HFS
    volume.
  - Connection is genuinely flaky on this vintage hardware/network path,
    and a failed operation can leave a control connection in a broken,
    unrecoverable state (observed: further reads on the same connection
    fail immediately with "cannot read from timed out object" even though
    the server side is fine). So every operation retries with a *fresh*
    connection each attempt, not a retry on the same connection.

Configure the connection via environment variables (with sensible
defaults matching this project's setup) or CLI flags:
  MAC_FTP_HOST  (default 192.168.1.210)
  MAC_FTP_USER  (default anonymous)
  MAC_FTP_PASS  (default ftp@example.com)

Examples:
  python mac_ftp.py ls
  python mac_ftp.py ls "Startup Items"
  python mac_ftp.py get "Preferences/NetPresenz Preferences" ./prefs.bin --macbinary
  python mac_ftp.py put ./readme.txt "Documents/readme.txt"
  python mac_ftp.py put-app --data "MyApp/MyApp" --rsrc "MyApp/MyApp.rsrc" \\
      --name "MyApp" --remote-dir "Applications"
  python mac_ftp.py mkdir "Documents/New Folder"
  python mac_ftp.py rm "Documents/old.txt"
  python mac_ftp.py rename "Documents/a.txt" "Documents/b.txt"
"""
import argparse
import ftplib
import os
import sys
import time

DEFAULT_HOST = os.environ.get("MAC_FTP_HOST", "192.168.1.210")
DEFAULT_USER = os.environ.get("MAC_FTP_USER", "anonymous")
DEFAULT_PASS = os.environ.get("MAC_FTP_PASS", "ftp@example.com")

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


def cmd_ls(args):
    path = args.path or ""

    def do(ftp):
        lines = []
        ftp.retrlines(f"LIST {path}".strip(), lines.append)
        return lines

    for line in with_retry("LIST", args.host, args.user, args.password, do):
        print(line)


def cmd_pwd(args):
    print(with_retry("PWD", args.host, args.user, args.password, lambda ftp: ftp.pwd()))


def cmd_get(args):
    remote = args.remote
    if args.macbinary and not remote.lower().endswith((".bin", ".mb", ".macbin", ".macbinary")):
        remote = remote + ".bin"
    local = args.local or os.path.basename(remote)

    def do(ftp):
        with open(local, "wb") as f:
            ftp.retrbinary(f"RETR {remote}", f.write)

    with_retry(f"GET {remote}", args.host, args.user, args.password, do)
    print(f"wrote {local} ({os.path.getsize(local)} bytes)")


def cmd_put(args):
    remote = args.remote or os.path.basename(args.local)

    def do(ftp):
        with open(args.local, "rb") as f:
            ftp.storbinary(f"STOR {remote}", f)

    with_retry(f"PUT {remote}", args.host, args.user, args.password, do)
    print(f"uploaded {args.local} -> {remote}")


def cmd_put_app(args):
    """Upload an app's data+resource fork by building a MacBinary file with
    tools/make_macbinary.py and letting NetPresenz auto-decode it on STOR
    (any upload ending in .bin/.mb/.macbin/.macbinary is decoded server-side
    -- see NetPresenz's own documentation)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from make_macbinary import make_macbinary  # noqa: E402

    def read_or_empty(path):
        if not path:
            return b""
        with open(path, "rb") as f:
            return f.read()

    import datetime
    when = datetime.datetime.strptime(args.date, "%Y-%m-%d") if args.date \
        else datetime.datetime.now()

    data_fork = read_or_empty(args.data)
    rsrc_fork = read_or_empty(args.rsrc)
    blob = make_macbinary(args.name, args.type, args.creator, args.flags,
                           data_fork, rsrc_fork, when)

    remote_dir = (args.remote_dir.rstrip("/") + "/") if args.remote_dir else ""
    remote = f"{remote_dir}{args.name}.bin"

    def do(ftp):
        import io
        ftp.storbinary(f"STOR {remote}", io.BytesIO(blob))

    with_retry(f"PUT-APP {remote}", args.host, args.user, args.password, do)
    print(f"uploaded {args.name} (data={len(data_fork)}B rsrc={len(rsrc_fork)}B) "
          f"-> {remote} (NetPresenz will auto-decode to '{args.name}')")


def cmd_rm(args):
    with_retry(f"DELE {args.remote}", args.host, args.user, args.password,
               lambda ftp: ftp.delete(args.remote))
    print(f"deleted {args.remote}")


def cmd_mkdir(args):
    with_retry(f"MKD {args.remote}", args.host, args.user, args.password,
               lambda ftp: ftp.mkd(args.remote))
    print(f"created {args.remote}")


def cmd_rmdir(args):
    with_retry(f"RMD {args.remote}", args.host, args.user, args.password,
               lambda ftp: ftp.rmd(args.remote))
    print(f"removed {args.remote}")


def cmd_rename(args):
    with_retry(f"RNFR/RNTO {args.old} -> {args.new}", args.host, args.user, args.password,
               lambda ftp: ftp.rename(args.old, args.new))
    print(f"renamed {args.old} -> {args.new}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--user", default=DEFAULT_USER)
    p.add_argument("--password", default=DEFAULT_PASS)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("ls", help="list a directory")
    sp.add_argument("path", nargs="?", default="")
    sp.set_defaults(func=cmd_ls)

    sp = sub.add_parser("pwd", help="print current/login directory")
    sp.set_defaults(func=cmd_pwd)

    sp = sub.add_parser("get", help="download a file")
    sp.add_argument("remote")
    sp.add_argument("local", nargs="?")
    sp.add_argument("--macbinary", action="store_true",
                     help="request as .bin (preserves resource fork/type/creator)")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("put", help="upload a plain file (data fork only, no Mac metadata)")
    sp.add_argument("local")
    sp.add_argument("remote", nargs="?")
    sp.set_defaults(func=cmd_put)

    sp = sub.add_parser("put-app", help="upload an app with real resource fork/type/creator")
    sp.add_argument("--data", help="path to local data fork (omit if none)")
    sp.add_argument("--rsrc", help="path to local resource fork (omit if none)")
    sp.add_argument("--name", required=True)
    sp.add_argument("--type", required=True, help="4-char file type, e.g. APPL")
    sp.add_argument("--creator", required=True, help="4-char creator code")
    sp.add_argument("--flags", type=lambda x: int(x, 0), default=0x2100,
                     help="Finder flags, default 0x2100 (typical app)")
    sp.add_argument("--date", help="YYYY-MM-DD, default now")
    sp.add_argument("--remote-dir", default="", help="remote directory to upload into")
    sp.set_defaults(func=cmd_put_app)

    sp = sub.add_parser("rm", help="delete a file")
    sp.add_argument("remote")
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("mkdir", help="create a directory")
    sp.add_argument("remote")
    sp.set_defaults(func=cmd_mkdir)

    sp = sub.add_parser("rmdir", help="remove an (empty) directory")
    sp.add_argument("remote")
    sp.set_defaults(func=cmd_rmdir)

    sp = sub.add_parser("rename", help="rename/move a file or directory")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.set_defaults(func=cmd_rename)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
