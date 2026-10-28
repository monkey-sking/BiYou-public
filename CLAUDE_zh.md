# BiYou 项目记忆 (AI 上下文)

## ⚡️ 快速命令

- **同步视频**: `python3 main.py "URL"`
- **模拟运行**: `python3 main.py "URL" --dry-run`
- **安装依赖**: `pip install -r requirements.txt`
- **环境检查**: `./check_env.sh`

## 🛠 技术栈

- **语言**: Python 3.10+
- **核心库**: `yt-dlp` (下载), `ffmpeg` (处理), `biliup` (上传), `google-generativeai` (Gemini API)
- **配置文件**: `config/config.yaml` (参考 `config/config.example.yaml`)

## 📂 项目结构

- `core/modules/`: 核心模块 (下载、上传、翻译等)
- `core/services/`: 外部服务层 (LLM, 字幕处理)
- `main.py`: 同步流程主入口
- `videos/`: 视频下载与原始元数据目录
- `history/`: 已处理视频的元数据存档目录
- `.agent/workflows/`: 标准工作流文档

## 📝 编码规范

- **国际化 (I18n)**: 外部显示的字符串必须通过 `core/utils/i18n.py` 使用，以支持中英双语。
- **LLM 调用**: 所有 AI 调用必须通过 `LLMFactory`，确保模型回退和配置一致性。
- **错误处理**: 在模块层捕获异常，并使用本地化的 `print_t` 或 `log` 记录。
- **Cookie**: 浏览器 Cookie 自动提取。确保用户已在 Chrome/Edge/Firefox 登录。

## ⚠️ 重要规则

- **禁止提交** `config/config.yaml` 或任何包含凭据的 JSON/TXT 文件。
- 添加新 import 时，务必检查 `requirements.txt` 是否需要更新。
- 处理过的视频元数据存放在 `history/` 中，请勿随意删除，以免产生重复上传。
