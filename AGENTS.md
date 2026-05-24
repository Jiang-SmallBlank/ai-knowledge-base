# AI 知识库助手

## 项目概述

自动从 GitHub Trending 和 Hacker News 采集 AI/LLM/Agent 领域的技术动态，通过 AI 分析后结构化存储为 JSON，并支持多渠道分发（Telegram / 飞书），帮助技术团队高效追踪行业前沿。

## 技术栈

- **Python** 3.12
- **Agent 框架**：OpenCode + 国产大模型（DeepSeek / GLM / Qwen）
- **工作流引擎**：LangGraph（多 Agent 编排与状态管理）
- **采集引擎**：OpenClaw（爬取 GitHub Trending、HN 等源）

## 编码规范

- 遵循 **PEP 8** 代码风格
- 命名使用 **snake_case**
- 函数/类必须写 **Google 风格 docstring**
- 禁止使用裸 `print()` — 一律使用 `logging` 模块
- 禁止硬编码密钥和 URL 等配置 — 统一放到 `.env`
- 类型注解全覆盖（mypy strict 模式）

## 项目结构

```
ai-knowledge-base/
├── .opencode/
│   ├── agents/          # Agent 行为定义与 prompt
│   ├── skills/          # 自定义 OpenCode skill
│   ├── .gitignore
│   ├── package.json
│   └── node_modules/
├── knowledge/
│   ├── raw/             # 原始采集数据（未经 AI 处理）
│   └── articles/        # AI 分析后的结构化 JSON
├── AGENTS.md            # Agent 系统文档（本文件）
└── README.md
```

## 知识条目 JSON 格式

```json
{
  "id": "20260524-001",
  "title": "OpenAI 发布 GPT-5 预览版",
  "source_url": "https://news.ycombinator.com/item?id=xxx",
  "source_type": "hackernews",
  "summary": "OpenAI 于今日发布了 GPT-5 的预览版本，主要改进包括...",
  "tags": ["openai", "gpt-5", "llm"],
  "status": "draft",
  "collected_at": "2026-05-24T10:00:00Z",
  "analyzed_at": "2026-05-24T10:05:00Z",
  "raw_content": "原始文章全文或摘要",
  "analysis": {
    "relevance_score": 9,
    "key_points": ["128K context window", "Agent 模式增强"],
    "impact": "可能改变 LLM Agent 的编排方式",
    "category": "model-release"
  }
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `id` | 格式 `YYYYMMDD-NNN`，同一天内自增 |
| `source_url` | 原文链接 |
| `source_type` | `github_trending` / `hackernews` |
| `summary` | AI 生成的 200 字以内摘要 |
| `tags` | 自动提取的标签 |
| `status` | `draft` → `reviewed` → `published` |
| `analysis` | AI 分析结果（评分、关键点、影响评估、分类） |
| `raw_content` | 原始采集内容 |

## Agent 角色概览

| 角色 | 职责 | 输入 | 输出 |
|------|------|------|------|
| **采集 Agent** | 定时爬取 GitHub Trending 和 HN | 源 URL 列表 | 写入 `knowledge/raw/` 的 Markdown |
| **分析 Agent** | AI 结构化分析、摘要、标签 | `knowledge/raw/` 中的原始数据 | 写入 `knowledge/articles/` 的 JSON |
| **整理 Agent** | 多渠道分发、状态流转审核 | `knowledge/articles/` 的 JSON | Telegram / 飞书消息 |

## 红线

以下操作 **严格禁止**，违反即视为事故：

1. **禁止将 API Key / Token 提交到 Git** — `.env` 已在 `.gitignore`，提交前须 double check
2. **禁止直接修改 `knowledge/articles/` 中的 JSON** — 必须通过分析 Agent 更新
3. **禁止跳过采集-分析-整理流程** — 不允许手动创建或补录知识条目
4. **禁止向用户频道发送未经 review 的内容** — `status` 必须为 `reviewed` 才能分发
5. **禁止在代码中硬编码 URL / 密钥 / 配置** — 外部配置必须通过环境变量或配置文件注入
