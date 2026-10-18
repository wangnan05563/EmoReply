# 文档索引

EmoReply（AIMsg）全部文档导航。命名约定：`t0xxxx-*.md` 中的编号即 **MTask 任务号**（可在 MTask 按号检索任务与回传记录）。

## 规格与设计

| 文档 | 内容 |
| --- | --- |
| [需求规格说明书](需求规格说明书_高情商聊天回复助手.md) | SRS：范围、功能/非功能需求、验收口径（FR 编号体系的事实源） |
| [概要设计说明书](概要设计说明书_高情商聊天回复助手.md) | 架构、模块划分、关键决策 |
| [概要设计自检记录](概要设计说明书_评审自检记录.md) | 设计自检结论与遗留项 |
| [需求评审报告](需求评审报告.md) / [需求再评估](需求再评估_EmoReply.md) | 评审结论与范围再评估 |
| [项目计划](项目计划_EmoReply.md) | 里程碑与 WBS（任务卡在 [plan-cards/](plan-cards/)） |
| [M1 架构评审](t01391-m1-architecture-review.md) | 核心链路架构评审（热键等关键决策来源） |

## 优化迭代 PRD

| 文档 | 内容 |
| --- | --- |
| [跨平台优化 PRD](optimization-prd-2026-09-27.md) / [HLD](optimization-hld-2026-09-27.md) / [计划](optimization-plan-2026-09-27.md) / [跟踪矩阵](optimization-trace-matrix-2026-09-27.md) | 23 条需求（FR-7~FR-10）的四件套，M-Opt1~4 已全部验收 |
| [跨平台竞品调研](competitive-research-2026-09-27.md) | 同类产品调研与优化需求梳理 |
| [PC 端优化 PRD](t01360-pc-optimization-prd-2026-09-30.md) | 桌面端 FR-PC1~4 共 25 条（含流式、多模型并行、暗色主题等） |
| [PC 端竞品调研](t01360-pc-competitor-research-2026-09-30.md) | 10 款桌面端竞品对照 |

## 质量与测试报告

| 文档 | 内容 |
| --- | --- |
| [PC 端功能 × 体验 UI 全面测试](pc-e2e-report-20261003.md) | CDP 端到端 51 项 + 截图证据 + 缺陷修复记录 |
| [SonarQube 治理报告](t01362-sonar-fix-report-2026-09-30.md) | 123→6 治理与生产构建复验 |
| [Sonar 增量修复报告](sonar-reports/sonar-fix-report-20261001-1425.md) | 86→12 收敛过程与等价性验证 |
| [变更记录](CHANGELOG.md) | 清理轮次、构建、修复的时序记录 |
| 性能压测报告 | `../desktop-app/perf/output/regression_report.md`（+ 资源采样 CSV、原始样本 jsonl） |
| 工作空间清理报告 | `../cleanup-logs/cleanup-report-20261003.md`（配置见 `../cleanup-config.yaml`） |

## 任务验证记录（t0xxxx）

| 文档 | 内容 |
| --- | --- |
| [T01055 桌面端 RAG](t01055-desktop-rag-result.md) / [真机](t01055-device-test-result.md) / [iOS](t01055-ios-rag-result.md) | 三端 RAG 验证 |
| [T01081 修复](t01081-fix-result.md) / [T01082 多轮](t01082-multiturn-result.md) | 读屏/上下文修复 |
| [T01083 设置改版](t01083-settings-redesign.md) | 设置页设计 |
| [T01097 多平台入口评估](t01097-multiplatform-ingress-assessment.md) | 入口方案权衡 |
| [T01099 tsf-proto](t01099-tsf-proto.md) | 协议原型 |
| [T01163 图标落地](t01163-logo-result.md) / [跟进](t01163-followup-icon-integration.md) / [T01164 三端接入](t01164-icon-integration-result.md) | 品牌图标全链路 |
| [T01339 真机复检清单](t01339-device-recheck-checklist.md) | 待补真机验证清单（图标遮罩/读屏 1 屏/密钥掩码） |
| [T01343 读屏优化](t01343-readscreen-optimization-2026-09-29.md) | 侧别判定/范围/时间标记四项 |
| [T01345 钉钉智能回复分析](t01345-dingtalk-smart-reply-analysis-2026-09-29.md) | 宿主侧能力评估 |

## 环境与运维

| 文档 | 内容 |
| --- | --- |
| [Android 测试环境搭建指引](Android测试环境搭建指引.md) | 真机环境、ADB 与常见问题 |
| 一键脚本 | `../scripts/`：`安卓构建打包.cmd`、`安卓推送测试.cmd`、`PC构建打包.cmd`、`Gen-PatchVersion.ps1` |

## 维护提示

- 新增文档请按上述分类放入并在此登记；任务类文档沿用 `t<任务号>-<主题>[-result].md`。
- 文档中的截图证据统一放 `desktop-app/docs/screenshots/`（E2E 自动化产出，勿手工混放）。
- 清理/构建类记录进 [CHANGELOG](CHANGELOG.md)，不要新开文件。
