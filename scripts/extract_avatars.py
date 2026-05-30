#!/usr/bin/env python3
"""导出指定 wxid 列表的头像为 base64 data URI JSON（Windows 适配版）。

用法:
    python extract_avatars.py --wxids wxid1,wxid2,... --out /tmp/avatars.json
    python extract_avatars.py --names-map names.json --out /tmp/avatars.json

Windows 头像来源:
  1. wechat_windows.py 适配器（在线查询：HardLinkImage.db + FileStorage）
  2. HardLinkImage.db（解密后直接查）
  3. FileStorage 目录（兜底扫描）
"""
import argparse
import base64
import json
import os
import sqlite3
import sys
from pathlib import Path


def resolve_default_db() -> str:
    for env in ("GROUP_DAILY_DECRYPT_DIR",):
        v = os.environ.get(env)
        if v:
            p = os.path.expanduser(os.path.join(v, "HardLinkImage.db"))
            if os.path.exists(p):
                return p

    import tempfile
    tmp = Path(tempfile.gettempdir()) / "group_daily_decrypted"
    if tmp.exists():
        for subdir in tmp.iterdir():
            p = subdir / "HardLinkImage.db"
            if p.exists():
                return str(p)

    return ""


DEFAULT_DB = resolve_default_db()


def load_wxids(args):
    if args.names_map:
        with open(args.names_map, encoding="utf-8") as f:
            mapping = json.load(f)
        return mapping
    if args.wxids_file:
        with open(args.wxids_file, encoding="utf-8") as f:
            wxids = [line.strip() for line in f if line.strip()]
        return {w: w for w in wxids}
    if args.wxids:
        return {w.strip(): w.strip() for w in args.wxids.split(",") if w.strip()}
    sys.exit("需要提供 --wxids / --wxids-file / --names-map 之一")


def detect_mime(buf: bytes) -> str:
    if buf[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if buf[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if buf[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def image_to_base64(filepath: str) -> str | None:
    try:
        with open(filepath, "rb") as f:
            data = f.read()
        if len(data) < 100:
            return None
        mime = detect_mime(data)
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


def find_avatar_in_storage(wx_dir: str, wxid: str = None) -> list[str]:
    """在 FileStorage/Image 中搜索头像文件"""
    paths = []
    img_dir = Path(wx_dir) / "FileStorage" / "Image"
    if not img_dir.exists():
        return paths

    for date_dir in sorted(img_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for img_file in date_dir.iterdir():
            if img_file.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif'):
                paths.append(str(img_file))
                if len(paths) >= 30:
                    return paths
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB, help="解密后的 HardLinkImage.db 路径")
    ap.add_argument("--wxids", help="逗号分隔的 wxid 列表")
    ap.add_argument("--wxids-file", help="每行一个 wxid 的文本文件")
    ap.add_argument("--names-map", help="JSON 文件，格式 {wxid: 显示名}")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    args = ap.parse_args()

    mapping = load_wxids(args)
    wxids = list(mapping.keys())

    # 优先使用 wechat_windows 在线查询
    if not os.path.exists(args.db):
        print("HardLinkImage.db 未找到，使用 wechat_windows 在线查询...", file=sys.stderr)
        from wechat_windows import WeChatWindows
        wx = WeChatWindows()
        ok, err = wx.init()
        if ok:
            result = wx.get_avatars(wxids)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
            print(f"\n✅ 导出 {len(result)} / {len(wxids)} 个头像 -> {args.out}", file=sys.stderr)
            return
        else:
            print(f"  wechat_windows 不可用: {err}", file=sys.stderr)

    result = {}

    # 从 HardLinkImage.db 查文件路径
    if os.path.exists(args.db):
        conn = sqlite3.connect(args.db)
        cur = conn.cursor()
        try:
            for wxid in wxids:
                cur.execute("SELECT FilePath FROM HardLinkImage WHERE UserName=? LIMIT 1", (wxid,))
                row = cur.fetchone()
                if row:
                    # 尝试多种路径组合
                    info = os.environ.get("GROUP_DAILY_WX_DIR", "")
                    if info:
                        full_path = os.path.join(info, "FileStorage", row[0])
                        if os.path.exists(full_path):
                            b64 = image_to_base64(full_path)
                            if b64:
                                result[mapping[wxid]] = b64
                                print(f"  OK {mapping[wxid]} ({wxid})", file=sys.stderr)
                                continue
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    # 兜底：扫描 FileStorage/Image
    wx_dir = os.environ.get("GROUP_DAILY_WX_DIR", "")
    if wx_dir:
        path_pool = find_avatar_in_storage(wx_dir)
        for wxid in wxids:
            if mapping[wxid] in result:
                continue
            for p in path_pool:
                b64 = image_to_base64(p)
                if b64:
                    result[mapping[wxid]] = b64
                    print(f"  generic {mapping[wxid]} -> {p}", file=sys.stderr)
                    break

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)

    print(f"\n✅ 导出 {len(result)} / {len(wxids)} 个头像 -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
