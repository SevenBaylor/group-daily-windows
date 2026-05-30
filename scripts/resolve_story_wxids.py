#!/usr/bin/env python3
"""从 story.json 自动批量解析所有 cast/highlights 人物的 wxid（Windows 适配版）。

用法:
    python resolve_story_wxids.py --story /tmp/story.json --group "<群名>"

行为:
  1. 扫 story.json，收集所有 name
  2. 调用 wechat_windows.py members 拉群全员
  3. 三档匹配：nick_name 完全相等 -> remark 完全相等 -> 子串包含
  4. 缺漏的 -> 全库搜联系人
  5. 把 wxid 写回 story.json
"""
import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def load_group_members(group_name: str) -> list[dict]:
    """调用 wechat_windows.py 获取群成员"""
    from wechat_windows import WeChatWindows
    wx = WeChatWindows()
    ok, err = wx.init()
    if not ok:
        sys.exit(f"微信数据初始化失败: {err}")

    members, err = wx.get_group_members(group_name)
    if err:
        sys.exit(f"获取群成员失败: {err}")
    if not members:
        sys.exit(f"群 '{group_name}' 没有找到成员")
    return members


def resolve_name_in_group(name: str, members: list[dict]) -> str | None:
    """三档匹配 name -> wxid"""
    # 档1: nick_name 完全相等
    for m in members:
        if m.get("nick_name") == name:
            return m["username"]
    # 档2: remark 完全相等
    for m in members:
        if m.get("remark") == name:
            return m["username"]
    # 档3: nick_name 包含
    for m in members:
        nick = m.get("nick_name", "")
        if nick and (name in nick or nick in name):
            return m["username"]
    # 档4: remark 包含
    for m in members:
        remark = m.get("remark", "")
        if remark and (name in remark or remark in name):
            return m["username"]
    return None


def fallback_contacts_search(name: str) -> list[tuple[str, str]]:
    """全库搜索联系人"""
    from wechat_windows import WeChatWindows
    wx = WeChatWindows()
    ok, err = wx.init()
    if not ok:
        return []

    results, err = wx.search_contacts(name)
    if err:
        return []
    return results


def collect_names(story: dict) -> list[str]:
    seen = []
    for s in story.get("timeline", []):
        for c in s.get("cast", []):
            n = c.get("name")
            if n and n not in seen:
                seen.append(n)
    for h in story.get("highlights", []):
        n = h.get("name")
        if n and n not in seen:
            seen.append(n)
    return seen


def inject_wxids(story: dict, name_to_wxid: dict[str, str]) -> int:
    count = 0
    for s in story.get("timeline", []):
        for c in s.get("cast", []):
            if not c.get("wxid"):
                w = name_to_wxid.get(c["name"])
                if w:
                    c["wxid"] = w
                    count += 1
    for h in story.get("highlights", []):
        if not h.get("wxid"):
            w = name_to_wxid.get(h["name"])
            if w:
                h["wxid"] = w
                count += 1
    return count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--story", required=True, help="story.json 路径（会就地改写）")
    ap.add_argument("--group", required=True, help="微信群名")
    ap.add_argument("--dry-run", action="store_true", help="只报告不改写")
    args = ap.parse_args()

    story_path = os.path.expanduser(args.story)
    with open(story_path, encoding="utf-8") as f:
        story = json.load(f)

    names = collect_names(story)
    print(f"> story 里共 {len(names)} 个独立人物名", file=sys.stderr)

    print(f"> 拉 {args.group} 群成员表...", file=sys.stderr)
    members = load_group_members(args.group)
    print(f"  群里 {len(members)} 人", file=sys.stderr)

    name_to_wxid: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        w = resolve_name_in_group(name, members)
        if w:
            name_to_wxid[name] = w
            print(f"  OK {name} -> {w}", file=sys.stderr)
        else:
            missing.append(name)
            print(f"  ?? {name} 群成员表里没找到", file=sys.stderr)

    if missing:
        print(f"\n> {len(missing)} 个名字不在群里，全库搜...", file=sys.stderr)
        for name in missing[:]:
            cands = fallback_contacts_search(name)
            if cands:
                print(f"  [{name}] 候选:", file=sys.stderr)
                for w, nick in cands[:5]:
                    print(f"    - {w} (nick: {nick})", file=sys.stderr)
                name_to_wxid[name] = cands[0][0]
                missing.remove(name)
                print(f"    -> 自动用 {cands[0][0]}", file=sys.stderr)
            else:
                print(f"  [{name}] 全库也找不到", file=sys.stderr)

    injected = inject_wxids(story, name_to_wxid)
    print(f"\n> 注入 {injected} 个 wxid", file=sys.stderr)

    if not args.dry_run:
        with open(story_path, "w", encoding="utf-8") as f:
            json.dump(story, f, ensure_ascii=False, indent=2)
        print(f"  ✅ 已写回 {story_path}", file=sys.stderr)

    if missing:
        print(f"\n❌ 仍有 {len(missing)} 个名字无法解析:", file=sys.stderr)
        for n in missing:
            print(f"    - {n}", file=sys.stderr)
        sys.exit(2)
    print(f"\n✅ 全部 {len(names)} 个名字都拿到 wxid", file=sys.stderr)


if __name__ == "__main__":
    main()
