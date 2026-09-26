# Spark Program · Notion Dashboard 自动化操作规格

> 目的：通过 Notion 官方 MCP 将工作流末端的 Dashboard 填写自动化。
> 状态：复制后的 Dashboard 已位于当前 Notion MCP 可读写的工作区；四张数据库 schema 与 Quantum Purse 模板均已核验。Tranfr 已于 2026-09-23 完成首次入库并回读验证。
> 创建：2026-09-19，依据用户口述工作流 + 4 张 Notion 截图整理。

## 0. 当前工作区页面与数据源速查（已验证）

| 对象 | 页面 / 数据源 ID |
|---|---|
| 主 Dashboard 页面 | `2a0639bc0d1b833ca3ca010fd134795b` |
| Fund Pool Status 页面 | `146639bc0d1b83c18f1181a8fa1c9261` |
| 资金分配情况数据源 | `collection://589639bc-0d1b-83ba-a2f3-075d058cd339` |
| 重要交易记录数据源 | `collection://ea3639bc-0d1b-82e3-8324-873a96961c01` |
| Project List 页面 | `e95639bc0d1b83f5952d811d03c925b3` |
| 进行中项目数据源 | `collection://385639bc-0d1b-826e-a95d-077aac42d7c1` |
| 已完成项目数据源 | `collection://5e0639bc-0d1b-8254-b985-075b92de3eb4` |
| 项目页模板（Quantum Purse） | `fc5639bc0d1b8340ab758160d2bb774d` |

已确认的 Select 字面量包括：`进行中 / Ongoing`、`已完成 / Completed`、
`关停 / Shutdown`、`提款 / Withdrawal` 与 `存款 / Deposit`。

## 1. 场景 A：新项目入库（帖子新加入 In-Progress 标签）

### A1. 资金分配情况表新增一行
字段（依据截图 1）：

| 列 | 类型（推测） | 填入内容 |
|---|---|---|
| 项目名称 / Project Name | title | 项目名 |
| 分配金额 / Allocated Amount | text | 如 `132,075 CKB` 或 `86,030 CKB + 400 USDI` |
| 状态 / Status | select | `进行中 / Ongoing` |
| 项目多签钱包地址 / Project Multisig Wallet Address | text | ckb1... 地址 |

### A2. 重要交易记录表新增 N 行（每种加密货币一笔）
字段（依据截图 2）：

| 列 | 类型（推测） | 填入内容 |
|---|---|---|
| 交易哈希 / TX Hash | title | 0x... |
| 交易类型 / Type | select | `提款 / Withdrawal` |
| 日期 / Date | date | 转账日期 |
| 金额 / Amount | text | 如 `132,075 CKB` / `400 USDI` |
| 用途 / Purpose | text | 惯例格式：`To {项目名} Multisig Wallet` |

### A3. 进行中项目表新建项目页（新增一行 = 新建页面）
字段（依据截图 3）：

| 列 | 填入内容 |
|---|---|
| 项目名称 / Project Name | 项目名（title） |
| 团队 / Team | 申请人 ID（如 duongja） |
| 开始日期 / Start Date | 日期 |
| 预计完成日期 / Est. Completion | 日期 |
| 总资助金额 / Total Funding Amount | 惯例格式：`$2000 USD (100% CKB, 1,409,000 CKB)` |
| 项目多签钱包地址 | ckb1... 地址 |
| 已发放 / Distributed | 百分比文本，如 `20%` |
| 状态 / Status | `进行中 / Ongoing` |

### A4. 项目页内容初始化
1. 读取复制后的 Quantum Purse 页面（`fc5639bc0d1b8340ab758160d2bb774d`）作为模板。
2. 在新项目页复制模板结构（截图 4 红框所示模块）：
   - 基本信息 / Basic Information（项目申请链接、团队多签钱包地址）
   - 评估结果 / Evaluation Result（批准日期、批准理由）
   - 资金使用情况 / Fund Usage（初始化）
   - 进度更新 / Progress Update（初始化）
   - 项目调整 / Project Adjustment（初始化）
3. 填入新项目初始信息；未发生的段落保留模板占位。

## 2. 场景 B：每周更新（已记录的进行中项目）

1. 在进行中项目表中按「项目名称」查询定位项目页。
2. 在项目页内追加/更新三段内容：
   - 资金使用情况 / Fund Usage
   - 进度更新 / Progress Update
   - 项目调整 / Project Adjustment
3. 具体格式以 Quantum Purse 页面为准（首次执行时读取其 blocks 后固化段落模板）。
4. 若「已发放 / Distributed」比例变化，同步更新进行中项目表对应行的该字段。

## 3. 场景 C：项目结项（Completion / Closure）

1. 定位进行中项目表中的项目页。
2. 项目页内填入四段：资金使用情况、进度更新、项目调整、**结项评价**。
3. 在已完成项目数据源（`collection://5e0639bc-0d1b-8254-b985-075b92de3eb4`）中新建页面。
4. 将原项目页全部内容复制到新页面（逐 block 复制，不遗漏）。
5. 已完成项目表对应行填写 11 个字段（**逐一对照，不得错误覆盖**）：

| 列 | 填入内容 |
|---|---|
| 项目名称 / Project Name | 项目名 |
| 团队 / Team | 申请人 ID |
| 开始日期 / Start Date | 原值照抄 |
| 完成日期 / Complete Date | 实际结项日期（新信息） |
| 资助金额 / Funding Amount | 如 `132,075 CKB` 或 `86,030 CKB + 400 USDI` |
| 项目多签钱包地址 | 原值照抄 |
| 成果链接 / Deliverables | 链接 |
| 成果链接 2 | 链接（如有） |
| 总资助金额 / Total Funding Amount | `$xxx USD (100% CKB, ...)` 格式 |
| 状态 / Status | `已完成` 或 `关停` |
| 预计完成日期 / Est. Completion | 原值照抄 |

6. 收尾联动（待用户确认是否一并做）：
   - 进行中项目表中的对应行删除或状态改为已完成；
   - 资金分配情况表中该行状态由「进行中」改为「已完成」。

## 4. 触发与集成方式（规划）

- **近期（手动触发）**：在对话中发指令，如「入库新项目 X，金额…，交易哈希…」，
  Kimi 通过 MCP 顺序执行场景 A 全部步骤并回报每步结果。
- **中期（半自动）**：spark_task_tracker 看板检测到帖子进入 In-Progress /
  Completion / Closure 标签时生成对应任务，用户把任务要点贴给 Kimi 一键执行。
- **远期（定时任务）**：挂 Blueprint 定时任务，周报周期自动执行场景 B。

## 5. 首轮验证结果

- [x] retrieve 四张表 schema，记录真实属性名、类型和 Select 选项
- [x] 读取 Quantum Purse 页面并固化项目页五段式结构
- [x] 确认 MCP 对复制后的 Dashboard 有读写权限
- [x] 以 Tranfr 完成首单：写入三张表、回读验证、更新本地任务状态

## 6. 幂等、重试与任务完成规则（v2）

### 6.1 唯一标识

- 每个项目同步作业使用 `nervos:{topic_id}:{workflow}:v1` 作为幂等键。
- 三张目标数据库建议新增隐藏属性 `Nervos Topic ID`；写入前必须先按该属性查询。
- 在数据库尚未增加该属性前，临时使用「项目名称 + 钱包地址」或「交易哈希」组合查询，但不得盲目新增。
- 交易记录以交易哈希为首要唯一键；同一交易哈希已存在时执行核对，不再创建第二行。

### 6.2 重试协议

1. 写入前查询目标记录。
2. 已存在且字段一致：视为成功，记录现有页面 ID。
3. 已存在但字段冲突：停止并进入 `conflict`，不得覆盖。
4. 不存在：创建记录。
5. 创建后重新读取，逐字段验证。
6. 网络超时或结果不确定：重新查询，不直接再次创建。

### 6.3 本地状态

- `notion_tasks_queued`：已创建本地任务，但尚未代表 Notion 成功。
- 每个新 In-Progress 项目仍自动创建四项任务：更新 Dashboard、更新项目列表、更新资金池状态、更新 Spark 联系人列表。
- `notion_tasks_created`：当前已实现的两个核心步骤（项目列表、资金池）均写入并回读验证成功后，才记录 topic ID；这不表示 Dashboard/联系人任务被取消。
- `notion_sync_jobs[topic_id].steps.project_list`：项目列表步骤状态与页面 ID。
- `notion_sync_jobs[topic_id].steps.fund_pool`：资金池步骤状态与页面 ID。
- `notion_sync_jobs[topic_id].steps.dashboard`：保留为后续 Dashboard 自动化，当前状态为 `planned`。
- `notion_sync_jobs[topic_id].steps.contacts`：保留为后续联系人列表自动化，当前状态为 `planned`。
- 只有已接入自动验证的步骤为 `verified` 时，任务页中的对应项才自动勾选。

### 6.4 四项任务的分阶段实现

| 任务 | 是否继续自动创建 | 当前执行方式 |
|---|---|---|
| 更新 Dashboard | 是 | 保留任务，后续补充具体自动化规则 |
| 更新项目列表 | 是 | 自动写入、回读验证、自动勾选 |
| 更新资金池状态 | 是 | 自动写入、回读验证、自动勾选 |
| 更新 Spark 联系人列表 | 是 | 保留任务，后续补充具体自动化规则 |

### 6.5 Tranfr 首次执行参数

- Nervos Topic ID：`10644`
- 幂等键：`nervos:10644:in-progress:v1`
- 建议开始日期：`2026-09-17`（首期款发放并进入 In-Progress 的日期）
- 预计完成日期：`2026-11-12`（开始日期后 8 周）
- 若委员会采用批准日作为项目开始日，则改为 `2026-09-02` / `2026-10-28`；写入前应以目标表既有项目口径复核。

## 7. 当前连接状态

旧访客工作区不再作为自动化写入目标。所有新写入均指向用户个人工作区中的复制版
Dashboard；若未来重新复制或移动数据库，必须先重新 fetch 页面和 schema，并更新本节
data source ID，不能沿用旧 ID 盲写。

### 7.1 Tranfr 首次写入结果（2026-09-23）

- 资金分配记录：`3e4639bc-0d1b-8149-a296-d152b4faa84f`
- 提款交易记录：`3e4639bc-0d1b-814d-86e5-d9a045ddc94d`
- 进行中项目页：`3e4639bc-0d1b-81a6-8011-f71927e9cc22`
- 写入前已按项目名和完整交易哈希查重；创建后已逐页回读验证。

## 8. 本地浏览器执行器（v3）

本地浏览器版位于 `spark_web/`，由 `Launch-Web.vbs` 启动，固定监听
`127.0.0.1`。它使用 SQLite 保存任务、同步步骤、外部记录和审计事件，并在首次
启动时导入 `spark_task_data.json`。

### 8.1 授权选择

- 交互式操作可以继续使用官方 Notion MCP。
- 官方 Hosted MCP 当前要求交互式 OAuth，不能作为完全无人值守任务的授权来源。
- 无人值守工作流使用 Notion 官方 REST API 的 Personal Access Token 或内部连接
  token；token 只存放在本机 `.env`。

### 8.2 已实现工作流

- `in_progress`：资金分配 → 交易记录 → 进行中项目页 → 模板内容 → 回读验证。
- `weekly`：按论坛帖子编号生成唯一键，把资金使用、进度、调整追加到项目页。
- `completion` / `closure`：复制进行中项目内容到已完成项目表，逐字段填写 11 个
  属性，更新进行中项目状态和资金分配状态。

### 8.3 失败规则

- 缺少字段：`needs_input`，不写入。
- 已有记录字段不一致：`conflict`，不覆盖。
- 网络结果不确定：下一次先查询自然键，不直接重复创建。
- 只有回读字段全部一致：`verified`，并自动勾选对应核心任务。
- Dashboard 和联系人任务继续创建；在业务规则补充前维持 `planned`。
