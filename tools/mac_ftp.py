#!/usr/bin/env python3
"""
CLI for managing files on the Mac SE's FTP server. Thin wrapper around
mac_ftp_lib.py -- see that module's docstring for the hard-won lessons
baked into the underlying connection/retry logic (active-mode FTP,
fresh-connection-per-retry, MacBinary auto-decode for apps).

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
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mac_ftp_lib as lib  # noqa: E402

DEFAULT_HOST = os.environ.get("MAC_FTP_HOST", "192.168.1.210")
DEFAULT_USER = os.environ.get("MAC_FTP_USER", "anonymous")
DEFAULT_PASS = os.environ.get("MAC_FTP_PASS", "ftp@example.com")


def cmd_ls(args):
    for line in lib.list_dir(args.host, args.user, args.password, args.path or ""):
        print(line)


def cmd_pwd(args):
    print(lib.pwd(args.host, args.user, args.password))


def cmd_get(args):
    local = args.local or os.path.basename(args.remote)
    size = lib.download(args.host, args.user, args.password, args.remote, local,
                         macbinary=args.macbinary)
    print(f"wrote {local} ({size} bytes)")


def cmd_put(args):
    remote = lib.upload(args.host, args.user, args.password, args.local, args.remote)
    print(f"uploaded {args.local} -> {remote}")


def cmd_put_app(args):
    when = datetime.datetime.strptime(args.date, "%Y-%m-%d") if args.date else None
    remote, data_len, rsrc_len = lib.upload_app(
        args.host, args.user, args.password,
        args.data, args.rsrc, args.name, args.type, args.creator,
        flags=args.flags, when=when, remote_dir=args.remote_dir)
    print(f"uploaded {args.name} (data={data_len}B rsrc={rsrc_len}B) "
          f"-> {remote} (NetPresenz will auto-decode to '{args.name}')")


def cmd_rm(args):
    lib.delete_file(args.host, args.user, args.password, args.remote)
    print(f"deleted {args.remote}")


def cmd_mkdir(args):
    lib.make_dir(args.host, args.user, args.password, args.remote)
    print(f"created {args.remote}")


def cmd_rmdir(args):
    lib.remove_dir(args.host, args.user, args.password, args.remote)
    print(f"removed {args.remote}")


def cmd_rename(args):
    lib.rename(args.host, args.user, args.password, args.old, args.new)
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
