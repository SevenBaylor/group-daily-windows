# group-daily — Windows 适配版

把微信群聊数据变成杂志风"故事型日报"的 Claude Code Skill。产出 HTML + PNG 长图。

**本版本适配 Windows 系统**，使用 PyWxDump 替代 macOS 专属的 vchat CLI。

## 前置条件

1. **微信 PC 版** 已安装并登录
2. **Python 3.10+** + `pip install pywxdump pillow`
3. **Chrome 或 Edge** 浏览器（HTML -> PNG 截图用）
4. 可选：`pip install openai-whisper`（语音转写）

运行环境检查：`python scripts/check_env.py`

---

## 核心架构差异（相比 macOS 原版）

| 功能 | macOS 原版 | Windows 适配版 |
|------|-----------|---------------|
| 数据访问 | `vchat` CLI | `scripts/wechat_windows.py` (PyWxDump) |
| 解密方式 | vchat 打包的 SQLCipher | PyWxDump 内存提取密钥 + AES 解密 |
| 数据路径 | `~/.vchat/data/decrypted/` | `%TEMP%/group_daily_decrypted/<wxid>/` |
| 浏览器 | `/Applications/Chrome.app/...` | `C:/Program Files/Google/Chrome/...` 或 Edge |

---

## 8 步工作流（Windows 版）

### Step 0: 数据初始化（替代原版的 force refresh）

**目标**: 确保微信数据已解密可用。

**执行**: 运行初始化命令，提取密钥 + 解密核心数据库。

```bash
python scripts/wechat_windows.py info
```

这会：
1. 检测运行中的微信进程
2. 提取 SQLCipher 密钥
3. 解密 MicroMsg.db / ChatMsg.db / ChatRoomUser.db / HardLinkImage.db 到临时目录
4. 输出微信用户信息 JSON

**失败处理**:
- "未检测到微信运行" → 启动微信并登录
- "未能提取微信密钥" → 更新 PyWxDump: `pip install --upgrade pywxdump`
- 微信版本太新 → 等待 PyWxDump 更新支持

---

### Step 1: 拉取聊天记录

**目标**: 获取指定群在指定时段的全部聊天消息。

```bash
python scripts/wechat_windows.py history "<群名>" --limit 5000 --asc > /tmp/chat_log_<date>_<group>.txt
```

- `--limit`: 单天 1000 条，多天 5000-10000 条
- `--asc`: 时间升序（从早到晚），便于叙事
- 输出格式与 vchat 兼容: `[YYYY-MM-DD HH:MM] sender: content`
- AI 用 Read 工具分块读取

**群名模糊匹配**: 如果不知道确切群名，可以先查：
```bash
python scripts/wechat_windows.py contacts "<部分群名>"
```

---

### Step 1.5: 语音转写（条件执行）

Windows 上语音消息的处理：
1. PyWxDump 可导出语音文件（SILK 格式）
2. 用 `scripts/transcribe_voices.py` + whisper 转写
3. 转写结果注入聊天记录

```bash
python scripts/transcribe_voices.py \
    --wav-dir <解码wav目录> \
    --filter "<chatroom_username>" \
    --out /tmp/voices_<群名>_<日期>.json
```

> 注意：Windows 上 SILK 解码依赖 `silk-python` 包，可能需要额外配置。如果语音转写太复杂，可暂时跳过此步骤。

---

### Step 2: 基础统计

从聊天记录计算：
- `total_messages` — 总消息数
- `unique_senders` — 发言人数
- `total_chars` — 总字数（排除 `[图片]` `[链接]` 等占位符）
- `new_members` — 新入群人数（通过系统消息判断）

可以写临时 Python 脚本或手动计算。

---

### Step 2.5: 加载群风格指纹

```bash
python scripts/context_helper.py check-style --group-name "<群名>"
```

- `exists: true` → 加载 `styles/<群名>.md`，定位语 / 文化标签 / 黑话 / 禁忌作为硬约束
- `exists: false` → 首次处理此群，跳过（Step 7.5 会生成）

**规则**: 人物气质由 AI 当天读聊天记录临时判断，不在 styles 里固化个人画像。

---

### Step 3: 阅读并提炼故事

AI 阅读 `/tmp/chat_log_<date>_<group>.txt`，参照以下引用文件：
- `references/writing-style.md` — 写作风格指南
- `references/design-principles.md` — 设计原则

**输出 story.json 结构**（详见 `references/story-schema.md`）：
- `opening` — 100-200 字开场钩子 + 摘要
- `lead_title` — 多行主标题，捕捉当日核心
- `timeline` — 6-8 个故事节点，每个含 time / badge / cast / theme / story / quotes / output
- `highlights` — 6-8 张人物卡片（不是按发言量，而是"没有他故事就缺一块"的人）
- `sops` — 群聊中可提取的工作流
- `qas` — 有价值的问答对
- `footer_quote` — 当日最共鸣的一句话

**原则**: 宁可少不要多。

---

### Step 4: 查群成员 wxid

```bash
# 推荐方式：直接从 Windows 适配器获取群成员
python scripts/wechat_windows.py members "<群名>" --json > /tmp/members.json
```

输出格式：
```json
{"members": [{"username": "wxid_xxx", "nick_name": "张三", "remark": "张总"}]}
```

备选方式（离线）:
```bash
python scripts/lookup_members.py --group-name "<群名>" --names "张三,李四,..." --out /tmp/members.json
```

---

### Step 5: 注入 wxid 到 story.json

```bash
python scripts/resolve_story_wxids.py --story /tmp/story_<date>_<group>.json --group "<群名>"
```

三步匹配：完全相等 → 备注相等 → 子串包含。找不到的自动全库搜索。

---

### Step 6: 写 story.json + 事实核查

**6a — 写文件**: 用 Write 工具保存 `/tmp/story_<date>_<group>.json`

**6b — 事实核查**:
```bash
python scripts/verify_story.py --story /tmp/story_<date>_<group>.json --chat /tmp/chat_log_<date>_<group>.txt
```

检查四个维度：
- A. 引用文字是否在原文存在（12 字子串匹配）
- B. sender 是否匹配
- C. 时间是否在 timeline 节点范围内
- D. cast 里的人是否在该时段有真实发言

退出码: 0=全通过, 1=跨节警告, 2=错位。非零必须修。

---

### Step 7: 编辑排版（make_daily）

```bash
python scripts/make_daily.py --story /tmp/story_<date>_<group>.json --out-dir ~/Desktop
```

自动完成：
1. 从 story 收集 wxid → 导出头像（base64 data URI）
2. 渲染 HTML（杂志风排版）
3. 截图 PNG 长图（Chrome headless + 自适应裁底）
4. 自动打开生成的文件

输出：
- `~/Desktop/群日报_<群名>_<日期>.html`
- `~/Desktop/群日报_<群名>_<日期>.png`

---

### Step 7.5: 更新群风格指纹

```bash
python scripts/context_helper.py path --style "<群名>"
```

- 已有文件 → 增量更新 `last_updated` / `sample_dates` / `sample_count`，追加新黑话/禁忌
- 新文件 → 按 `references/group-style.md` 模板写入 v1

**约束**: 不要在 styles 里点名固化"X 是 Y 角色"。

---

### Step 8: 归档

```bash
cp /tmp/story_<date>_<group>.json "$GROUP_DAILY_VAULT/<date>_<group>.json"
```

- `GROUP_DAILY_VAULT` 默认: `~/Documents/GroupDaily`
- story.json 是最珍贵的资产（比 HTML/PNG 重要）

---

## 可用脚本一览

| 脚本 | 用途 | 平台 |
|------|------|------|
| `wechat_windows.py` | **Windows 微信数据适配器**（核心新增） | Windows |
| `check_env.py` | 环境自检 | Windows |
| `make_daily.py` | 主编排（头像→HTML→PNG） | 跨平台 |
| `render_html.py` | HTML 渲染（Jinja2 模板） | 跨平台 |
| `html_to_png.py` | HTML→PNG（Chrome headless） | 跨平台 |
| `resolve_story_wxids.py` | 批量解析 wxid | Windows |
| `lookup_members.py` | 群成员查询 | Windows |
| `extract_avatars.py` | 头像导出 | Windows |
| `verify_story.py` | 事实核查 | 跨平台 |
| `transcribe_voices.py` | 语音转写 | 跨平台 |
| `context_helper.py` | 群风格指纹管理 | 跨平台 |

## 与原版的不兼容差异

1. **无 vchat CLI 依赖** → 改为 `wechat_windows.py` + PyWxDump
2. **无 `wxrefresh` sudo 刷新** → 每次运行时自动解密（有缓存机制）
3. **头像来源不同** → macOS 用 `head_image.db`，Windows 用 `HardLinkImage.db` + `FileStorage`
4. **语音转写路径不同** → Windows 上的 SILK 解码可能需要额外配置
5. **Chrome 路径不同** → 自动检测 Program Files 下的 Chrome/Edge

## 参考

- PyWxDump: https://github.com/xaoyaoo/PyWxDump
- 原版 group-daily: https://github.com/Larkin0302/group-daily
