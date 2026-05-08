# ChatContextPlus - 群聊上下文增强插件

AstrBot 插件，用于接管群聊场景下的短期上下文记录、触发判断与临时上下文注入，保留本体 agent/tool 能力。

## 功能特性

- **群聊历史记录**：自动记录群聊中的所有消息，以 JSON 文件持久化存储
- **智能触发**：支持 @Bot、回复Bot、关键词、随机概率等多种触发方式
- **上下文注入**：将群聊历史以临时内容注入 LLM 请求，不污染本体对话历史
- **工具历史**：可选记录并注入工具调用历史
- **多平台隔离**：不同平台、不同群聊的历史完全隔离
- **防注入保护**：自动清洗用户消息中的危险标签

## 前置要求

**重要：安装本插件前，必须在 AstrBot 设置中关闭以下两项功能：**

1. **群聊上下文感知** — 关闭
2. **主动回复** — 关闭

本插件会完全接管群聊的上下文管理和触发逻辑，与上述本体功能同时开启会产生冲突。

## 安装

将本插件目录放置到 AstrBot 的插件目录中，或通过 AstrBot 插件管理界面安装。

## 配置项

安装后可在 AstrBot WebUI 中配置，分为两组：

### 群聊上下文增强 (group_context)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | bool | `true` | 启用插件 |
| `store_max_events` | int | `100` | 每个群聊最多保存的事件数 |
| `inject_message_count` | int | `30` | 注入给 LLM 的最近消息数量（仅计消息，工具事件不占用） |
| `enable_tool_history` | bool | `false` | 是否记录并注入工具调用历史 |
| `tool_args_max_chars` | int | `500` | 工具参数截断长度 |
| `tool_result_max_chars` | int | `1000` | 工具结果截断长度 |

### 回复触发 (reply)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `trigger_on_at` | bool | `true` | 被 @Bot 时触发回复 |
| `trigger_on_reply` | bool | `true` | 回复 Bot 消息时触发回复 |
| `trigger_keywords` | list | `[]` | 触发关键词列表 |
| `blacklist_keywords` | list | `[]` | 黑名单关键词（命中则不触发，优先级最高） |
| `active_reply_probability` | float | `0.0` | 主动回复概率 (0.0~1.0) |

## 指令

| 指令 | 说明 |
|------|------|
| `/ccp status` | 查看当前群聊的插件状态和历史数量 |
| `/ccp clear` | 清空当前群聊的历史记录 |
| `/ccp history` | 查看最近保存的历史消息摘要 |

## 工作原理

### 消息流程

1. 收到群聊消息 → 记录到 JSON → 规则判断是否触发 LLM
2. 触发后在 `on_llm_request` 中：
   - 清空 `req.contexts`（由插件接管上下文）
   - 渲染 `<history>` 和可选的 `<tool_history>`
   - 通过 `extra_user_content_parts` + `mark_as_temp()` 注入（不写入对话历史）
3. Bot 回复后记录到 JSON

### 数据存储

```
data/plugin_data/astrbot_plugin_chat_context_plus/
└── sessions/
    ├── aiocqhttp/
    │   └── group/
    │       └── 123456.json
    └── telegram/
        └── group/
            └── 789012.json
```

### 触发优先级

1. 黑名单关键词命中 → **不触发**
2. 被 @Bot → 触发
3. 回复 Bot 消息 → 触发
4. 触发关键词命中 → 触发
5. 主动回复概率 → 触发
6. 以上都未命中 → **不触发**

## 注意事项

- 插件**不会**删除或修改 AstrBot 本体的 DB 对话历史
- 插件注入的历史默认使用 `mark_as_temp()`，不写入本体 conversation history
- 每轮请求中 `req.contexts` 会被清空，确保本体历史不进入当前 LLM 请求
- 当前轮的 agent/tool 调用能力不受影响

## 兼容性

- 要求 AstrBot >= 4.24
- 支持所有平台适配器（aiocqhttp、Telegram 等）

## 许可证

MIT License
