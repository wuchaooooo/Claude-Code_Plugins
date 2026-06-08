# WeFlow HTTP API Reference

WeFlow 本地 HTTP API（`127.0.0.1:5031`），支持 GET 和 POST 请求。

## 启用方式

在 WeFlow 应用设置页启用 `API 服务`。

- 默认监听地址：`127.0.0.1`
- 默认端口：`5031`
- 基础地址：`http://127.0.0.1:5031`
- 可选开启 `主动推送`，通过 `GET /api/v1/push/messages` 推送 SSE 事件

## 鉴权

除 `/health` 外，所有 `/api/v1/*` 接口需要 Access Token，三种传参方式任选其一：

1. **HTTP Header（推荐）**: `Authorization: Bearer <Token>`
2. **Query 参数**: `?access_token=<Token>`
3. **JSON Body**: `{"access_token": "<Token>"}`（仅 POST）

---

## 接口索引

| 接口 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /api/v1/health` | 健康检查（同） |
| `GET /api/v1/push/messages` | SSE 主动推送新消息事件 |
| `GET/POST /api/v1/messages` | 获取消息（JSON / ChatLab） |
| `GET/POST /api/v1/sessions` | 获取会话列表 |
| `GET /api/v1/sessions/:id/messages` | ChatLab Pull 会话消息 |
| `GET/POST /api/v1/contacts` | 获取联系人列表 |
| `GET/POST /api/v1/group-members` | 获取群成员列表 |
| `GET/POST /api/v1/media/*` | 访问导出媒体文件 |
| `GET /api/v1/sns/timeline` | 朋友圈时间线 |
| `GET /api/v1/sns/usernames` | 朋友圈发布者列表 |
| `GET /api/v1/sns/export/stats` | 朋友圈导出统计 |

---

## 1. 健康检查

```http
GET /health
```

响应：

```json
{ "status": "ok" }
```

---

## 2. 获取消息

```http
GET /api/v1/messages
```

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `talker` | string | 是 | 会话 ID。私聊为对方 `wxid`，群聊为 `xxx@chatroom` |
| `limit` | number | 否 | 返回条数，默认 `100`，范围 `1~10000` |
| `offset` | number | 否 | 分页偏移，默认 `0` |
| `start` | string | 否 | 开始时间，支持 `YYYYMMDD` 或时间戳 |
| `end` | string | 否 | 结束时间，支持 `YYYYMMDD` 或时间戳 |
| `keyword` | string | 否 | 基于消息显示文本过滤 |
| `chatlab` | string | 否 | `1/true` 时输出 ChatLab 格式 |
| `format` | string | 否 | `json` 或 `chatlab` |
| `media` | string | 否 | `1/true` 时导出媒体并返回媒体地址 |
| `image` | string | 否 | `media=1` 时控制图片导出 |
| `voice` | string | 否 | `media=1` 时控制语音导出 |
| `video` | string | 否 | `media=1` 时控制视频导出 |
| `emoji` | string | 否 | `media=1` 时控制表情导出 |

### JSON 响应顶层字段

- `success` — 是否成功
- `talker` — 会话 ID
- `count` — 本次返回条数
- `hasMore` — 是否还有更多数据
- `media.enabled` — 媒体导出是否启用
- `media.exportPath` — 媒体导出路径
- `media.count` — 媒体数量
- `messages` — 消息数组

### 单条消息字段

| 字段 | 说明 |
|------|------|
| `localId` | 本地 ID |
| `serverId` | 服务端 ID |
| `localType` | 消息类型 |
| `createTime` | 消息时间（秒级 Unix 时间戳） |
| `isSend` | 0=收到，1=发出 |
| `senderUsername` | 发送者 wxid |
| `content` | 消息文本内容 |
| `rawContent` | 原始 XML 内容 |
| `parsedContent` | 解析后内容 |
| `replyToMessageId` | 引用回复目标消息的 serverId（仅引用消息） |
| `quote` | 引用消息快照 |
| `mediaType` | 媒体类型（`image`/`voice`/`video`/`emoji`） |
| `mediaFileName` | 媒体文件名 |
| `mediaUrl` | 媒体访问 URL |
| `mediaLocalPath` | 媒体本地路径 |

### 示例

```bash
# 基础拉取
curl -H "Authorization: Bearer TOKEN" \
  "http://127.0.0.1:5031/api/v1/messages?talker=wxid_xxx&limit=10000"

# 增量拉取（since 为 Unix 秒级时间戳）
curl -H "Authorization: Bearer TOKEN" \
  "http://127.0.0.1:5031/api/v1/messages?talker=wxid_xxx&limit=10000&since=1738713600"

# 按日期范围
curl -H "Authorization: Bearer TOKEN" \
  "http://127.0.0.1:5031/api/v1/messages?talker=wxid_xxx&start=20260101&end=20260131"
```

### 示例响应

```json
{
  "success": true,
  "talker": "wxid_xxx",
  "count": 2,
  "hasMore": false,
  "messages": [
    {
      "localId": 123,
      "serverId": "6116895530414915131",
      "localType": 1,
      "createTime": 1738713600,
      "isSend": 0,
      "senderUsername": "wxid_xxx",
      "content": "你好",
      "rawContent": "你好",
      "parsedContent": "你好"
    },
    {
      "localId": 124,
      "serverId": "6116895530414915135",
      "localType": 1,
      "createTime": 1738713660,
      "isSend": 1,
      "senderUsername": "wxid_self",
      "content": "好久不见",
      "rawContent": "好久不见",
      "parsedContent": "好久不见"
    }
  ]
}
```

---

## 3. 获取会话列表

```http
GET /api/v1/sessions
```

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `keyword` | string | 否 | 匹配 `username` 或 `displayName` |
| `limit` | number | 否 | 默认 `100` |
| `format` | string | 否 | 设为 `chatlab` 输出 ChatLab Pull 兼容格式 |

### 响应字段

- `success`
- `count`
- `sessions[].username` — 会话 ID（wxid 或 xxx@chatroom）
- `sessions[].displayName` — 显示名称
- `sessions[].type` — 类型（私聊/群聊）
- `sessions[].lastTimestamp` — 最后消息时间（秒级 Unix 时间戳）
- `sessions[].unreadCount` — 未读数量

---

## 4. 获取联系人列表

```http
GET /api/v1/contacts
```

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `keyword` | string | 否 | 匹配 `username`、`nickname`、`remark`、`displayName` |
| `limit` | number | 否 | 默认 `100` |

### 响应字段

- `success`
- `count`
- `contacts[].username` — wxid
- `contacts[].displayName` — 显示名称
- `contacts[].detailDescription` — 微信备注（联系人详情描述）
- `contacts[].labels` — 微信标签数组
- `contacts[].remark` — 备注（旧字段，可能不存在）
- `contacts[].nickname` — 昵称
- `contacts[].alias` — 微信号
- `contacts[].avatarUrl` — 头像 URL
- `contacts[].type` — 类型（`friend` 等）

私聊好友筛选条件：`type == "friend"`。

---

## 5. 获取群成员列表

```http
GET /api/v1/group-members
```

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `chatroomId` | string | 是 | 群 ID（兼容别名 `talker`） |
| `includeMessageCounts` | string | 否 | `1/true` 时附带发言数 |
| `forceRefresh` | string | 否 | `1/true` 时跳过缓存 |

### 响应字段

- `success`
- `chatroomId`
- `count`
- `fromCache`
- `members[].wxid`
- `members[].displayName`
- `members[].nickname`
- `members[].remark`
- `members[].alias`
- `members[].groupNickname`
- `members[].avatarUrl`
- `members[].isOwner`
- `members[].isFriend`
- `members[].messageCount`（需 `includeMessageCounts=1`）

---

## 6. SSE 主动推送

```http
GET /api/v1/push/messages
```

需在设置页同时开启 `HTTP API 服务` 和 `主动推送`。

事件类型：
- `message.new` — 新消息
- `message.revoke` — 撤回消息

事件字段：`event`, `sessionId`, `rawid`, `avatarUrl`, `sourceName`, `groupName`（仅群聊）, `content`, `timestamp`

---

## 7. 媒体访问

```http
GET /api/v1/media/{relativePath}
```

仅在消息已通过 `media=1` 导出后可访问。支持的 Content-Type：`image/png`, `image/jpeg`, `image/gif`, `image/webp`, `audio/wav`, `audio/mpeg`, `video/mp4`。

---

## 注意事项

1. API 仅监听 `127.0.0.1`，不对外网开放
2. 使用前需要 WeFlow 已完成数据库连接
3. `start`/`end` 支持 `YYYYMMDD` 与时间戳；纯日期的 `end` 扩展到当天 23:59:59
4. 群成员的 `groupNickname` 依赖微信源数据，缺失时为空
5. 媒体访问仅在对应消息已通过 `media=1` 导出后可用
