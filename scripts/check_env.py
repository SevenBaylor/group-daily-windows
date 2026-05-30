#!/usr/bin/env python3
"""group-daily skill 环境自检（Windows 版）。

检查项:
    1. Windows 平台 + 微信运行状态
    2. Python 依赖: Pillow, pywxdump, openai-whisper (可选)
    3. PyWxDump 密钥提取
    4. 微信数据目录 + 解密验证
    5. Chrome / Edge 浏览器
    6. 环境变量
"""
import os
import platform
import re
import shutil
import sys
from pathlib import Path

# Windows GBK 编码兼容
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"


def ok(msg, detail=""):
    print(f"  {ANSI_GREEN}✅{ANSI_RESET} {msg}", end="")
    if detail:
        print(f"  {ANSI_DIM}{detail}{ANSI_RESET}")
    else:
        print()


def warn(msg, fix=""):
    print(f"  {ANSI_YELLOW}⚠️{ANSI_RESET}  {msg}")
    if fix:
        print(f"     {ANSI_DIM}修复: {fix}{ANSI_RESET}")


def fail(msg, fix=""):
    print(f"  {ANSI_RED}❌{ANSI_RESET} {msg}")
    if fix:
        print(f"     {ANSI_DIM}修复: {fix}{ANSI_RESET}")


def header(title):
    print(f"\n{ANSI_DIM}── {title} ─────────────{ANSI_RESET}")


def check_platform():
    header("平台")
    if platform.system() == "Windows":
        ok(f"Windows {platform.version()}")
        return True
    fail(f"系统是 {platform.system()}，Windows 适配版仅支持 Windows")
    return False


def check_python_deps():
    header("Python 依赖")
    has_pillow = False
    try:
        import PIL  # noqa: F401
        ok("Pillow")
        has_pillow = True
    except ImportError:
        fail("Pillow 未装（PNG 截图必需）", "pip install Pillow")

    has_pywxdump = False
    try:
        import pywxdump  # noqa: F401
        ok("pywxdump（微信数据解密）")
        has_pywxdump = True
    except ImportError:
        fail("pywxdump 未装（核心依赖，无法运行）",
             "pip install pywxdump")

    has_whisper = False
    try:
        import whisper  # noqa: F401
        ok("openai-whisper（语音转写）")
        has_whisper = True
    except ImportError:
        warn("openai-whisper 未装（无法转写语音；不需要语音功能可忽略）",
             "pip install openai-whisper")

    return has_pillow and has_pywxdump


def check_wechat_running():
    header("微信运行状态")
    try:
        from pywxdump import get_wx_info, WX_OFFS
        results = get_wx_info(WX_OFFS, is_print=False)
        if results:
            info = results[0]
            ok(f"微信正在运行 (PID: {info.get('pid')})")
            ok(f"  wxid: {info.get('wxid', '?')}")
            ok(f"  nickname: {info.get('nickname', '?')}")
            ok(f"  微信版本: {info.get('version', '?')}")

            if info.get("key"):
                ok(f"  密钥已提取 ({len(info['key'])} 位)")
            else:
                fail("  未能提取解密密钥",
                     "更新 PyWxDump: pip install --upgrade pywxdump")

            if info.get("wx_dir"):
                ok(f"  数据目录: {info['wx_dir']}")
            else:
                fail("  未能定位微信数据目录")
                return info

            return info
        else:
            fail("未检测到微信进程", "请先启动微信并登录")
            return None
    except Exception as e:
        fail(f"检查失败: {e}")
        return None


def check_databases(wx_info):
    header("微信数据库")
    if not wx_info or not wx_info.get("wx_dir"):
        fail("无微信数据目录信息")
        return False

    wx_dir = Path(wx_info["wx_dir"])
    msg_dir = wx_dir / "Msg"

    ok_count = 0
    for db in ["MicroMsg.db", "HardLinkImage.db"]:
        path = msg_dir / db
        if path.exists():
            size_kb = path.stat().st_size / 1024
            ok(f"{db} ({size_kb:.0f} KB)")
            ok_count += 1
        else:
            warn(f"{db} 未找到", f"检查 {msg_dir}")

    # Windows: MSG*.db 在 Msg/Multi/ 子目录
    multi_dir = msg_dir / "Multi"
    msg_found = False
    if multi_dir.exists():
        for f in multi_dir.iterdir():
            if re.match(r'^MSG\d+\.db$', f.name):
                size_kb = f.stat().st_size / 1024
                ok(f"Multi/{f.name} ({size_kb:.0f} KB)")
                msg_found = True
                ok_count += 1
                break
    if not msg_found:
        warn("MSG*.db 未找到（消息数据库）", f"检查 {multi_dir}")

    return ok_count >= 2


def check_browser():
    header("浏览器（HTML -> PNG 用）")
    candidates = [
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            ok(f"找到: {p}")
            return True

    if shutil.which("chrome") or shutil.which("msedge"):
        ok("在 PATH 中找到 chrome/msedge")
        return True

    fail("没找到 Chrome / Edge", "从 https://www.google.com/chrome/ 下载")
    return False


def check_env_vars():
    header("环境变量（可选）")
    gd_vault = os.environ.get("GROUP_DAILY_VAULT")
    if gd_vault:
        path = Path(os.path.expanduser(gd_vault))
        if path.exists():
            ok(f"GROUP_DAILY_VAULT: {path}")
        else:
            warn(f"GROUP_DAILY_VAULT 指向不存在的目录: {path}",
                 f"创建目录或修改环境变量")
    else:
        default = Path.home() / "Documents/GroupDaily"
        warn(f"GROUP_DAILY_VAULT 未设，将用默认值 {default}",
             "如需自定义归档路径，set GROUP_DAILY_VAULT=你的路径")


def main():
    print(f"{ANSI_DIM}group-daily skill 环境自检 (Windows){ANSI_RESET}")

    plat_ok = check_platform()
    py_ok = check_python_deps()
    wx_info = check_wechat_running() if py_ok else None
    db_ok = check_databases(wx_info) if wx_info else False
    br_ok = check_browser()
    check_env_vars()

    print()
    if all([plat_ok, py_ok, wx_info, db_ok, br_ok]):
        print(f"{ANSI_GREEN}✅ Windows 环境就绪，可以运行 group-daily。{ANSI_RESET}")
    elif py_ok and wx_info and db_ok and br_ok:
        print(f"{ANSI_YELLOW}⚠️ 基本可用，但存在警告，查看上方提示。{ANSI_RESET}")
    else:
        print(f"{ANSI_RED}❌ 必装项缺失，按上方提示修复后重试。{ANSI_RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
