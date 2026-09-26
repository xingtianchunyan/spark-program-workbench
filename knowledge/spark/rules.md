# Spark Program 规则章程（工具唯一权威来源）

> 本文件是 spark_task_tracker.py 预审与起草功能的规则数据源。
> 修改规则只改这里；工具启动/起草时自动加载。代码内不再保留硬编码副本。
> 更新日期：2026-08-26

## 资金档位标准（2026-06 版，现行）

| 档位 | 上限 | 适用 | 说明 |
|------|------|------|------|
| 纯技术项目 | up to $1,000 | 开发工具、npm 包、CLI 等纯代码交付 | 超 $1,000 须充分论证，否则调档 |
| 纯社区项目 | up to $1,000 | 社区营销、种子用户测试、社区运营活动 | 同上 |
| 技术+社区综合 | up to $2,000 | 须同时包含技术交付与社区活动两部分 | 须提供详细论证 |
| 特殊/高难度 | up to $5,000 | 技术难度或生态价值显著超出常规 | 须全体委员会成员一致同意 |

判例：Fiber Checkout $1,200 → 调档 $1,000；CKB Probe $1,500（eBPF 论证）→ 维持 $1,000；
Nervos Brain 为首个综合类 $2,000；zk-Lock 预算不符惯例 → 委员会给出 $900 方案。

## 提案模板标准章节（格式完整性比对基准）

0. 标题与标签 / Title & Tags
1. 项目概述 / Project Overview
2. 团队简介 / Team Profile
3. 问题描述 / Problem Description
4. 解决方案 / Solution
5. 技术方案 / Technical Approach
6. 执行计划 / To-Do List
7. 所需资金 / Required Funding
8. 资金分配明细 / Funding Breakdown
9. 交付物 + 验证方式 / Deliverables + How to Verify
10. 当前状态 vs 资助范围 / Current State vs. Funded Work
11. CKB 契合度 / CKB Alignment（如适用）

## 预审格式检查清单（5 项高频问题）

| 检查项 | 常见错误 | 通过标准 |
|--------|---------|---------|
| 1. 核心信息前置 | 交付物+验证、时间线、预算藏在长篇大论后面 | 前 3 屏可见：项目概述、交付物+验证清单、资金 |
| 2. 交付物+验证清晰度 | "一个工具"——没有具体形态和验证步骤 | 每项交付物有：名称+形态+验收标准+验证步骤 |
| 3. To-Do List 按周 | "Week 1-4: 开发" | 每周有具体、可验收的子任务 |
| 4. 资金与范围匹配 | 申请 $1,500 但只做纯技术工作 | 金额与项目类型档位匹配，或有充分论证 |
| 5. CKB 契合度诚实 | 模糊关联 CKB 或过度包装 Web5 | 具体说明与 CKB 独特特性的关联，或诚实说明不直接涉及 |

## 验证方式标准

必须可通过**非代码审查**的方式**低成本、可复现**地验证：
- Docker 一键测试：`docker compose up`
- npm 包安装+测试：`npm install && npm test`
- 录屏验证
- 链上交易哈希验证

反面案例：需要 Linux 服务器 + root 权限 + 运行 CKB 全节点，且无简化方案。

## 结项政策（主帖 Section 3.3）

依据 "Spark Program: Mini-Grant Initiative" Section 3.3
（https://talk.nervos.org/t/spark-program-mini-grant-initiative/8752#h-33-12），
委员会将在以下情形终止项目并停止剩余拨款：
- 连续长时间（**2 周**）无实质进展且无合理原因
- 项目方向完全偏离原申请目标 / 资金使用严重偏离项目计划且未经委员会批准
- 发现重大造假或其他诚信问题

补充原则（2026-08-18 Cell Sandbox 判例）：对关停后来争论的项目原则上不继续支付；
Spark 资金用于支持开发者实现 MVP，而非购买申请人/团队已完成工时的报酬。

## 语言政策（2026-06 起）

中英文均可，论坛已接入 AI 智能翻译，不再强制双语版本——
预审时不得再以"缺少中文/双语版本"为由提出意见。

## 项目周期与范围

- 完成周期建议 1-2 个月，聚焦 MVP
- 范围过大的提案应建议缩减至可验收的最小闭环（判例：TiKo、NNCBN）
- 经济模型过大或需长期运营的项目，引导至 Community Fund DAO（判例：Corven）

## 委员会成员

| 成员 | 角色 | Nervos Talk | Discord |
|------|------|-------------|---------|
| zz_tovarishch | 版主/资金管理员 | zz_tovarishch | tovarishch.bit |
| Hanssen | 技术评审员 | Hanssen | hanssen0 |
| yixiu.ckbfans.bit | 社区建设者/文档工程师 | yixiu.ckbfans.bit | nervosyixiu.bit |
| xingtianchunyan | 预审联络员 | xingtianchunyan | xingtianchunyan |

正式决议回复惯例：以委员会名义发出，结尾 cc 其余三位委员
（cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan，按发起人不同相应变化）。
