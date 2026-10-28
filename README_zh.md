# BiYou: Bilibili <-> YouTube 双向同步工具

[English](README.md) | **中文文档**

**BiYou** 致力于构建**跨平台视频同步与本地化解决方案**。不仅支持 YouTube 到 Bilibili 的自动同步，未来还将涵盖**双向同步**、**AI 字幕/配音**及**本地视频增强**等全链路功能。

---

## 📁 项目结构

项目目录与运行产物说明见 [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)。

## ✨ 功能特性

- 🤖 **AI 驱动**：使用 Gemini API (Web/Official) 智能翻译标题、生成 Catchy 描述及精准分区/标签。
- 🖼️ **全自动封面**：根据视频内容自动设计并生成 16:9 和 4:3 比例的封面图，解决 B 站封面裁切痛点。
- 🚀 **统一日志与弹性重试**：内置基于 `loguru` 的统一日志管理与指数退避重试机制，确保 API 调用稳定可靠。
- 📦 **开箱即用**：支持从主流浏览器 (Chrome/Edge/Safari) 自动提取 Cookie，无需手动配置登录信息。
  - **智能分区与标签**：根据视频内容自动推荐最匹配的 Bilibili 分区和热门标签。
- 🌍 **国际化 (I18n)**: 支持 **中文**（默认）和 **英文** 界面切换。
- 🎬 **AI 字幕**: 自动提取音频、生成字幕并完成翻译，支持生成双语 ASS 字幕（内置烧录功能）。
- 📝 **自动评论**: 为健身类视频自动发表置顶评论，支持自定义回复模板和重试机制。
- 🧹 **智能清理**:
  - 自动清理下载失败产生的临时文件 (`.part`)。
  - 支持上传成功后自动删除源视频 (`--delete-video`)。
  - 自动归档已处理视频的元数据到 `history/` 文件夹。
- **📂 有序存储**: 生成的封面图将直接保存在视频同目录下，使用后缀命名 (`_cover_16x9.jpg`)，便于管理。
- **🔗 深度 B 站集成**:
  - 通过浏览器 Cookie (Chrome/Edge/Firefox) 自动登录。
  - 支持定时发布。
  - 支持自动发布置顶评论。
  - 支持 **B站合集** 管理 (自动/手动将视频加入合集)。
- 🛡️ **安全与稳定**: 内置 FFmpeg 完整性校验，自动检测视频损坏；支持 **自动修复 (Auto-Repair)** 与断点续传，告别大文件重新下载。

## Multi-Platform Publishing

- Stable adapters: `Bilibili`, `Douyin`, `Kuaishou`
- Beta adapters: `WeChat Channels`, `Xiaohongshu`
- Unified fields: title, description, cover, tags, schedule intent
- Beta adapters use browser automation and may require manual re-login
- 配置入口:
  - `config/config.yaml -> publishing`
  - `config/publishing_automation.yaml` for `Douyin` / `Kuaishou` / `WeChat Channels` / `Xiaohongshu` locators
- 鉴权方式:
  - `Bilibili`: browser cookies
  - `Douyin`: 已登录浏览器 profile 的 `automation`，或官方 `api` + `access_token`
  - `Kuaishou`: 已登录浏览器 profile 的 `automation`，或官方 `api` + `access_token` + `app_id`
  - `WeChat Channels` / `Xiaohongshu`: logged-in browser profile

### 平台字段矩阵

| 平台 | 当前已打通的发布字段 | 已可透传的平台专属字段 |
|------|----------------------|------------------------|
| `Bilibili` | `title`、`description`、`cover`、`tags`、定时 | `category/tid`、`copyright`、`mission_id`、`topic_id`、`activity_id`、`season`、`collection`、`suggested_collection` |
| `Douyin` | `title`、`description`、`cover`、`video_path`、定时意图 | `visibility`、`allow_comment`，以及其他会保留到 `platform_options.douyin.*` 的扩展字段 |
| `Kuaishou` | `title`、`caption`、`cover`、`video_path`、定时意图 | `visibility`、`allow_comment`，以及其他会保留到 `platform_options.kuaishou.*` 的扩展字段 |
| `WeChat Channels` | 短 `title`、`description`、`cover`、`video_path`、定时意图 | `declare_original`、`allow_comment`、`collection_name`，以及其他会保留到 `platform_options.wechat_channels.*` 的扩展字段 |
| `Xiaohongshu` | `title`、`body`、`cover`、`video_path`、定时意图 | `visibility`、`allow_comment`、`poi_name`，以及其他会保留到 `platform_options.xiaohongshu.*` 的扩展字段 |

### Multi-Platform Quick Setup

1. 复制配置文件:

   ```bash
   cp config/config.example.yaml config/config.yaml
   cp config/publishing_automation.example.yaml config/publishing_automation.yaml
   ```

2. 在 `config/config.yaml` 打开目标平台:

   ```yaml
   publishing:
     enabled: true
     default_targets:
       - bilibili
       - douyin
       - kuaishou
       - wechat_channels
       - xiaohongshu
     automation_config_file: "config/publishing_automation.yaml"
     adapters:
       bilibili:
         enabled: true
         mode: api
       douyin:
         enabled: true
         mode: automation
         automation: {}
         # 如果你有官方能力，也可以改成 api:
         # mode: api
         # access_token: "YOUR_DOUYIN_ACCESS_TOKEN"
       kuaishou:
         enabled: true
         mode: automation
         automation: {}
         # 如果你有官方能力，也可以改成 api:
         # mode: api
         # access_token: "YOUR_KUAISHOU_ACCESS_TOKEN"
         # app_id: "YOUR_KUAISHOU_APP_ID"
       wechat_channels:
         enabled: true
         mode: automation
       xiaohongshu:
         enabled: true
         mode: automation
   ```

3. 抖音 / 快手 / 视频号 / 小红书浏览器自动化:
   - `browser_profile` 尽量单独用一个工作流 profile
   - `browser_debug_port` 固定下来，自动化期间保持这个浏览器会话存活
   - 先用 `browser_profile` 指向的浏览器 profile 登录创作者后台
   - 再按实际页面结构修改 `config/publishing_automation.yaml` 里的 `publish_url`、`locators` 和可选的 `option_controls`
   - `option_controls` 现已支持 `fill / click / choice / boolean` 四种动作，可用于把 `visibility / allow_comment / declare_original / collection_name / poi_name` 这类平台专属字段真正落到发布页控件

4. 如需给单条视频加平台专属配置，在对应 `history/*.json` 或 `video_metadata.json` 中写 `platform_options`:

   ```json
   {
     "platform_options": {
       "bilibili": {
         "copyright": 2,
         "mission_id": 4058195,
         "suggested_collection": "居家暴汗健身跟练"
       },
       "douyin": {
         "visibility": "friends",
         "allow_comment": false
       },
       "wechat_channels": {
         "declare_original": true
       }
     }
   }
   ```

   当前版本会正式保留并透传这些平台专属字段；其中 `B站` 还会自动继承顶层 metadata 里的 `category / mission_id / copyright / collection` 等常用字段。对于自动化平台，只要 `config/publishing_automation.yaml` 里为该字段补好了 `option_controls` selector，这些字段会继续被实际点进后台表单，而不是只停留在 payload 里。

5. 安全验证多平台装配:

   ```bash
   python3 main.py "https://www.youtube.com/watch?v=VIDEO_ID" --dry-run -y
   ```

   这会跑完整个前处理流程，并在上传阶段校验多平台配置装配；`--dry-run` 不会真实发片。

## 🔮 路线图 (Roadmap)

我们正在积极开发以下功能，致力于打造最强的视频本地化工具：

- [x] **AI 字幕**: 自动提取音频、生成字幕并翻译（支持双语字幕）。
- [ ] **AI 配音**: 消除原声、生成目标语言的 AI 配音（音频本地化）。
- [ ] **双向同步**: 支持 **Bilibili -> YouTube** 的反向同步与出海。
- [ ] **本地工作流**:
  - 支持对**本地视频文件**进行标题优化、简介生成和封面处理。
  - 独立运行的图片处理工具（智能裁剪、文字清理）。
- [ ] **更多平台**: 拓展支持 TikTok, Xiaohongshu 等短视频平台。

## 🛠 环境要求

- **Python 3.10+**
- **ffmpeg** (必须添加到系统环境变量 PATH 中)
- **Deno 2.0+** (yt-dlp 处理 YouTube JS challenge 必需)
- **浏览器**: Chrome, Edge 或 Firefox (需已登录 **YouTube**, **Bilibili** 和 **Gemini**)。

## 🚀 安装指南

1. **克隆项目**:

   ```bash
   git clone <repository_url>
   cd biyou
   ```

2. **安装依赖**:

   ```bash
   pip install -r requirements.txt
   ```

   这里会安装已验证可用的 `yt-dlp[default]==2026.03.17`，并带上 `yt-dlp-ejs`。

   如果 Homebrew Python 拦截了 `pip install`，用下面这条：

   ```bash
   python3 -m pip install --break-system-packages --user -r requirements.txt
   ```

3. **配置文件**:
   复制示例配置并进行修改:

   ```bash
   cp config/config.example.yaml config/config.yaml
   ```

## ⚙️ 配置说明

编辑 `config/config.yaml`:

```yaml
# 界面语言: 'zh' (中文) 或 'en' (英文)
language: zh

# 获取 Cookie 的默认浏览器 (chrome, edge, firefox)
browser: chrome

# BiYou 专用浏览器 Profile
browser_profile: ~/.biyou-workflow-browser-data

# 浏览器自动化使用的远程调试端口
browser_debug_port: 39222

# 自动模式: 跳过关键环节的手动确认 (y/n)
auto_confirm: false

# 自动评论关键词: 仅在匹配到这些分类时才发表自动评论
auto_comment_keywords: ["健身", "运动", "训练", "Fitness", "Workout"]

# 服务配置 (精细化控制)
services:
  translation:
    provider: gemini_official # 文本翻译任务 (标题, 简介)
    model: gemini-2.5-pro
  cover:
    provider: gemini_web      # 封面生成任务 (推荐 gemini_web 以获得最强图片编辑能力)
    model: Thinking           # gemini_web 使用 Thinking / Pro / Flash 别名
  analysis:
    provider: gemini_official # 内容分析任务 (标签, 分区)
    model: gemini-2.5-flash

# 提供商详情
providers:
  gemini_official:
    api_key: "YOUR_API_KEY" # 官方 API Key (https://aistudio.google.com/)
  gemini_web:
    browser: chrome # 使用浏览器 Cookie

# 路径配置
video_source_dir: "../videos"  # 视频下载目录

```

## 🖥 使用方法

### 基础用法

#### 设置命令行快捷访问 (推荐)

为了在终端任何地方都能直接运行 `biyou` 命令，你可以运行项目根目录下的设置脚本：

```bash
zsh setup_cli.sh
source ~/.zshrc
```

设置完成后，你就可以在任何目录下直接使用：

```bash
biyou "https://www.youtube.com/watch?v=VIDEO_ID"
```

#### 手动运行

如果你不想设置别名，也可以通过以下方式执行全流程同步（含自动下载、AI 本地化处理、B站上传）:

> [!TIP]
> **重要**: URL 建议使用引号包裹，防止 `&` 等特殊字符被 Shell 截断。

```bash
python3 main.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

### 命令行参数

| 参数 | 说明 |
| :--- | :--- |
| `url` | YouTube 视频链接 (可选，不填则处理目录下最新视频) |
| `-n`, `--dry-run` | **模拟运行模式**：仅测试流程，不实际上传或发帖 |
| `-y`, `--yes` | **静默模式**：自动确认所有交互式 Prompt (y) |
| `-s`, `--schedule` | **定时发布**：指定发布时间 (如 `+4h` 表示4小时后，或 `2024-01-01 12:00`) |
| `--delete-video` | **上传后删除**：上传成功后自动删除源视频文件以释放空间 |
| `-t`, `--sync-subtitles` | **同步字幕**：单独上传/同步缺失的字幕文件 (支持断点续传检测) |
| `--season "名称"` | **加入合集**：指定上传后加入的 B 站合集名称或 ID |
| `--auto-season` | **智能合集**：让 AI 根据视频内容推荐并自动加入合集 |
| `-kill`/`-k` | **强制停止**：一键终止所有 BiYou 相关进程 |

### 常见使用示例

**1. 测试 AI 翻译和封面生成效果 (不上传):**

```bash
python3 main.py "URL" --dry-run
```

### 让外部代理自动接手失败

如果你希望“龙虾”之类的外部代理在流程失败时自动接手，可以启用 `workflow_signals`：

```yaml
workflow_signals:
  enabled: true
  state_dir: ./logs/workflow_status
  events_file: ./logs/workflow_events.jsonl
  agent_hook: "/path/to/lobster-hook.sh"
  notify_hook: "/path/to/notify.sh"
```

运行时会写出结构化状态文件和事件流：

- `logs/workflow_status/latest.json`: 最近一次运行的聚合状态
- `logs/workflow_status/<run_id>.json`: 单次运行状态
- `logs/workflow_events.jsonl`: 追加式事件流

失败事件会带这些关键字段：

- `stage`: 出错阶段，例如 `metadata`、`cover`、`upload`
- `error_code`: 稳定错误码，例如 `COVER_PROCESS_FAILED`
- `recoverable`: 是否建议自动重试
- `suggested_action`: 建议动作，例如 `retry_cover`

`agent_hook` 会在 `run_failed` 时触发，适合让龙虾读取状态文件后自动重试或修复。
`notify_hook` 只会在 `recoverable=false` 的失败上触发，适合发消息给你。

**2. 同步并自动删除原文件 (节省空间):**

```bash
python3 main.py "URL" --delete-video
```

**3. 设定在 4 小时后发布:**

```bash
python3 main.py "URL" -s "+4h"
```

**4. 手动检测视频完整性:**

```bash
check-video /path/to/video.mp4
```

**5. 补充上传字幕 (视频已发布但缺字幕):**

```bash
biyou -t
```

**6. 上传并由 AI 自动管理合集:**

```bash
by "URL" --auto-season
```

**7. 预拉取频道库存并每天定时发一条:**

先在 `config.yaml` 配置：

```yaml
channel_daily_publish:
  channel_url: "https://www.youtube.com/@RosarioCooking/videos"
  publish_time: "19:05"
  timezone: "Asia/Shanghai"
  sync_window: 20
  state_file: "history/channel_daily_publish.json"
```

同步频道到本地待发库存：

```bash
python3 scripts/channel_daily_publish.py sync
```

为下一个可用的 `19:05` 排一条待发：

```bash
python3 scripts/channel_daily_publish.py publish
```

一次执行“同步 + 排期”：

```bash
python3 scripts/channel_daily_publish.py run
```

**8. 指定上传到 "游戏教程" 合集:**

```bash
by "URL" --season "游戏教程"
```

## 📂 文件结构

```text
BiYou/
├── config/              # 配置文件目录
├── core/                # 核心功能逻辑
│   ├── modules/         # 功能模块 (下载、上传、翻译等)
│   ├── services/        # 原子服务 (LLM, 字幕转换等)
│   ├── utils/           # 通用工具 (i18n, logger, config 等)
│   └── workflows/       # 任务工作流
├── videos/              # 视频及其原始元数据下载目录
├── history/             # 已处理视频的关键元数据存档 (Git 忽略)
├── main.py              # 程序主入口
├── requirements.txt     # Python 依赖列表
└── .gitignore           # 忽略文件配置```
```


## 📝 免责声明 (Disclaimer)

- **内容合规**: 本工具仅供 UP 主**同步管理及二次加工自己的原创内容**（包括 AI 封面生成、标题优化等）。请确保你拥有视频的版权或已获得授权。
- **平台规则**: 用户需自行承担因违反 Bilibili 社区规则而导致的账号风险。作者不对任何违规搬运/滥用造成的后果负责。

## ☕ 求打赏 (Support)

[![](https://img.shields.io/badge/Afdian-@runking-purple.svg)](https://afdian.com/a/runking)

如果这个工具帮你节省了时间，欢迎在 **[爱发电](https://afdian.com/a/runking)** 或 **[GitHub Sponsors](https://github.com/sponsors/monkey-sking)** 上支持作者！

## ⚠️ 注意事项

- **Cookie 有效性**: BiYou 现在只使用流程专用浏览器 Profile (`~/.biyou-workflow-browser-data`)；运行前请确保你已在这个 Profile 中登录 **YouTube**, **Bilibili** 和 **Gemini**。
- **API 配额**: Gemini API 有免费额度限制；如果封面生成失败，请检查配额或网络连接。
- **安全检查**: 建议首次使用时加上 `--dry-run` 参数，确认生成的封面和翻译内容无误。
