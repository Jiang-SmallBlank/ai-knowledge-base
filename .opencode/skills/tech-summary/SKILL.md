---
name: tech-summary
description: 当需要对采集的技术内容进行深度分析总结时使用此技能
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# 技术内容深度分析技能

## 使用场景

- 对采集的 GitHub Trending / Hacker News 原始数据进行 AI 深度分析
- 生成结构化知识条目，用于入库和分发
- 发现技术趋势和新兴概念

## 执行步骤

1. **读取最新采集文件** — 扫描 `knowledge/raw/` 目录，按修改时间找到最新的 JSON 文件，读取全部条目
2. **逐条深度分析** — 对每条内容执行：
   - **摘要**：50 字以内中文，提炼核心信息
   - **技术亮点**：2-3 个，用事实和数据说话，避免空泛评价
   - **评分**：1-10 分（见评分标准），附评分理由
   - **标签**：建议 2-5 个标签，优先使用已有标签体系
3. **趋势发现** — 综合所有条目，识别：
   - 共同主题（哪些方向最热门）
   - 新兴概念（近期首次出现的技术或理念）
4. **输出 JSON** — 写入 `knowledge/articles/tech-summary-YYYY-MM-DD-HHmmss.json`

## 评分标准

| 分值 | 含义 | 典型特征 |
|------|------|---------|
| 9-10 | 改变格局 | 新范式、重大突破、可能重塑行业 |
| 7-8 | 直接有帮助 | 解决实际痛点、可落地、效果显著 |
| 5-6 | 值得了解 | 有价值但非关键、概念验证阶段 |
| 1-4 | 可略过 | 增量改进、炒作大于实质、信息量少 |

## 约束

- 15 个项目中，评分 9-10 的条目不超过 2 个
- 若已有 `knowledge/articles/` 中存在同标题/同 URL 条目，应在 `analysis` 中标注 `updated: true` 而非重复创建

## 输出格式

```json
{
  "skill": "tech-summary",
  "analyzed_at": "2026-05-24T10:00:00Z",
  "source_file": "github-trending-2026-05-24.json",
  "items": [
    {
      "title": "LangGraph v0.3: 状态图驱动的 Agent 编排框架",
      "summary": "LangGraph 发布 v0.3，支持条件分支和并行节点执行，大幅降低复杂 Agent 开发门槛",
      "highlights": [
        "v0.3 新增条件边（conditional_edge）API，实现动态路由",
        "并行节点支持将推理速度提升 3 倍（官方基准测试）"
      ],
      "relevance_score": 8,
      "score_reason": "直接提升 Agent 编排效率，有明确性能数据和 API 改进",
      "tags": ["agent", "langgraph", "workflow"],
      "updated": false
    }
  ],
  "trends": {
    "common_themes": [
      "Agent 编排框架成为 2026 年 Q2 最活跃方向"
    ],
    "emerging_concepts": [
      "状态图驱动的 Agent 工作流正在取代线性 Chain"
    ]
  }
}
```
