# CKB 开发栈（验证方式与可行性判断基准）

> 用途：预审时判断提案的"验证方式是否低成本可复现"、技术选型是否过时。
> 来源：github.com/ckb-ccc、github.com/ckb-js、docs.nervos.org，蒸馏于 2026-08。

## SDK 现状（重要：避免推荐过时栈）

| SDK | 状态 | 评审口径 |
|-----|------|---------|
| **CCC（CKBers' Codebase）** | ✅ 现行推荐 | JS/TS 一站式 SDK，钱包连接、交易组装、UDT/DOB 操作均有封装。新提案前端/脚本开发应默认基于 CCC |
| **Lumos** | ⚠️ 遗留维护 | 官方仓库已建议改用 CCC；有 `@ckb-ccc/lumos-patches` 过渡包。提案若从零选用 Lumos 须问明理由 |
| **ckb-sdk-js** | ❌ 已废弃 | 仓库标注 obsolete，仅被动维护 |
| ckb-sdk-rust / ckb-sdk-java / Go SDK | 维护中 | 按语言生态可选，Rust SDK 与核心仓库同步度高 |

判例：Cell Sandbox（2026-06-23）被追问"没有直接用 CCC 提供的钱包选择器而是自己造 UI"——**重复造 CCC 已有轮子是高频扣分点**。

## 本地开发与测试链

- **OffCKB**：现行本地开发脚手架（一条命令起 dev 链 + 内置测试账户 + 部署脚本），提案 PoC 阶段的标准选择。
- **dev 链**：`ckb-cli` 或 OffCKB 本地起链，出块快、可重置，适合 CI。
- **测试网（Pudge）**：公开测试网 + faucet 领水，主网部署前的必经环境。
- **验证方式判例**：要求预审可 `docker compose up` 或 `npm install && npm test` 复现；"需要 Linux 服务器+root+全节点"且无简化方案为反面案例。

## 节点与数据服务

- **全节点（ckb）**：Rust 实现，支持 mainnet/testnet/devnet；同步主网需要相当磁盘与时间，不适合作为"用户侧验证"前提。
- **公共 RPC**：社区与官方均有公共 RPC 端点（如 testnet/mainnet 公共节点），评审时注意提案是否假设了"自建节点"。
- **CKB Indexer / Mercury / ckb-light-client**：索引与轻客户端方案；新提案一般直接用 CCC 封装的 indexer 接口，而非自建索引。

## 命令行与部署工具

- **ckb-cli**：官方 CLI（账户、交易、DAO、部署脚本交互），兼容 Neuron 的 keystore。
- **部署脚本（type id 升级）**：链上脚本部署后可按 type id 升级——提案若声称"不可升级保证安全"或"随意升级无风险"都需追问治理设计。

## 智能合约（链上脚本）开发

- 主流语言 **Rust**（ckb-std 库），也有 C；无 EVM 兼容层（Godwoken 已淡出，勿按"CKB 上跑 Solidity"的旧认知评审）。
- 测试框架：ckb-testtool（Rust 单元测试，模拟链上环境，无需起链）。
- **评审要点**：脚本类提案应能看到 ckb-testtool 测试或可复现的 dev 链部署脚本；纯口头"已完成合约开发"而无仓库/测试是红旗。

## 钱包集成

- **CCC 钱包连接器**：支持 JoyID、OKX、UniSat、imToken 等，新 dApp 不应自研钱包连接 UI（见 Cell Sandbox 判例）。
- **Neuron**：官方全节点桌面钱包（重，面向持币/DAO 用户）。
- **CKBull**：移动端钱包（注意：无浏览器插件形态——Cell Sandbox 判例中委员指出"sandbox 要是接 CKBull，怎么连接"）。

## 常见过时认知清单（预审纠错用）

1. "用 ckb-sdk-js / Lumos 从零开发" → 应推荐 CCC。
2. "需要用户跑全节点才能验证" → 应用公共 RPC / 测试网 / OffCKB。
3. "CKB 上可以部署 Solidity 合约" → Godwoken 已非主流路径，链上脚本用 Rust。
4. "转账需要部署合约" → 基本概念错误（DevLaunch Kit 判例）。
5. "AI 生成代码即交付" → 须有可运行验证；CCC Vibe-Coding Scaffold 判例：区分 AI/人工代码对开发者无明确价值。
