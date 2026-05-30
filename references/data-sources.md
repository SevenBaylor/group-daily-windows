# 数据源说明（Windows 版）

群日报需要从三处取数据：聊天记录、群成员、群成员头像。

## 一、聊天记录（wechat_windows.py）

工具：`scripts/wechat_windows.py history`（基于 PyWxDump 解密微信数据库）

用法：
```bash
# 根据报告类型选择 --limit（日报 1000 / 周报 5000 / 月报 10000）
python scripts/wechat_windows.py history "<群名>" --limit 1000 --asc > /tmp/chat_log_<群名>_<日期>.txt
```

- `--limit`：拉取最近 N 条消息
- `--asc`：时间升序（从早到晚），便于叙事
- 输出直接写入文件，AI 用 Read 工具分块读取

返回的消息行格式：
```
[2026-05-11 09:20] 示例联系人A: 想问一下大家搭建企业的知识库用的什么方案呢？
[2026-05-11 09:21] me: obsidian可以实现
```

`me` 代表当前登录的微信用户（运行 skill 的本机用户）。其他消息按显示名（昵称或备注）展示，不带 wxid。

**注意**：图片消息、引用消息、链接消息会以 `[图片]` `[链接]` `[视频]` 等占位符出现。

**群名模糊匹配**：如果不知道确切群名，可以先查：
```bash
python scripts/wechat_windows.py contacts "<部分群名>"
```

**时间过滤**：`wechat_windows.py history` 不支持 `--since`/`--until` 日期过滤，统一拉取较大的 `--limit` 值后，用 grep 按日期范围过滤：
```bash
# 日报：过滤 2026-05-26
grep "^\[2026-05-26" /tmp/chat_log_full.txt > /tmp/chat_log_target.txt

# 周报：过滤 2026-05-19 ~ 2026-05-25
grep -E "^\[2026-05-(19|20|21|22|23|24|25)" /tmp/chat_log_full.txt > /tmp/chat_log_target.txt

# 月报：过滤 2026-05
grep "^\[2026-05" /tmp/chat_log_full.txt > /tmp/chat_log_target.txt
```

## 二、群成员（MicroMsg.db）

数据库：`%TEMP%/group_daily_decrypted/<wxid>/MicroMsg.db`（PyWxDump 自动解密到临时目录）

Windows 微信的群成员存储：
- `MicroMsg.db` 的 `ChatRoom` 表：`UserNameList` 和 `DisplayNameList` 是 ^G 分隔的并行数组
- 备选：`ChatRoomUser.db`（部分版本有独立表）

封装在 `scripts/wechat_windows.py members` 和 `scripts/lookup_members.py` 里。直接调用即可：

```bash
# 在线查询（推荐）
python scripts/wechat_windows.py members "<群名>" --json > /tmp/members.json

# 离线查询（依赖已解密的 MicroMsg.db）
python scripts/lookup_members.py \
    --group-name "示例社区群" \
    --names "示例联系人A,示例联系人C,示例联系人D" \
    --out /tmp/members.json
```

输出格式：
```json
{"members": [{"username": "wxid_xxx", "nick_name": "张三", "remark": "张总"}]}
```

## 三、群成员头像（HardLinkImage.db + FileStorage）

Windows 微信头像来源（优先级从高到低）：
1. `HardLinkImage.db`（解密后）：`HardLinkImage` 表存 UserName -> FilePath 映射
2. `FileStorage/Image/` 目录：按日期分子文件夹的图片文件
3. `wechat_windows.py avatars` 在线查询（兜底）

封装在 `scripts/extract_avatars.py` 里。输入 `{wxid: 显示名}` 的 JSON，输出 `{显示名: data:image/jpeg;base64,...}` 的 JSON。

```bash
python scripts/extract_avatars.py \
    --names-map /tmp/members.json \
    --out /tmp/avatars.json
```

如果某些群友的头像没找到，`extract_avatars.py` 会在 stderr 列出 `✗ <name> (<wxid>): 头像未找到`。这种情况会自动 fallback 到首字 placeholder，不会让脚本挂掉。

## 四、语音消息

微信语音以 SILK 编码存储在 MediaMSG*.db 的 BLOB 中，聊天记录中只显示 `[语音]` 占位符。要让语音进入日报需要额外处理。

### Windows 语音处理（三步走）

**Step A: 列出语音消息**

```bash
python scripts/wechat_windows.py voice-list "<群名>" --limit 200
```

**Step B: 导出语音为 WAV + 元数据**

```bash
python scripts/wechat_windows.py voice-extract "<群名>" \
    --out-dir /tmp/voices_<群名>_<日期> \
    --limit 200 > /tmp/voice_metadata.json 2>&1
```

> 注意：输出是 stdout（JSON）+ stderr（进度），需要分离。可用 `2>/dev/null` 抑制进度输出只拿 JSON。

**Step C: Whisper 转写**

```bash
python scripts/transcribe_voices.py \
    --wav-dir /tmp/voices_<群名>_<日期> \
    --metadata /tmp/voice_metadata_clean.json \
    --out /tmp/voices_<群名>_<日期>.json \
    --min-duration 3
```

`transcribe_voices.py` 使用 whisper Python API（不依赖 ffmpeg CLI），通过标准库 `wave` 模块直接加载 WAV 文件。缓存写到 `~/.claude/skills/group-daily/voice_cache.json`，按 wav 文件 mtime + size 签名，重复运行零成本。

> **依赖**：`pip install openai-whisper numpy`（numpy 通常在安装 whisper 时自动安装）。

### 输出 JSON 结构

```json
{
  "11239": {
    "time": "2026-05-12 00:27:49",
    "duration_s": 10.0,
    "text": "那还是不一样的\n没置顶的群讨就会永远的忘记..."
  }
}
```

AI 在 Step 3 写故事时，把语音转写当成「这个人的真实发言」用，引用时在 quote 里加 `source: "voice"` 字段（见 story-schema.md）。

### 常见坑

- **繁体输出**：whisper 中文模型默认偏繁体，AI 引用时按上下文转成简体并修订错字。
- **短语音被跳过**：`--min-duration 3` 过滤掉 < 3 秒的，避免「嗯」「啊」浪费 token。
- **模型下载**：首次跑 whisper 会下载约 145MB 的 base 模型权重。
- **群里没语音**：直接跳过整个语音处理流程，不影响主流程。
- **ffmpeg 可选**：transcribe_voices.py 优先用 whisper CLI（需要 ffmpeg），回退到 Python API（不需要 ffmpeg）。Windows 上即使没装 ffmpeg 也能正常转写。

## 五、典型工作流（Windows）

```bash
# 0. 初始化微信数据（提取密钥+解密数据库）
python scripts/wechat_windows.py info

# 1. 拉聊天记录
python scripts/wechat_windows.py history "<群名>" --limit 1000 --asc > /tmp/chat_log_full.txt

# 1.5 按日期过滤
grep "^\[2026-05-26" /tmp/chat_log_full.txt > /tmp/chat_log_target.txt

# 1.6 语音转写（可选，群里有语音时执行）
python scripts/wechat_windows.py voice-list "<群名>" --limit 200
python scripts/wechat_windows.py voice-extract "<群名>" \
    --out-dir /tmp/voices_<群名>_<日期> --limit 200 2>/dev/null > /tmp/voice_metadata.json
python scripts/transcribe_voices.py \
    --wav-dir /tmp/voices_<群名>_<日期> \
    --metadata /tmp/voice_metadata.json \
    --out /tmp/voices_<群名>_<日期>.json

# 2. AI 分析消息，提炼时间线 + 高光人物 + SOP + 金句，输出 story.json

# 3. 查群成员 wxid
python scripts/wechat_windows.py members "<群名>" --json > /tmp/members.json

# 4. 注入 wxid 到 story.json
python scripts/resolve_story_wxids.py --story /tmp/story.json --group "<群名>"

# 5. 事实核查
python scripts/verify_story.py --story /tmp/story.json --chat /tmp/chat_log_target.txt

# 6. 主编排（自动跑头像导出 + HTML + PNG）
python scripts/make_daily.py --story /tmp/story.json --out-dir ~/Desktop
```

## 六、备用路径

如果解密后的数据库路径变了，可以用环境变量指定：

```bash
# 自定义解密输出目录
set GROUP_DAILY_DECRYPT_DIR=C:\path\to\decrypted

# 自定义微信数据目录
set GROUP_DAILY_WX_DIR=C:\path\to\WeChat Files\wxid_xxx
```
