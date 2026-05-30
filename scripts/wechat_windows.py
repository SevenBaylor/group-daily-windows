#!/usr/bin/env python3
"""
Windows 微信数据适配器 — group-daily 的 Windows 数据访问层。

替代 macOS 专属的 vchat CLI，使用 PyWxDump 实现同等功能：
  - 从运行中的微信进程提取 SQLCipher 密钥
  - 解密数据库（MicroMsg.db / ChatMsg.db / ChatRoomUser.db 等）
  - 查询聊天记录、群成员、联系人、头像

用法（命令行）:
    python wechat_windows.py info                     # 显示微信运行信息
    python wechat_windows.py decrypt                  # 解密核心数据库
    python wechat_windows.py history <群名> [--limit N] [--asc]  # 导出聊天记录
    python wechat_windows.py members <群名> [--json]   # 列出群成员
    python wechat_windows.py contacts <关键词>          # 搜索联系人
    python wechat_windows.py avatars <wxid列表>        # 导出头像 base64

用法（Python API）:
    from wechat_windows import WeChatWindows
    wx = WeChatWindows()
    wx.init()                                          # 初始化：获取密钥+解密
    history = wx.get_chat_history("群名", limit=1000)
    members = wx.get_group_members("群名")
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ============================================================
# 核心适配类
# ============================================================

class WeChatWindows:
    """Windows 微信数据访问适配器"""

    def __init__(self, decrypt_dir=None):
        self._info = None          # 微信信息 dict
        self._decrypt_dir = None   # 解密输出目录
        self._wx_dir = None        # 微信数据目录
        self._wxid = None          # 当前微信用户 wxid
        self._key = None           # SQLCipher 密钥 (64位hex)
        self._override_decrypt_dir = decrypt_dir

    # ---- 初始化 ----

    def init(self, force_decrypt=False):
        """初始化：获取微信进程信息 + 解密核心数据库。成功返回 True。"""
        ok, err = self._get_wechat_info()
        if not ok:
            return False, err

        ok, err = self._decrypt_core_dbs(force=force_decrypt)
        if not ok:
            return False, err

        return True, None

    # ---- 公开查询 API ----

    def get_chat_history(self, group_name, limit=5000, asc=True):
        """获取群聊天记录，返回文本格式（兼容 vchat history 输出）。"""
        # 先查群 ChatMsg 表名
        group_info = self._find_group(group_name)
        if not group_info:
            return None, f"找不到群: {group_name}"

        chatroom_username = group_info["username"]
        display_name = group_info["nick_name"]

        msgs = self._query_messages(chatroom_username, limit, asc)

        if not msgs:
            # 尝试用 ChatMsg.db 里的数据
            msgs = self._query_messages_fallback(chatroom_username, limit, asc)

        if not msgs:
            return None, f"群 {display_name} 没有消息记录或数据库还未解密"

        lines = [f"# {display_name} ({chatroom_username})"]
        lines.append(f"# 共 {len(msgs)} 条消息\n")
        for ts, sender, content in msgs:
            sender_display = self._resolve_sender_name(sender)
            lines.append(f"[{ts}] {sender_display}: {content}")

        return "\n".join(lines), None

    def get_group_members(self, group_name):
        """获取群成员列表，返回 [{username, nick_name, remark}, ...]"""
        group_info = self._find_group(group_name)
        if not group_info:
            return None, f"找不到群: {group_name}"

        members = self._query_group_members(group_info)
        if not members:
            return None, f"无法获取群 {group_name} 的成员列表"

        return members, None

    def search_contacts(self, keyword):
        """搜索联系人，返回 [(wxid, nick_name), ...]"""
        micro_path = self._get_db_path("MicroMsg.db")
        if not micro_path or not os.path.exists(micro_path):
            return [], "MicroMsg.db 未解密"

        conn = sqlite3.connect(micro_path)
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT UserName, NickName, Remark FROM Contact "
                "WHERE NickName LIKE ? OR Remark LIKE ? OR Alias LIKE ? "
                "LIMIT 30",
                (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"),
            )
            results = []
            for username, nick, remark in cur.fetchall():
                display = remark or nick or username
                results.append((username, display))
            return results, None
        except sqlite3.OperationalError as e:
            return [], str(e)
        finally:
            conn.close()

    def get_avatars(self, wxids):
        """获取指定 wxid 列表的头像，返回 {display_name: data_uri}"""
        results = {}

        # 方法1: 从 HardLinkImage.db 查找头像图片路径
        hardlink_path = self._get_db_path("HardLinkImage.db")
        if hardlink_path and os.path.exists(hardlink_path):
            conn = sqlite3.connect(hardlink_path)
            cur = conn.cursor()
            try:
                # Windows 微信的 HardLinkImage 表结构
                for wxid in wxids:
                    cur.execute(
                        "SELECT FilePath FROM HardLinkImage "
                        "WHERE UserName=? LIMIT 1",
                        (wxid,),
                    )
                    row = cur.fetchone()
                    if row:
                        img_path = self._resolve_file_path(row[0])
                        if img_path and os.path.exists(img_path):
                            b64 = self._image_to_base64(img_path)
                            if b64:
                                results[wxid] = b64
            except Exception:
                pass
            finally:
                conn.close()

        # 方法2: 从 FileStorage 头像目录查找
        for wxid in wxids:
            if wxid in results:
                continue
            avatar_paths = self._find_avatar_in_storage(wxid)
            for p in avatar_paths:
                if os.path.exists(p):
                    b64 = self._image_to_base64(p)
                    if b64:
                        results[wxid] = b64
                        break

        return results

    def get_voice_messages(self, group_name, limit=200):
        """获取群语音消息列表。返回 [(local_id, MsgSvrID, ts, sender, duration_ms), ...]

        跨所有 MSG*.db 文件搜索（不只是最新的活跃文件）。
        """
        group_info = self._find_group(group_name)
        if not group_info:
            return None, f"找不到群: {group_name}"

        chatroom_username = group_info["username"]

        # 按需解密所有 MSG*.db（不只是 init 时解密的最新一个）
        import re as _re
        from pywxdump.wx_core.decryption import decrypt
        msg_dir = os.path.join(self._wx_dir, "Msg") if self._wx_dir else ""
        multi_dir = os.path.join(msg_dir, "Multi")
        decrypt_dir = self._decrypt_dir
        if not decrypt_dir:
            decrypt_dir = self._decrypt_dir = str(
                Path(tempfile.gettempdir()) / "group_daily_decrypted" / (self._wxid or "unknown")
            )
            Path(decrypt_dir).mkdir(parents=True, exist_ok=True)

        msg_dbs = []
        if os.path.exists(multi_dir):
            for f in sorted(os.listdir(multi_dir)):
                if _re.match(r'^MSG\d+\.db$', f):
                    src_path = os.path.join(multi_dir, f)
                    dst_path = os.path.join(decrypt_dir, f)
                    # 按需解密
                    if not os.path.exists(dst_path):
                        try:
                            ok, _ = decrypt(self._key, src_path, dst_path)
                            if ok:
                                msg_dbs.append(dst_path)
                        except Exception:
                            continue
                    else:
                        msg_dbs.append(dst_path)

        if not msg_dbs:
            return None, "MSG 数据库未解密"

        all_results = []
        for db_path in msg_dbs:
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                # 检查 MSG 表是否存在
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='MSG'")
                if not cur.fetchone():
                    conn.close()
                    continue
                cur.execute(
                    "SELECT localId, MsgSvrID, CreateTime, StrTalker, StrContent, BytesExtra, TalkerId, IsSender "
                    "FROM MSG WHERE StrTalker=? AND Type=34 "
                    "ORDER BY CreateTime DESC LIMIT ?",
                    (chatroom_username, limit),
                )
                for row in cur.fetchall():
                    ts = datetime.fromtimestamp(row["CreateTime"]).strftime("%Y-%m-%d %H:%M")
                    sender = self._resolve_sender_name(
                        self._extract_sender_from_msg(row)
                    )
                    content = WeChatWindows._to_str(row["StrContent"] or "")
                    dur_match = _re.search(r"voicelength=\"(\d+)\"", content)
                    duration_ms = int(dur_match.group(1)) if dur_match else 0
                    all_results.append({
                        "local_id": row["localId"],
                        "msg_svr_id": row["MsgSvrID"],
                        "time": ts,
                        "sender": sender,
                        "duration_ms": duration_ms,
                    })
                conn.close()
            except Exception:
                continue

            if len(all_results) >= limit:
                break

        all_results.sort(key=lambda x: x["time"], reverse=True)
        return all_results[:limit], None

    def extract_voice_audio(self, msg_svr_id, save_path=None):
        """提取单条语音的音频数据，解码 SILK → WAV。

        Args:
            msg_svr_id: MSG 表中的 MsgSvrID
            save_path: 保存 WAV 文件的路径（可选），不指定则返回 bytes

        Returns:
            (wav_bytes, None) 成功, (None, error_msg) 失败
        """
        # 1. 查找并解密 MediaMSG*.db
        media_db_path = self._find_media_db(msg_svr_id)
        if not media_db_path:
            return None, f"未找到包含 MsgSvrID={msg_svr_id} 的 MediaMSG 数据库"

        conn = sqlite3.connect(media_db_path)
        cur = conn.cursor()
        try:
            cur.execute("SELECT Buf FROM Media WHERE Reserved0=?", (msg_svr_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                return None, f"Media 表中未找到 MsgSvrID={msg_svr_id}"
            silk_data = row[0]
        except sqlite3.OperationalError as e:
            return None, str(e)
        finally:
            conn.close()

        # 2. SILK → WAV 解码
        try:
            from pywxdump.db.utils.common_utils import silk2audio
            wav_data = silk2audio(
                silk_data, is_play=False, is_wave=True,
                save_path=save_path, rate=24000,
            )
            return wav_data, None
        except ImportError:
            return None, "pysilk 未安装 (pip install silk-python)"
        except Exception as e:
            return None, f"SILK 解码失败: {e}"

    def export_all_voices(self, group_name, out_dir, limit=200):
        """导出群的所有语音消息为 WAV 文件。

        Returns:
            ([(local_id, wav_path, time, sender, duration_ms), ...], None) 成功
            (None, error_msg) 失败
        """
        voice_msgs, err = self.get_voice_messages(group_name, limit)
        if err:
            return None, err
        if not voice_msgs:
            return [], None

        out_path = Path(os.path.expanduser(out_dir))
        out_path.mkdir(parents=True, exist_ok=True)

        results = []
        for vm in voice_msgs:
            wav_file = out_path / f"voice_{vm['local_id']}_{vm['msg_svr_id']}.wav"
            _, err = self.extract_voice_audio(vm['msg_svr_id'], save_path=str(wav_file))
            if err:
                print(f"  ✗ local_id={vm['local_id']}: {err}", file=sys.stderr)
                continue
            results.append({
                "local_id": vm["local_id"],
                "wav_path": str(wav_file),
                "time": vm["time"],
                "sender": vm["sender"],
                "duration_ms": vm["duration_ms"],
            })
            print(f"  ✓ local_id={vm['local_id']} ({vm['duration_ms']}ms) → {wav_file.name}",
                  file=sys.stderr)

        return results, None

    def _find_media_db(self, msg_svr_id):
        """在所有 MediaMSG*.db 中查找包含指定 MsgSvrID 的数据库，必要时自动解密"""
        if not self._wx_dir or not self._key:
            return None

        import re as _re
        from pywxdump.wx_core.decryption import decrypt

        msg_dir = os.path.join(self._wx_dir, "Msg")
        multi_dir = os.path.join(msg_dir, "Multi")

        # 收集所有 MediaMSG*.db 文件（加密源文件）
        media_sources = []
        if os.path.exists(multi_dir):
            for fname in os.listdir(multi_dir):
                if _re.match(r'^MediaMSG\d+\.db$', fname):
                    media_sources.append(fname)

        # 从最新的开始尝试（优化：新语音更可能在最新文件中）
        media_sources.sort(reverse=True)

        decrypt_dir = self._decrypt_dir
        if not decrypt_dir:
            decrypt_dir = self._decrypt_dir = str(
                Path(tempfile.gettempdir()) / "group_daily_decrypted" / (self._wxid or "unknown")
            )
            Path(decrypt_dir).mkdir(parents=True, exist_ok=True)

        for fname in media_sources:
            dst_path = os.path.join(decrypt_dir, fname)

            # 按需解密
            if not os.path.exists(dst_path):
                src_path = os.path.join(multi_dir, fname)
                if os.path.exists(src_path):
                    try:
                        ok, _ = decrypt(self._key, src_path, dst_path)
                        if not ok:
                            continue
                    except Exception:
                        continue

            # 查询
            conn = sqlite3.connect(dst_path)
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM Media WHERE Reserved0=?",
                    (msg_svr_id,),
                )
                if cur.fetchone()[0] > 0:
                    conn.close()
                    return dst_path
            except sqlite3.OperationalError:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        return None

    def get_info(self):
        """返回当前微信信息 dict"""
        return self._info

    def get_decrypt_dir(self):
        """返回解密输出目录"""
        return self._decrypt_dir

    # ---- 内部方法 ----

    def _get_wechat_info(self):
        """通过 PyWxDump 获取微信进程信息"""
        try:
            from pywxdump import get_wx_info, WX_OFFS
        except ImportError:
            return False, "PyWxDump 未安装。请运行: pip install pywxdump"

        results = get_wx_info(WX_OFFS, is_print=False)

        if not results:
            return False, "未检测到微信运行。请先启动微信并登录。"

        # 使用第一个微信进程
        self._info = results[0]

        if not self._info.get("key"):
            return False, "未能提取微信密钥。可能是微信版本不兼容，请更新 PyWxDump。"

        self._wxid = self._info["wxid"]
        self._wx_dir = self._info["wx_dir"]
        self._key = self._info["key"]

        if not self._wx_dir:
            return False, "未能定位微信数据目录"

        return True, None

    def _decrypt_core_dbs(self, force=False):
        """解密核心数据库到临时目录"""
        from pywxdump.wx_core.decryption import decrypt

        if self._override_decrypt_dir:
            decrypt_dir = Path(os.path.expanduser(self._override_decrypt_dir))
        else:
            decrypt_dir = Path(tempfile.gettempdir()) / "group_daily_decrypted" / (self._wxid or "unknown")

        decrypt_dir.mkdir(parents=True, exist_ok=True)
        self._decrypt_dir = str(decrypt_dir)

        msg_dir = os.path.join(self._wx_dir, "Msg")
        multi_dir = os.path.join(msg_dir, "Multi")

        # 基础数据库
        needed_dbs = {
            "MicroMsg.db": os.path.join(msg_dir, "MicroMsg.db"),
            "HardLinkImage.db": os.path.join(msg_dir, "HardLinkImage.db"),
        }

        # 找到最新的 MSG 数据库（从 config.ini 读取或找最新文件）
        config_ini = os.path.join(multi_dir, "config.ini")
        latest_msg = None
        if os.path.exists(config_ini):
            try:
                with open(config_ini, "r", encoding="utf-8") as f:
                    latest_msg = f.read().strip()
            except Exception:
                pass

        if latest_msg and os.path.exists(os.path.join(multi_dir, latest_msg)):
            needed_dbs[latest_msg] = os.path.join(multi_dir, latest_msg)
        else:
            # 找最新修改的 MSG*.db
            msg_files = []
            if os.path.exists(multi_dir):
                for f in os.listdir(multi_dir):
                    if re.match(r'^MSG\d+\.db$', f):
                        fp = os.path.join(multi_dir, f)
                        msg_files.append((os.path.getmtime(fp), f, fp))
            if msg_files:
                msg_files.sort(reverse=True)
                _, latest, fp = msg_files[0]
                needed_dbs[latest] = fp
                self._active_msg_db = latest

        decrypted_count = 0
        for db_name, src_path in needed_dbs.items():
            dst_path = os.path.join(self._decrypt_dir, db_name)

            if os.path.exists(dst_path) and not force:
                if os.path.getmtime(dst_path) >= os.path.getmtime(src_path):
                    decrypted_count += 1
                    continue

            if not os.path.exists(src_path):
                continue

            try:
                ok, result = decrypt(self._key, src_path, dst_path)
                if ok:
                    decrypted_count += 1
                else:
                    print(f"  warning 解密失败 {db_name}: {result}", file=sys.stderr)
            except Exception as e:
                print(f"  warning 解密异常 {db_name}: {e}", file=sys.stderr)

        if decrypted_count == 0:
            return False, f"Failed to decrypt any database.\nSource: {msg_dir}\nTarget: {self._decrypt_dir}"

        # 确定活跃的 MSG 数据库文件名
        if not hasattr(self, '_active_msg_db') or not self._active_msg_db:
            for db_name in needed_dbs:
                if db_name.startswith("MSG") and db_name.endswith(".db"):
                    self._active_msg_db = db_name
                    break

        return True, None

    def _get_db_path(self, db_name):
        """获取解密后的数据库路径"""
        if not self._decrypt_dir:
            return None
        return os.path.join(self._decrypt_dir, db_name)

    def _find_group(self, group_name):
        """从 MicroMsg.db 查找联系人或群（优先群聊，其次个人）"""
        micro_path = self._get_db_path("MicroMsg.db")
        if not micro_path or not os.path.exists(micro_path):
            return None

        conn = sqlite3.connect(micro_path)
        cur = conn.cursor()

        try:
            # 第一轮：优先精确匹配群聊
            cur.execute(
                "SELECT UserName, NickName, Remark FROM Contact "
                "WHERE UserName LIKE '%@chatroom%' AND (NickName=? OR Remark=?)",
                (group_name, group_name),
            )
            row = cur.fetchone()
            if row:
                return {"username": row[0], "nick_name": row[1] or row[2] or group_name, "remark": row[2] or ""}

            # 第二轮：模糊匹配群聊
            cur.execute(
                "SELECT UserName, NickName, Remark FROM Contact "
                "WHERE UserName LIKE '%@chatroom%' AND (NickName LIKE ? OR Remark LIKE ?) "
                "LIMIT 10",
                (f"%{group_name}%", f"%{group_name}%"),
            )
            rows = cur.fetchall()
            if rows:
                r = rows[0]
                return {"username": r[0], "nick_name": r[1] or r[2] or group_name, "remark": r[2] or ""}

            # 第三轮：精确匹配 UserName（wxid）- 个人聊天
            cur.execute(
                "SELECT UserName, NickName, Remark FROM Contact "
                "WHERE UserName=?",
                (group_name,),
            )
            row = cur.fetchone()
            if row:
                return {"username": row[0], "nick_name": row[1] or row[2] or group_name, "remark": row[2] or ""}

            # 第四轮：匹配个人聊天（NickName / Remark / Alias）
            cur.execute(
                "SELECT UserName, NickName, Remark, Alias FROM Contact "
                "WHERE (NickName=? OR Remark=? OR Alias=?)",
                (group_name, group_name, group_name),
            )
            row = cur.fetchone()
            if row:
                return {"username": row[0], "nick_name": row[1] or row[2] or group_name, "remark": row[2] or ""}

            # 第五轮：模糊匹配个人聊天
            cur.execute(
                "SELECT UserName, NickName, Remark FROM Contact "
                "WHERE (NickName LIKE ? OR Remark LIKE ?) "
                "LIMIT 10",
                (f"%{group_name}%", f"%{group_name}%"),
            )
            rows = cur.fetchall()
            if rows:
                r = rows[0]
                return {"username": r[0], "nick_name": r[1] or r[2] or group_name, "remark": r[2] or ""}

            return None
        finally:
            conn.close()

    def _query_messages(self, chatroom_username, limit, asc):
        """从解密后的 MSG*.db 查询消息（WeChat v3.9+ 消息存储在 Multi/MSG*.db）"""
        # 找到解密后的 MSG*.db
        msg_db = self._find_msg_db()
        if not msg_db:
            return None

        conn = sqlite3.connect(msg_db)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            # 检查 MSG 表是否存在
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='MSG'")
            if not cur.fetchone():
                return None

            order = "ASC" if asc else "DESC"
            cur.execute(
                f"SELECT CreateTime, TalkerId, StrContent, StrTalker, Type, SubType, IsSender, BytesExtra "
                f"FROM MSG "
                f"WHERE StrTalker=? "
                f"ORDER BY CreateTime {order} "
                f"LIMIT ?",
                (chatroom_username, limit),
            )
            rows = cur.fetchall()

            if not rows:
                return None

            msgs = []
            for row in rows:
                ts = datetime.fromtimestamp(row["CreateTime"]).strftime("%Y-%m-%d %H:%M")
                sender = self._extract_sender_from_msg(row)
                content = self._format_msg_content(row)
                msgs.append((ts, sender, content))

            if asc:
                msgs.sort(key=lambda x: x[0])
            return msgs

        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()

    def _query_messages_fallback(self, chatroom_username, limit, asc):
        """备用：尝试解密目录中所有其他 MSG*.db 文件"""
        if not self._decrypt_dir:
            return None

        # 收集所有解密后的 MSG*.db
        msg_files = []
        for f in os.listdir(self._decrypt_dir):
            if re.match(r'^MSG\d+\.db$', f):
                msg_files.append(os.path.join(self._decrypt_dir, f))

        if not msg_files:
            return None

        all_msgs = []
        for msg_path in msg_files:
            # 跳过已经在主查询中使用的活跃 MSG
            if hasattr(self, '_active_msg_db') and os.path.basename(msg_path) == self._active_msg_db:
                continue

            conn = sqlite3.connect(msg_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='MSG'")
                if not cur.fetchone():
                    continue

                order = "ASC" if asc else "DESC"
                cur.execute(
                    f"SELECT CreateTime, TalkerId, StrContent, StrTalker, Type, SubType, IsSender, BytesExtra "
                    f"FROM MSG "
                    f"WHERE StrTalker=? "
                    f"ORDER BY CreateTime {order} "
                    f"LIMIT ?",
                    (chatroom_username, limit),
                )
                for row in cur.fetchall():
                    ts = datetime.fromtimestamp(row["CreateTime"]).strftime("%Y-%m-%d %H:%M")
                    sender = self._extract_sender_from_msg(row)
                    content = self._format_msg_content(row)
                    all_msgs.append((ts, sender, content))
            except sqlite3.OperationalError:
                continue
            finally:
                conn.close()

        if not all_msgs:
            return None

        all_msgs.sort(key=lambda x: x[0], reverse=not asc)
        return all_msgs[:limit]

    def _find_msg_db(self):
        """找到解密后的活跃 MSG*.db 文件"""
        if not self._decrypt_dir:
            return None

        # 优先使用 _decrypt_core_dbs 中记录的活跃 MSG
        if hasattr(self, '_active_msg_db') and self._active_msg_db:
            path = os.path.join(self._decrypt_dir, self._active_msg_db)
            if os.path.exists(path):
                return path

        # 否则找最新的 MSG*.db
        msg_files = []
        for f in os.listdir(self._decrypt_dir):
            if re.match(r'^MSG\d+\.db$', f):
                fp = os.path.join(self._decrypt_dir, f)
                msg_files.append((os.path.getmtime(fp), fp))

        if msg_files:
            msg_files.sort(reverse=True)
            return msg_files[0][1]

        return None

    @staticmethod
    def _to_str(val):
        """将 bytes 转为 str（SQLite BLOB 列可能返回 bytes）"""
        if isinstance(val, bytes):
            return val.decode("utf-8", errors="replace")
        return str(val) if val is not None else ""

    def _extract_sender_from_msg(self, row):
        """从 MSG 行提取发送者 wxid。

        群聊: 从 BytesExtra protobuf 提取真实发送者 → 回退 TalkerId
        私聊: IsSender=1 → 自己, IsSender=0 → StrTalker（对方）
        """
        str_talker = WeChatWindows._to_str(row["StrTalker"])
        is_group = str_talker.endswith("@chatroom")

        # IsSender=1 表示是自己发的
        if row["IsSender"] == 1:
            return self._wxid or "我"

        if is_group:
            # 群聊消息的真实发送者在 BytesExtra protobuf 中
            bytes_extra = row["BytesExtra"]
            if isinstance(bytes_extra, bytes) and bytes_extra:
                try:
                    import blackboxprotobuf
                    deserialize_data, _ = blackboxprotobuf.decode_message(bytes_extra)
                    if isinstance(deserialize_data, dict) and "3" in deserialize_data:
                        inner = deserialize_data["3"]
                        if isinstance(inner, list) and len(inner) > 0:
                            if isinstance(inner[0], dict) and "2" in inner[0]:
                                result = inner[0]["2"]
                                return WeChatWindows._to_str(result)
                except Exception:
                    pass

            # 回退：使用 TalkerId
            return WeChatWindows._to_str(row["TalkerId"]) or "未知"
        else:
            # 私聊：对方就是 StrTalker
            return str_talker or "未知"

    def _format_msg_content(self, row):
        """根据消息类型格式化消息内容"""
        msg_type = row["Type"]
        sub_type = row["SubType"]
        content = WeChatWindows._to_str(row["StrContent"] or "")

        type_id = (msg_type, sub_type)

        if type_id == (1, 0):
            # 文本消息
            return content
        elif type_id == (3, 0):
            return "[图片]"
        elif type_id == (34, 0):
            return "[语音]"
        elif type_id == (43, 0):
            return "[视频]"
        elif type_id == (47, 0):
            return "[动画表情]"
        elif type_id == (48, 0):
            return "[位置]"
        elif type_id == (49, 0):
            return "[文件]"
        elif type_id[0] == 49:
            # 分享链接/卡片/引用等
            if content:
                # 尝试提取 XML 中的标题
                import re as _re
                title_match = _re.search(r"<title>(.*?)</title>", content)
                if title_match:
                    return f"[分享] {title_match.group(1)}"
            return "[分享]"
        elif type_id == (50, 0):
            return "[语音/视频通话]"
        elif type_id == (10000, 0):
            return content if content else "[系统消息]"
        else:
            return content if content else f"[消息类型 {msg_type}/{sub_type}]"

    def _query_group_members(self, group_info):
        """从 MicroMsg.db 的 ChatRoom 表查群成员（解析 ^G 分隔的成员列表）"""
        chatroom_username = group_info["username"]

        micro_path = self._get_db_path("MicroMsg.db")
        if not micro_path or not os.path.exists(micro_path):
            return None

        conn = sqlite3.connect(micro_path)
        cur = conn.cursor()

        try:
            # ChatRoom 表：UserNameList 和 DisplayNameList 是 ^G 分隔的并行数组
            cur.execute(
                "SELECT UserNameList, DisplayNameList FROM ChatRoom "
                "WHERE ChatRoomName=?",
                (chatroom_username,),
            )
            row = cur.fetchone()
            if not row:
                return None

            user_names = row[0].split("^G") if row[0] else []
            display_names = row[1].split("^G") if row[1] else []

            # 构建 Contact 查询以获得备注名
            members = []
            for i, wxid in enumerate(user_names):
                display_name = display_names[i] if i < len(display_names) else ""
                members.append({
                    "username": wxid,
                    "nick_name": display_name or wxid,
                    "remark": "",
                })

            # 批量查 Remark（Contact 表中的备注）
            if members:
                wxid_list = [m["username"] for m in members]
                placeholders = ",".join(["?" for _ in wxid_list])
                cur.execute(
                    f"SELECT UserName, NickName, Remark FROM Contact "
                    f"WHERE UserName IN ({placeholders})",
                    wxid_list,
                )
                contact_map = {}
                for crow in cur.fetchall():
                    contact_map[crow[0]] = (crow[1] or "", crow[2] or "")

                for m in members:
                    if m["username"] in contact_map:
                        nick, remark = contact_map[m["username"]]
                        if remark:
                            m["remark"] = remark
                        if nick and not m["nick_name"]:
                            m["nick_name"] = nick

            return members

        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()

    def _resolve_sender_name(self, sender_wxid):
        """把 wxid 转成显示名（从缓存的联系人中查找）"""
        if not hasattr(self, '_contact_cache'):
            self._contact_cache = {}
            micro_path = self._get_db_path("MicroMsg.db")
            if micro_path and os.path.exists(micro_path):
                conn = sqlite3.connect(micro_path)
                cur = conn.cursor()
                try:
                    cur.execute("SELECT UserName, NickName, Remark FROM Contact LIMIT 10000")
                    for uname, nick, remark in cur.fetchall():
                        self._contact_cache[uname] = remark or nick or uname
                except Exception:
                    pass
                finally:
                    conn.close()

        return self._contact_cache.get(sender_wxid, sender_wxid)

    def _resolve_file_path(self, relative_path):
        """把微信相对文件路径解析为绝对路径"""
        if not self._wx_dir:
            return None
        # 微信图片路径通常在 FileStorage 下
        return os.path.join(self._wx_dir, "FileStorage", relative_path)

    def _find_avatar_in_storage(self, wxid):
        """在 FileStorage 中搜索头像文件"""
        paths = []
        base = Path(self._wx_dir) / "FileStorage" / "Image" if self._wx_dir else None
        if not base or not base.exists():
            return paths

        # 尝试在 Image 目录下的日期子目录中查找
        for date_dir in sorted(base.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for img_file in date_dir.iterdir():
                if img_file.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif'):
                    paths.append(str(img_file))
                    if len(paths) >= 20:
                        return paths
        return paths

    def _image_to_base64(self, path):
        """读取图片文件，返回 base64 data URI"""
        import base64
        try:
            with open(path, "rb") as f:
                data = f.read()
            if len(data) < 100:
                return None

            # 检测 MIME
            if data[:3] == b"\xff\xd8\xff":
                mime = "image/jpeg"
            elif data[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            elif data[:6] in (b"GIF87a", b"GIF89a"):
                mime = "image/gif"
            else:
                mime = "image/jpeg"

            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except Exception:
            return None


# ============================================================
# CLI 入口（兼容原 vchat 命令行风格）
# ============================================================

def cmd_info(wx):
    """显示微信运行信息"""
    ok, err = wx.init()
    if not ok:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    info = wx.get_info()
    print(json.dumps({
        "wxid": info.get("wxid"),
        "nickname": info.get("nickname"),
        "account": info.get("account"),
        "mobile": info.get("mobile"),
        "wx_dir": info.get("wx_dir"),
        "version": info.get("version"),
        "decrypt_dir": wx.get_decrypt_dir(),
    }, ensure_ascii=False, indent=2))


def cmd_history(wx):
    """导出群聊天记录"""
    ok, err = wx.init()
    if not ok:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    text, err = wx.get_chat_history(
        CLI_ARGS.group_name,
        limit=CLI_ARGS.limit or 5000,
        asc=CLI_ARGS.asc,
    )
    if err:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)
    print(text)


def cmd_members(wx):
    """输出群成员列表"""
    ok, err = wx.init()
    if not ok:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    members, err = wx.get_group_members(CLI_ARGS.group_name)
    if err:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    if CLI_ARGS.json:
        print(json.dumps({"members": members}, ensure_ascii=False, indent=2))
    else:
        for m in members:
            print(f"{m['username']}\t{m['nick_name']}\t{m.get('remark', '')}")


def cmd_contacts(wx):
    """搜索联系人"""
    ok, err = wx.init()
    if not ok:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    results, err = wx.search_contacts(CLI_ARGS.keyword)
    if err:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    for wxid, name in results:
        print(f"{wxid}\t{name}")


def cmd_avatars(wx):
    """导出头像"""
    ok, err = wx.init()
    if not ok:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    wxids = [w.strip() for w in CLI_ARGS.wxids.split(",") if w.strip()]
    results = wx.get_avatars(wxids)

    if CLI_ARGS.out:
        out_path = os.path.expanduser(CLI_ARGS.out)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"✅ 导出 {len(results)}/{len(wxids)} 个头像 → {out_path}", file=sys.stderr)
    else:
        print(json.dumps({k: v[:80] + "..." for k, v in results.items()},
                         ensure_ascii=False, indent=2))


def cmd_voice_list(wx):
    """列出群语音消息"""
    ok, err = wx.init()
    if not ok:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    msgs, err = wx.get_voice_messages(CLI_ARGS.group_name, limit=CLI_ARGS.limit)
    if err:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)
    if not msgs:
        print("(无语音消息)", file=sys.stderr)
        return

    for vm in msgs:
        dur_s = vm["duration_ms"] / 1000
        print(f"[{vm['time']}] {vm['sender']}: [语音 {dur_s:.0f}s] "
              f"local_id={vm['local_id']} MsgSvrID={vm['msg_svr_id']}")


def cmd_voice_extract(wx):
    """导出群语音为 WAV 文件"""
    ok, err = wx.init()
    if not ok:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    print(f"> 查询 {CLI_ARGS.group_name} 的语音消息...", file=sys.stderr)
    results, err = wx.export_all_voices(
        CLI_ARGS.group_name, CLI_ARGS.out_dir, limit=CLI_ARGS.limit
    )
    if err:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    print(f"\n✅ 导出 {len(results)} 条语音 → {CLI_ARGS.out_dir}", file=sys.stderr)
    # 输出 JSON 供下游使用
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="Windows 微信数据适配器 (group-daily)")
    sub = ap.add_subparsers(dest="command")

    sub.add_parser("info", help="显示微信运行信息")

    p_hist = sub.add_parser("history", help="导出群聊天记录")
    p_hist.add_argument("group_name", help="群名")
    p_hist.add_argument("--limit", type=int, default=5000)
    p_hist.add_argument("--asc", action="store_true", default=True, help="时间升序")

    p_mem = sub.add_parser("members", help="列出群成员")
    p_mem.add_argument("group_name", help="群名")
    p_mem.add_argument("--json", action="store_true", help="JSON 格式输出")

    p_contact = sub.add_parser("contacts", help="搜索联系人")
    p_contact.add_argument("keyword", help="搜索关键词")

    p_av = sub.add_parser("avatars", help="导出头像")
    p_av.add_argument("wxids", help="逗号分隔的 wxid 列表")
    p_av.add_argument("--out", help="输出 JSON 路径")

    p_vlist = sub.add_parser("voice-list", help="列出群语音消息")
    p_vlist.add_argument("group_name", help="群名")
    p_vlist.add_argument("--limit", type=int, default=200)

    p_vext = sub.add_parser("voice-extract", help="导出群语音为 WAV 文件")
    p_vext.add_argument("group_name", help="群名")
    p_vext.add_argument("--out-dir", required=True, help="WAV 输出目录")
    p_vext.add_argument("--limit", type=int, default=200)

    global CLI_ARGS
    CLI_ARGS = ap.parse_args()

    if not CLI_ARGS.command:
        ap.print_help()
        sys.exit(1)

    wx = WeChatWindows()
    {
        "info": cmd_info,
        "history": cmd_history,
        "members": cmd_members,
        "contacts": cmd_contacts,
        "avatars": cmd_avatars,
        "voice-list": cmd_voice_list,
        "voice-extract": cmd_voice_extract,
    }[CLI_ARGS.command](wx)


CLI_ARGS = None

if __name__ == "__main__":
    main()
