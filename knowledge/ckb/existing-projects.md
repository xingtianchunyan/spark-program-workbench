# CKB 生态已有项目与工具清单（重复资助检查用）

> 用途：预审时识别"重复造轮子"与"生态已有同类"，也是"建议在 X 基础上做"的依据。
> 来源：docs.nervos.org、生态公开资料、Spark 判例库，蒸馏于 2026-08。状态可能变化，引用前建议快速核实。

## 钱包

| 项目 | 形态 | 特点 |
|------|------|------|
| JoyID | Web/插件 | passkey 生物识别，无助记词，80 万+用户（2026-04） |
| Neuron | 桌面 | 官方全节点钱包，内置 Nervos DAO |
| CKBull | 移动端 | 移动钱包，**无浏览器插件形态** |
| imToken / OKX Wallet / SafePal | 移动/插件 | 多链钱包，已支持 CKB |
| UniSat | 插件 | 比特币系钱包，支持 RGB++ 资产 |

## 开发工具（官方/主流）

| 项目 | 用途 |
|------|------|
| CCC（CKBers' Codebase） | 现行推荐 JS/TS SDK，含钱包连接器 |
| OffCKB | 本地开发脚手架（dev 链一键启动） |
| ckb-cli | 官方命令行 |
| ckb-testtool | Rust 链上脚本单元测试 |
| CKB Indexer / Mercury | 链上数据索引 |
| CKB Explorer | 官方区块浏览器 |
| Lumos / ckb-sdk-js | 遗留/废弃，勿再推荐 |

## Fiber Network 生态

| 项目 | 状态 | 说明 |
|------|------|------|
| Fiber Node (FNN) | 主网 v0.9.0（2026-08） | 官方节点实现，仍持续迭代 |
| Fiber Dashboard | 运行中 | 网络结构与活动实时看板 |
| **Fiber Checkout** | ✅ Spark 资助已结项 | 页面版 Fiber 支付（同类提案须对比差异） |
| Fiber Link | 社区项目 | 打赏/微支付插件 |
| Fiber Audio Player | 示例应用 | 按秒流式微支付 |

## NFT / DOB / 资产

| 项目 | 说明 |
|------|------|
| Spore Protocol | 官方 DOB 协议（内容完全上链） |
| Omiga | 生态已有 NFT 市场（CKB NFT Market 提案因此被拒） |
| .bit（原 DAS） | DID/域名服务，跨链账户体系 |

## DeFi / 交易

| 项目 | 说明 |
|------|------|
| UTXOSwap | RGB++/CKB 资产的 AMM DEX |
| Fiber 跨网交换 | Fiber↔闪电网络原子交换（协议层能力） |

## 治理与资金渠道

| 渠道 | 说明 |
|------|------|
| Nervos DAO | 原生质押对冲通胀 |
| DAO 1.1 | 2026-02 主网上线的治理/财库系统 |
| **Community Fund DAO** | 大额/长期项目立项渠道（Spark 范围外项目的引导方向，Corven 判例） |
| Spark Program | 本计划：$1,000–$5,000 小额快审 |

## Spark 已资助项目（判例库快照，详见 knowledge/spark/precedents.md）

- 已结项：Fiber Checkout、Nervos Brain、Dular、Cell Sandbox（关停后完结）、CKB Developer Onboarding（关停）、Hash This（关闭）
- 进行中/待定：CKB UGMP、CKB Probe、CKB Builder Lab、Wallet Behaviour、Fiber RGB++ Swap、Cellar、zk-Lock、Spark Verify、Fibersplit、Joyid Connect、Programmable Recovery、NNCBN、Atlantik、VibeQuest、Spore Metadata、Fiber Pay Studio、TiKo、CellMint、CellKit Actions
- 被拒绝的典型方向：Appchain、QUIC、通用 NFT 市场、电商插件、AI 代码标记

## 使用注意

1. 本清单用于"是否已有同类"的初筛；引用具体项目状态前最好点开链接快速核实。
2. "已有同类"不等于一律拒绝——有明确差异化突破的仍可支持（判例：Fiber Checkout 之于 Fiber 官方 Demo）。
3. 清单更新时机：每次看板刷新发现新结项项目、或预审遇到清单外项目时，顺手补充。
