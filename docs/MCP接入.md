# MCP 接入

把数仓、指标口径与归因能力暴露给 Claude Desktop / Cursor。

## 配置

Claude Desktop 的 `claude_desktop_config.json`（macOS 位于
`~/Library/Application Support/Claude/claude_desktop_config.json`）加入：

```json
{
  "mcpServers": {
    "channel-insight": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/liurui/workspace/简历agent项目",
               "python", "-m", "app.mcp.server"],
      "env": { "PYTHONPATH": "/Users/liurui/workspace/简历agent项目" }
    }
  }
}
```

Cursor 用 `.cursor/mcp.json`，字段相同。

## 暴露的五个工具

| 工具 | 用途 |
|---|---|
| `ask_data` | 自然语言问数，走完整链路，返回 SQL 与结果 |
| `query_warehouse` | 直接执行只读 SQL，复用问数链路同一套 guardrail |
| `list_metrics` | 12 项指标口径，含计算式与易错点 |
| `describe_schema` | 表结构、业务语义、关联路径 |
| `attribute_metric` | 指标异动定量归因，返回主因与下钻路径 |

## 设计要点

**暴露的不是裸 SQL 通道，是带口径的能力。** server 的 instructions 明确要求
「写 SQL 前先调用 list_metrics」——外部客户端拿到的是 12 项指标的精确定义与陷阱说明，
而不是让它自己从表结构猜口径。费效比那类跨粒度关联，靠猜必错。

**guardrail 与问数链路共用同一份实现。** `query_warehouse` 直接调用
`app.agent.nodes.sql.guardrail`，非 SELECT、写操作、越权表一律拒绝，
执行前还要过 EXPLAIN。实测：`DROP TABLE` 被拦为「非 SELECT 语句」，
`information_schema` 被拦为「引用了不存在或未授权的表」。

**归因拒绝比率指标。** `attribute_metric` 只接受可加指标，
传 `promo_roi` 会明确返回「不是可加指标」而不是给出一个无意义的分解。
