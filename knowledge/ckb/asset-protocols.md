# CKB 资产协议与二层网络

> 用途：判断提案是否误解协议能力边界、是否与现有协议重复。
> 来源：nervosnetwork/rfcs、fiber.world、github.com/nervosnetwork/fiber、CKB 生态公开报告（2026-04），蒸馏于 2026-08。

## sUDT / xUDT（同质化代币）

- **sUDT**（Simple UDT）：CKB 最基础的代币标准，type script 保证发行总量守恒；发行方拥有 owner lock。
- **xUDT**（eXtensible UDT）：sUDT 的扩展版，支持附加扩展脚本（如发行规则、元数据），现行新项目应优先考虑 xUDT。
- 每个 UDT Cell 同样占用 capacity（存储成本），批量空投/分红类提案须计算 capacity 开销——"零成本发币给一万个地址"是不可能的。

## Spore 协议与 DOB（链上数字对象）

- **Spore Protocol**：CKB 原生 NFT/数字对象协议，铸造 **DOB（Digital Object）**，内容完全上链（非外链 URL），销毁 DOB 可赎回占用的 CKB。
- 特点：内容不可篡改、完全链上、有内在价值兜底（占用的 CKB）。
- 现状提示（2026-07-21 Spore Metadata 拒绝判例）：Spore 元数据规范仍处于演进期，委员会认为"对现有规范进行格式化意义不大"——涉及 Spore 元数据标准化的提案需谨慎。
- 相关基础设施：Spore 铸造工具、DOB 市场（见 existing-projects.md 的 Omiga 等）。

## RGB++（比特币资产协议）

- 将比特币 UTXO 与 CKB Cell **同构绑定**：比特币链提供所有权封印，CKB 提供计算与数据可用性；用户无需运行 RGB 客户端（解决了原 RGB 协议的最大可用性问题）。
- 能力：在比特币安全模型上发行代币、NFT，跑 DeFi；资产可在 BTC 与 CKB 之间双向映射（leap）。
- 规模：截至 2025-07，66.2 万独立地址、400+ dApp（来源：CKB 生态公开报告，2026-04）。
- **评审要点**：RGB++ 类提案须说明资产封印在 BTC 侧的哪一环、是否依赖特定索引器；声称"RGB++ 上做高频交易"须注意 BTC 侧 10 分钟确认约束（这正是 Fiber 要解决的问题）。

## Fiber Network（支付通道网络，重点）

- CKB 上的支付通道网络（类比闪电网络）：双方在链上锁定资金开通道，通道内无限次即时、近零费交易，关通道时链上结算。
- **已于 2025 Q1 主网上线**（合约部署 + 节点稳定版）；2026-08 节点版本 v0.9.0，仍标注"会持续迭代，谨慎使用"（来源：github.com/nervosnetwork/fiber，2026-08）。
- 与闪电网络的差异：支持 CKB/RGB++/xUDT 多资产（含稳定币）、与闪电网络跨网互联（跨网原子交换）、Daric 协议使 watchtower 存储 O(1)、基于 PTLC（比 HTLC 更隐私）。
- **评审要点**：
  - Fiber 项目须说明是"通道内"还是"链上"交互，两者成本/确认模型完全不同。
  - Fiber 生态已有 Fiber Checkout（Spark 资助结项的页面版支付），同类支付提案须说明差异（Fiber Pay Studio 因此被拒，2026-07-21）。
  - 声称"基于 Fiber"但与 Fiber 无关是高危红旗（FiberTap 拒绝判例，2026-08-25）。
  - 流动性与节点在线要求是 Fiber 的现实约束，面向 C 端"无感支付"提案须说明如何解决收款在线问题。

## 稳定币现状

- **USDI（UTXO Global）正在退出**：2026 年中起 USDI 逐步关闭（委员会纪要多次提及"USDI 即将关闭，需尽快结项/换成其它稳定币"）。涉及稳定币的提案不应再默认 USDI。
- 评审涉及"稳定币支付"时，须追问具体币种与发行方现状。

## CoTA（聚合代币账户）

- 基于 SMT 的账户式资产管理方案（一个 Cell 管理多种代币），适合"一个地址持大量币种"的场景；热度低于 sUDT/xUDT + Spore 主线，提案引用时确认其维护状态。

## 跨链桥

- Force Bridge 已停运（2025-06 被攻击）；Rosen Bridge 为社区批准的继任方案（2026-01）。
- 托管桥类提案须重点审查信任假设；优先鼓励 RGB++/Fiber 原生互操作路径。
