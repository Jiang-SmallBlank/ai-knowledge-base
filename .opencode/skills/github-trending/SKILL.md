---
name: github-trending
description: 当需要采集 GitHub 热门开源项目时使用此技能
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# GitHub Trending 采集技能

## 使用场景

- 每日定时采集 GitHub Trending 热门项目
- 追踪 AI/LLM/Agent 领域的最新开源动态
- 为知识库补充高质量开源项目信息

## 执行步骤

1. **搜索热门仓库** — 通过 GitHub API (`https://api.github.com/search/repositories`) 获取当日热门仓库，按 stars 排序，限定最近 7 天创建或更新的项目
2. **提取信息** — 从搜索结果中提取：项目名称、URL、描述、stars 数、语言、topic 标签
3. **过滤** — 仅保留与 AI/LLM/Agent 相关的项目；排除 Awesome 列表类仓库（名称或描述包含 "awesome"、"awesome-list"）
4. **去重** — 对比 `knowledge/articles/` 中已有的 JSON 条目，排除已收录的项目
5. **撰写中文摘要** — 格式为「项目名 + 做什么 + 为什么值得关注」，每条约 50-100 字
6. **排序取 Top 15** — 按 stars 数降序排列，取前 15 个
7. **输出 JSON** — 写入 `knowledge/raw/github-trending-YYYY-MM-DD.json`

## 注意事项

- 遵守 GitHub API 速率限制（未认证 60 req/h，建议配置 `GITHUB_TOKEN`）
- 同一项目在同一天内不重复采集
- 过滤时需同时检查 `topics` 和 `description` 字段
- 排除标准：Awesome 列表、非中文摘要项目（非必须）、与 AI/LLM/Agent 无关

## 输出格式

```json
{
  "source": "github_trending",
  "skill": "github-trending",
  "collected_at": "2026-05-24T10:00:00Z",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "summary": "LangGraph 是一个用于构建 LLM Agent 编排的工作流引擎，支持状态图驱动的多步推理，值得关注的理由是它简化了复杂 Agent 流程的开发",
      "stars": 12800,
      "language": "Python",
      "topics": ["llm", "agent", "langchain"]
    }
  ]
}
```
