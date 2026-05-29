# group-daily — Windows 适配版

把微信群聊数据变成杂志风"故事型日报"的 Claude Code Skill。产出 HTML + PNG 长图。

> **原版作者**: [Larkin0302/group-daily](https://github.com/Larkin0302/group-daily)（仅支持 macOS）
>
> 本仓库是 Windows 适配版，使用 [PyWxDump](https://github.com/xaoyaoo/PyWxDump) 替代原版的 vchat CLI，实现微信数据读取。

## 效果预览

输入一个群名，AI 自动读聊天记录 → 提炼故事线 → 生成 HTML 杂志页面 + PNG 长图。

## 前置条件

1. **微信 PC 版** 已安装并登录
2. **Python 3.10+**
3. **Chrome 或 Edge** 浏览器（HTML → PNG 截图用）

## 快速安装

```powershell
# 1. 克隆仓库
git clone https://github.com/SevenBaylor/group-daily-windows.git
cd group-daily-windows

# 2. 运行安装脚本
powershell -ExecutionPolicy Bypass -File install.ps1

# 3. 验证环境
python scripts/check_env.py
```

安装脚本会自动：
- 安装 Python 依赖（pywxdump、Pillow）
- 设置环境变量
- 运行环境自检

## 使用方法（在 Claude Code 中）

在 Claude Code 中直接说：**"给 XX 群生成一份日报"**，Skill 会自动执行 8 步工作流。

也可以手动使用命令行工具：

```bash
# 检查微信运行状态
python scripts/wechat_windows.py info

# 搜索群聊
python scripts/wechat_windows.py contacts "关键词"

# 导出群聊天记录
python scripts/wechat_windows.py history "群名" --limit 1000 --asc

# 查看群成员
python scripts/wechat_windows.py members "群名" --json

# 生成 HTML + PNG 日报
python scripts/make_daily.py --story story.json --out-dir ~/Desktop
```

## 工作流（8 步）

| 步骤 | 说明 | 脚本 |
|------|------|------|
| Step 0 | 数据初始化（提取密钥+解密数据库） | `wechat_windows.py info` |
| Step 1 | 拉取聊天记录 | `wechat_windows.py history` |
| Step 1.5 | 语音转写（可选） | `transcribe_voices.py` |
| Step 2 | 基础统计 | AI 计算 |
| Step 2.5 | 加载群风格指纹 | `context_helper.py` |
| Step 3 | AI 阅读并提炼故事 | AI 处理 |
| Step 4 | 查群成员 wxid | `wechat_windows.py members` |
| Step 5 | 注入 wxid 到 story | `resolve_story_wxids.py` |
| Step 6 | 写 story.json + 事实核查 | `verify_story.py` |
| Step 7 | 编辑排版（HTML+PNG） | `make_daily.py` |
| Step 7.5 | 更新群风格指纹 | `context_helper.py` |
| Step 8 | 归档 story.json | 手动/cp |

## 与 macOS 原版的核心差异

| 功能 | macOS 原版 | Windows 适配版 |
|------|-----------|---------------|
| 数据访问 | `vchat` CLI | `wechat_windows.py` (PyWxDump) |
| 解密方式 | vchat 内置 | PyWxDump 内存提取密钥 + AES 解密 |
| 数据路径 | `~/.vchat/data/decrypted/` | `%TEMP%/group_daily_decrypted/<wxid>/` |
| 浏览器 | `/Applications/Chrome.app/...` | `C:/Program Files/Google/Chrome/...` 或 Edge |

## 脚本一览

| 脚本 | 用途 | 平台 |
|------|------|------|
| `wechat_windows.py` | **Windows 微信数据适配器（核心）** | Windows |
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

## 许可

MIT License — 基于 [Larkin0302/group-daily](https://github.com/Larkin0302/group-daily) 改编
