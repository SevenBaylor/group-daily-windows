#!/usr/bin/env python3
"""根据群名查 chatroom 成员（Windows 适配版）。

用法:
    python lookup_members.py --group-name "<群名>"
    python lookup_members.py --group-name "XX群" --names 示例A,示例C --out /tmp/members.json

Windows 数据源优先级:
  1. GROUP_DAILY_DECRYPT_DIR 环境变量（解密后 DB 目录）
  2. 默认解密目录 %TEMP%/group_daily_decrypted/<wxid>/
  3. 直接调 wechat_windows.py 在线查询
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def _resolve_default_db() -> str:
    for env in ("GROUP_DAILY_DECRYPT_DIR",):
        v = os.environ.get(env)
        if v:
            p = os.path.expanduser(os.path.join(v, "MicroMsg.db"))
            if os.path.exists(p):
                return p

    tmp = Path(tempfile.gettempdir()) / "group_daily_decrypted"
    if tmp.exists():
        for subdir in tmp.iterdir():
            p = subdir / "MicroMsg.db"
            if p.exists():
                return str(p)

    return ""


DEFAULT_DB = _resolve_default_db()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB, help="解密后的 MicroMsg.db 路径")
    ap.add_argument("--group-name", required=True, help="群名")
    ap.add_argument("--names", help="逗号分隔的成员显示名。留空则尽量返回全部。")
    ap.add_argument("--out", help="输出 JSON 路径。留空则打印到 stdout。")
    args = ap.parse_args()

    if not args.db or not os.path.exists(args.db):
        # 降级到 wechat_windows 在线查询
        print("MicroMsg.db 未找到，使用 wechat_windows 在线查询...", file=sys.stderr)
        from wechat_windows import WeChatWindows
        wx = WeChatWindows()
        ok, err = wx.init()
        if not ok:
            sys.exit(f"微信数据初始化失败: {err}")

        members, err = wx.get_group_members(args.group_name)
        if err:
            sys.exit(f"获取群成员失败: {err}")

        if args.names:
            names = set(n.strip() for n in args.names.split(",") if n.strip())
            members = [m for m in members if m.get("nick_name") in names or m.get("remark") in names]

        output = {m.get("nick_name") or m.get("remark") or m["username"]: m["username"] for m in members}

        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            print(f"✅ 导出 {len(output)} 位成员 -> {args.out}", file=sys.stderr)
        else:
            json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
            print()
        return

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    # 1. 找群 id
    cur.execute(
        "SELECT UserName, NickName FROM Contact "
        "WHERE (NickName=? OR Remark=?) LIMIT 1",
        (args.group_name, args.group_name),
    )
    row = cur.fetchone()
    if not row:
        cur.execute(
            "SELECT UserName, NickName FROM Contact "
            "WHERE NickName LIKE ? LIMIT 5",
            (f"%{args.group_name}%",),
        )
        candidates = cur.fetchall()
        if not candidates:
            sys.exit(f"找不到群: {args.group_name}")
        if len(candidates) > 1:
            print("找到多个候选群，请用更精确的名字：", file=sys.stderr)
            for c in candidates:
                print(f"  - {c[0]} {c[1]}", file=sys.stderr)
            sys.exit(1)
        row = candidates[0]

    room_username = row[0]
    print(f"  群 username = {room_username}", file=sys.stderr)

    # 2. 拉成员（Windows WeChat: ChatRoomUser.db）
    cru_path = os.path.join(os.path.dirname(args.db), "ChatRoomUser.db")
    members = {}

    if os.path.exists(cru_path):
        cru_conn = sqlite3.connect(cru_path)
        cru_cur = cru_conn.cursor()
        try:
            if args.names:
                names = [n.strip() for n in args.names.split(",") if n.strip()]
                for name in names:
                    cru_cur.execute(
                        "SELECT UserName, NickName, Remark FROM ChatRoomUser "
                        "WHERE ChatRoomName=? AND (NickName=? OR Remark=?)",
                        (room_username, name, name),
                    )
                    for uname, nick, remark in cru_cur.fetchall():
                        display = remark or nick or uname
                        members[display] = uname
            else:
                cru_cur.execute(
                    "SELECT UserName, NickName, Remark FROM ChatRoomUser "
                    "WHERE ChatRoomName=? LIMIT 1000",
                    (room_username,),
                )
                for uname, nick, remark in cru_cur.fetchall():
                    display = remark or nick or uname
                    members[display] = uname
        except sqlite3.OperationalError:
            pass
        finally:
            cru_conn.close()

    if members:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(members, f, ensure_ascii=False, indent=2)
            print(f"✅ 导出 {len(members)} 位成员 -> {args.out}", file=sys.stderr)
        else:
            json.dump(members, sys.stdout, ensure_ascii=False, indent=2)
            print()
    else:
        print("未找到群成员（ChatRoomUser.db 可能不存在或为空）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
