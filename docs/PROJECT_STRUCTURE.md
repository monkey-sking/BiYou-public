# BiYou Project Structure

这个项目现在的有效入口和目录职责可以按下面理解。先看这份，再看根目录，会清晰很多。

## 1. 运行入口

- `main.py`
  主工作流入口。负责把下载、翻译、封面、投稿、评论等步骤串起来。

- `monitor.py`
  独立监控入口，偏定时/轮询用途，不是主上传流程入口。

## 2. 核心代码目录

- `core/modules/`
  面向业务步骤的模块层。
  这里放下载、抓取、翻译、上传、评论、合集整理等流程拼装代码。
  如果你要查“某一步具体怎么跑”，通常先看这里。

- `core/services/`
  面向外部能力和具体服务的适配层。
  这里放 Gemini、B 站 API、图片翻译、水印去除等实现。
  如果你要查“某个能力本身怎么实现”，通常先看这里。

- `core/utils/`
  通用基础设施。
  这里放配置读取、cookie、浏览器连接、日志、i18n、URL/时间/文本工具等。
  浏览器 profile、debug port、Gemini 浏览器连接逻辑都在这里。

- `core/workflows/`
  较聚焦的工作流片段。
  当前体量不大，更多是把某一段流程独立封装出来。

## 3. 配置与脚本

- `config/`
  配置目录。
  `config/config.yaml` 是当前实际运行配置。
  `config/config.example.yaml` 是样例配置。
  `config/prompts.yaml`、`config/category_map.yaml` 这类是静态配置资源。

- `scripts/`
  运维、调试、探测、数据分析脚本。
  这些不是主流程依赖的入口文件，而是辅助工具集合。

- `tools/`
  零散的小工具脚本。
  目前只有少量内容，可以继续往这里放一次性辅助工具。

## 4. 测试与文档

- `tests/`
  单元测试和回归测试。

- `README.md` / `README_zh.md`
  面向使用者的说明。

- `.agent/`
  代理工作流和内部说明文件，属于仓库文档的一部分，不是运行时垃圾。

## 5. 运行时数据

下面这些更偏运行结果或本地状态，不是核心源码：

- `history/`
  已处理项目的历史记录。

- `logs/`
  运行日志。

- `output/`
  浏览器自动化或中间输出目录。

- `tmp/`
  临时文件目录。

- `.playwright-cli/`
  Playwright 调试日志、页面快照、网络抓包。

- `one-api.db`
  本地数据库文件。

## 6. 浏览器配置现状

当前实际运行读的是 `config/config.yaml`，关键配置是：

- `browser: chrome`
- `browser_debug_port: 39222`
- `browser_profile: ~/.biyou-workflow-browser-data`
- `current_account_index: 0`

当前代码里的解析规则在 `core/utils/browser_config.py`：

1. 优先使用 `config/config.yaml` 里的 `browser_profile`
2. 如果这个 profile 本身有完整状态，就直接用它
3. 运行时只使用配置指定的 workflow profile，不再自动回退旧 profile

按当前机器上的实际状态，生效的是：

- configured profile: `${HOME}/.biyou-workflow-browser-data`
- effective profile: `${HOME}/.biyou-workflow-browser-data`
- fallback: `false`
- debug port: `39222`

也就是说，现在只认你家目录下的 `~/.biyou-workflow-browser-data`，不会再自动复用旧的 `~/.biyou-browser-data` 或项目里的 `.gemini-browser-data`。

## 7. 哪些流程会用浏览器

按当前 `config/config.yaml`：

- `services.cover.use_browser: false`
  封面生成默认走 Web API（`gemini-webapi>=2.0.0`），失败时才会退到浏览器 Gemini。

- `services.translation.use_browser: false`
  文本翻译默认先走 Web API，失败时才会退到浏览器文本生成。

## 8. 后续整理建议

如果你后面还想继续“瘦身”根目录，建议按这个顺序做：

1. 保持根目录只留入口、说明、安装脚本和少量运维脚本。
2. 新的一次性排查脚本优先放进 `scripts/` 或 `tools/`，不要再直接丢根目录。
3. 所有浏览器抓包、截图、数据库、临时图片都继续留在忽略列表里，不进版本库。
4. 如果要继续大整理，优先把 `monitor.py`、`setup_browser.py` 这类入口脚本归档到 `scripts/`，但这一步需要同步更新 README 和调用方式，属于下一轮结构调整。
