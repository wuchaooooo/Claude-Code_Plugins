---
name: wechat-chatlog-reader
description: 通过 WeFlow 本地 HTTP API 读取微信私聊好友的聊天记录，增量追加写入 Obsidian LLM-CRM vault 的 raw/chatlog/ 目录。当用户需要同步微信聊天记录、拉取微信消息、更新聊天日志时使用。触发词包括：拉取微信消息、同步聊天记录、更新微信聊天、读取微信、聊天记录。
weflow_token: 815bf63d6cbb3bec0338e47214d4b68a
asr_api_key: ark-cf151ed8-7b7f-45e4-82a5-19bbbbde0b7a-ea2bb
default_asr_backend: local
whisper_url: http://127.0.0.1:8080
whisper_model: /opt/homebrew/share/whisper-cpp/models/ggml-large-v3-turbo.bin
whisper_host: 0.0.0.0
whisper_port: 8080
whisper_language: auto
blacklist_file: references/blacklist.md
---

# 微信聊天记录读取

通过 WeFlow 本地 HTTP API（`127.0.0.1:5031`）读取微信私聊好友的聊天记录，增量追加写入 Obsidian LLM-CRM vault 的 `raw/chatlog/` 目录。

纯数据搬运，不做任何分析。

## 技能资源

- `references/weflow-api.md` — WeFlow HTTP API 完整参考（端点、参数、响应格式、鉴权）
- `references/blacklist.md` — 用户维护的黑名单 wxid 列表，命中即跳过
- `scripts/sync_chatlog.py` — ⭐ 批处理同步脚本，整合联系人筛选、增量拉取、语音转写、格式化写入全流程
- `scripts/format_messages.py` — 将 WeFlow API JSON 响应格式化为 chatlog 文本行
- `scripts/transcribe_voice.py` — 调用 ASR 后端将语音消息转为文字，支持 `remote`（火山 doubao）和 `local`（whisper-cpp）两种后端
- `scripts/start_whisper_server.sh` — 启动并守护本地 whisper-cpp 服务（仅 `local` 后端需要）

## 前置条件

开始前确认：
- WeFlow 已启动且 API 服务已开启
- WeFlow API Token：已存储在 SKILL.md frontmatter 的 `weflow_token` 字段
- ASR 后端：见下方「ASR 后端选择」一节
  - **remote（默认）**：需 `asr_api_key` 字段（已存于 frontmatter）
  - **local**：需本地 `whisper-server`（`scripts/start_whisper_server.sh` 可一键启动）
- Obsidian vault 路径：`/Users/Charles/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM-CRM`

## 执行流程

### ASR 后端选择

`transcribe_voice.py` 和 `sync_chatlog.py` 通过 `--asr-backend`（默认 `local`，见 frontmatter `default_asr_backend`）切换语音识别后端：

| 后端 | 参数值 | 适用场景 | 所需凭证/服务 |
|------|--------|---------|---------------|
| **本地 whisper-cpp**（默认） | `local` | 离线/隐私场景、批量转写、避免 API 费用 | 本地 `whisper-server`（端口 8080） |
| **远程 doubao** | `remote` | 网络通畅、追求识别质量、不介意付费/限流 | 火山引擎 API Key（`asr_api_key`） |

#### 配置（已存于 SKILL.md frontmatter）

| 字段 | 默认值 | 用途 |
|------|--------|------|
| `whisper_url` | `http://127.0.0.1:8080` | whisper-cpp 服务地址 |
| `whisper_model` | `/opt/homebrew/share/whisper-cpp/models/ggml-large-v3-turbo.bin` | 模型文件路径（启动时校验） |
| `whisper_host` / `whisper_port` | `0.0.0.0` / `8080` | 启动参数 |
| `whisper_language` | `auto` | 语言提示（`zh`/`en`/`auto`） |

启动参数与默认值可通过 `start_whisper_server.sh` 接受的同名环境变量覆盖，例如 `WHISPER_PORT=9000 ./start_whisper_server.sh`。

#### local 后端：自动启动行为

`sync_chatlog.py --asr-backend local` 在拉取前会**自动调用** `scripts/start_whisper_server.sh`：
- 服务已在跑 → 直接复用（通过 `GET /health` 校验）
- 服务未跑 → 用 frontmatter 的参数后台启动 `nohup whisper-server ...`，写入 `/tmp/whisper-server.pid` + `/tmp/whisper-server.log`，轮询 `/health` 直到就绪（默认 30s 超时）
- 启动失败 → 脚本退出并打印日志尾部，提示可手动启动后用 `--no-auto-start-whisper` 重跑

#### 手动启动（可选）

```bash
# 用 frontmatter 默认值启动
./scripts/start_whisper_server.sh

# 自定义端口
WHISPER_PORT=9000 ./scripts/start_whisper_server.sh

# 启动后用 --no-auto-start-whisper 跳过自动检测
python scripts/sync_chatlog.py --asr-backend local --no-auto-start-whisper ...
```

### 模式选择

| 模式 | 参数 | 行为 |
|------|------|------|
| **增量**（默认） | 无 | sessions 预筛选 → 只拉有新消息的好友 |
| **全量** | `--full` | 删除 `raw/chatlog/` 下所有文件 → 全量重拉全部好友 |

### 黑名单

`references/blacklist.md` 是一个用户维护的 wxid 列表，命中即跳过（不创建文件、不调用 messages API、不参与 sessions 预筛选）。文件格式：

- 每行一个 wxid
- `#` 开头为注释（行内 `<wxid>  # 原因` 也支持）
- 空行忽略
- 必须符合 wxid 模式（ASCII 字母开头、字母/数字/下划线/连字符、≥ 4 字符）—— 这样文件可附带中文说明而不会污染列表
- 默认路径可通过 `--blacklist-file` 覆盖

维护方式：直接编辑该文件，下一次 `sync_chatlog.py` 启动时会即时读取。

### 1. 配置确认

确认 WeFlow API 连接信息：
- 基础地址：`http://127.0.0.1:5031`（默认）
- API Token：从 SKILL.md frontmatter 的 `weflow_token` 读取
- 默认 ASR 后端：从 frontmatter 的 `default_asr_backend` 读取（当前为 `local`）。命令行 `--asr-backend` 可覆盖
- 黑名单文件：从 frontmatter 的 `blacklist_file` 读取（默认 `references/blacklist.md`）。命令行 `--blacklist-file` 可覆盖

### 2. 获取联系人列表

调用 WeFlow API 获取全部联系人，筛选私聊好友（API 细节见 `references/weflow-api.md` 第 4 节）：

```bash
curl -s -H "Authorization: Bearer <weflow_token>" \
  "http://127.0.0.1:5031/api/v1/contacts"
```

从响应中筛选联系人，必须同时满足：

1. `type == "friend"`（排除群聊、公众号、服务号）
2. `username` 不在系统账号黑名单中
3. `username` 不在用户维护的 `references/blacklist.md` 黑名单中（见下方「黑名单」一节）

系统账号黑名单（这些会以 `friend` 类型出现，但不是私人好友）：
- `filehelper` — 文件传输助手
- `weixin` — 微信团队
- `mphelper` — 公众平台安全助手

提取以下字段：
- `username`（wxid）
- `displayName`
- `detailDescription`（微信备注）
- `nickname`
- `alias`
- `labels`（微信标签数组 → 写入 tags，每个加 `wechat-` 前缀）

将完整响应缓存到 `/tmp/weflow_contacts.json`（供 `sync_chatlog.py` 使用）：

```bash
curl -s -H "Authorization: Bearer <weflow_token>" \
  "http://127.0.0.1:5031/api/v1/contacts?limit=10000" \
  -o /tmp/weflow_contacts.json
```

### 2.5 会话预筛选（增量拉取核心）

调用 sessions API 获取每个私聊会话的最后消息时间，与本地文件 `最后拉取时间` 对比，**只对有新消息的好友调用 messages API**，避免无效请求：

```bash
curl -s -H "Authorization: Bearer <weflow_token>" \
  "http://127.0.0.1:5031/api/v1/sessions?limit=10000"
```

筛选规则：
- 排除群聊（username 含 `@chatroom`）和系统账号
- 提取每个会话的 `lastTimestamp`
- 对比本地文件 `最后拉取时间`：`session.lastTimestamp > 本地时间` → 有新消息，需要拉取
- 无 session 记录的好友跳过
- 新好友（无本地文件）始终拉取

`sync_chatlog.py` 已内置此逻辑，无需手动执行。

### 3. 批量拉取消息（推荐）

使用 `scripts/sync_chatlog.py`：

```bash
# 增量模式（默认）—— sessions 预筛选，只拉有新消息的好友
# 本地 whisper-cpp 后端（默认）—— 自动启动 whisper-server
python scripts/sync_chatlog.py \
  --weflow-token <weflow_token> \
  --vault-chatlog "<vault_path>/raw/chatlog"

# 远程 doubao 后端 —— 显式指定
python scripts/sync_chatlog.py \
  --weflow-token <weflow_token> \
  --asr-backend remote \
  --asr-key <asr_api_key> \
  --vault-chatlog "<vault_path>/raw/chatlog"

# 全量模式 —— 删除旧文件，全量重拉全部好友
python scripts/sync_chatlog.py \
  --weflow-token <weflow_token> \
  --vault-chatlog "<vault_path>/raw/chatlog" \
  --full

# 预览模式（不实际拉取，两种模式均支持）
python scripts/sync_chatlog.py ... --dry-run

# 自定义黑名单文件路径
python scripts/sync_chatlog.py ... --blacklist-file /path/to/other.md
```

脚本自动完成以下步骤：
- 从 `/tmp/weflow_contacts.json` 加载已缓存联系人（需先执行第 2 步缓存）
- 增量模式：调用 sessions API 预筛选，只保留有新消息的好友（见 2.5 节）
- 全量模式：清空 `raw/chatlog/` 目录，全量拉取全部好友
- 比对 frontmatter 身份字段并更新
- 调用消息 API（`media=1&voice=1`，增量模式传 `since`，全量模式不传）
- 语音消息自动转写
- 格式化为 chatlog 文本行并写入/追加

### 3x. 手动逐好友处理（参考）

以下为单好友手动处理流程，供调试或特殊场景使用。常规拉取请使用上面的批处理脚本。

对单个私聊好友：

**3a. 身份信息比对**

若 `raw/chatlog/<wxid>.md` 已存在，读取其 frontmatter，比对以下三个字段是否与 API 返回一致：

| frontmatter 字段 | API 字段 |
|---|---|
| `微信ID` | `username` |
| `显示名/昵称` | `displayName` |
| `微信备注` | `detailDescription` |
| `标签` | `labels`（每个标签加 `wechat-` 前缀写入 tags 数组） |

若任一字段有变化，更新 frontmatter 中的对应字段。无论是增量还是全量拉取，每次拉取后都要把 `最后拉取时间` 更新为当前时间。

**3b. 判断增量起点**

检查 `raw/chatlog/<wxid>.md` 是否已存在：
- **存在**：读取其 frontmatter 中的 `最后拉取时间` 字段。若该字段存在且是 ISO 格式，将其转为 Unix 秒级时间戳，作为 API 的 `since` 参数。若无法解析，则不传 `since`（全量拉取）。
- **不存在**：不传 `since`，全量拉取。

**3c. 调用消息 API（含媒体导出）**

```bash
curl -s -H "Authorization: Bearer <weflow_token>" \
  "http://127.0.0.1:5031/api/v1/messages?talker=<wxid>&limit=10000&since=<timestamp>&media=1&voice=1"
```

参数说明见 `references/weflow-api.md` 第 2 节。

**3d. 语音转文字**

若响应中有语音消息（`mediaType: "voice"`），用 `scripts/transcribe_voice.py` 转写：

```bash
# 本地 whisper-cpp 后端（默认）
python scripts/transcribe_voice.py \
  -i /tmp/wechat_response_<wxid>.json \
  --backend local \
  --whisper-url http://127.0.0.1:8080 \
  --replace-content \
  -o /tmp/wechat_response_<wxid>_transcribed.json

# 远程 doubao 后端
python scripts/transcribe_voice.py \
  -i /tmp/wechat_response_<wxid>.json \
  --backend remote \
  -k <asr_api_key> \
  --replace-content \
  -o /tmp/wechat_response_<wxid>_transcribed.json
```

`--replace-content` 会将 `[语音消息]` 替换为 `[语音] <转写文本>`。后端选择见「ASR 后端选择」一节。

**3e. 格式化消息为文本**

用 `scripts/format_messages.py` 将 API 返回的 JSON 转为 chatlog 文本行：

```bash
python scripts/format_messages.py \
  -n "<联系人名称>" \
  -i /tmp/wechat_response_<wxid>_transcribed.json
```

若没有语音消息（无需转写），则使用原始 JSON 文件。

联系人名称优先级：`detailDescription` > `nickname` > `displayName`。

脚本输出格式：`[YYYY-MM-DD HH:MM] <发送者>: <消息内容>`，按时间升序排列，空消息自动跳过。

**3f. 写入文件**

- **新文件**（首次拉取）：创建 `raw/chatlog/<wxid>.md`，写入完整 frontmatter + 全部消息
- **已有文件**（增量拉取）：在文件末尾追加新消息行，更新 frontmatter 中的 `最后拉取时间`

使用 obsidian-cli 技能进行文件读写。

### 4. 文件格式

**Frontmatter（必须包含所有字段）：**

```yaml
---
微信ID: <wxid>
显示名/昵称: <displayName>
微信备注: <detailDescription，无则留空>
手机号: <>
最后拉取时间: <当前时间 ISO 格式>
tags:
  - wechat
  - wechat-<label1>
  - wechat-<label2>
---
```

微信标签写入 `tags` 数组，每个标签加 `wechat-` 前缀（如 `wechat-保险`、`wechat-客户`）。无标签时只有 `wechat`。

**正文（标题 + 纯文本对话流，按时间顺序）：**

```
#微信聊天记录

[2026-06-01 14:30] 张三: 你好，好久不见
[2026-06-01 14:32] 我: 是啊，最近忙什么呢
[2026-06-02 09:15] 张三: 最近在考虑买保险的事
```

每条消息一行，无其他格式。

### 5. 汇总输出

拉取完成后输出汇总：

```
本次拉取汇总：
- 扫描好友：N 个
- 会话预筛选后：M 个（有新消息）
- 新增消息：X 条（分布在 K 个好友）
- 跳过（无新消息）：L 个
- 身份信息更新：P 个
- 失败：F 个
  - wxid_xxx: 连接超时
```

若 WeFlow 连接失败或 API 返回错误，提示用户确认 WeFlow 是否正常运行。

## 注意事项

- 每次拉取前必须比对 frontmatter 中的身份字段（微信ID、显示名/昵称、微信备注）和 tags 与 API 返回是否一致，有变化则更新。微信标签写入 `tags` 数组，每个标签加 `wechat-` 前缀
- 增量模式下，若已拉取的文件中 `最后拉取时间` 为空，则全量重新拉取（覆盖写入）
- 联系人手机号从 contacts API 的上一级数据中提取（如有），若 API 未返回则留空
- 只处理 `type: "friend"` 的私聊，群聊全部跳过
- 消息按 createTime 升序排列
- 增量模式下，`references/blacklist.md`（或 `--blacklist-file` 指定的文件）中的 wxid 会被跳过；新增黑名单 wxid 后再次运行增量模式不会回溯创建历史文件（因为增量模式本身只拉有新消息的好友）—— 如需补建或清理已存在的文件，请用全量模式或手动删除
- 用户黑名单 wxid 也用于全量模式（`--full`），确保即使重拉也不会创建黑名单文件
- **微信备注中的换行符处理**：API 返回的 `detailDescription` 字段可能包含换行符（`\n`），写入 YAML frontmatter 前必须将所有换行符替换为中文逗号（`，`），确保 frontmatter 格式不被破坏。例如 `"尹璐\n友邦善心浙里团队 王康\n20250303分享会认识"` → `"尹璐，友邦善心浙里团队 王康，20250303分享会认识"`
- **语音转写文本的换行符处理**：whisper-cpp（以及火山 doubao LLM 后端）在长语音上常以 `\n` 在子句边界分段，`format_messages.py` 一条消息占一行，未经处理会把一条语音拆成多行 chatlog。`transcribe_voice.py` 在 `transcribe_audio` 出口统一把所有换行（`\r` / `\n` / 连续换行）替换为单个中文逗号「，」并去掉首尾冗余的逗号/空白，确保一条语音最终落成一行，例如 `"我觉得是这样子的\n你就是应该按照规律性的每天都来参加早会\n除非有特别特别重要的事情..."` → `"我觉得是这样子的，你就是应该按照规律性的每天都来参加早会，除非有特别特别重要的事情..."`。远程 doubao 与本地 whisper 后端都走该归一化
