# 技术评审手册（按提案类型的评审要点）

> 用途：AI 预审第三步"逻辑真实性与可行性分析"的直接依据。
> 每条要点均来自委员会历史判例或 CKB 技术事实，蒸馏于 2026-08。

## 通用红旗清单（任何类型都先过一遍）

1. **基本概念错误**：如"转账需部署合约"（DevLaunch Kit 判例）——直接怀疑真实性。
2. **无 GitHub 仓库或仓库与身份对不上**：Fiber Submarine 判例——首席开发者 GitHub 与提案中推特账号不匹配，冒名顶替风险，直接拒绝。
3. **与宣称技术栈无关**：FiberTap 判例——项目与 Fiber 毫无关系却挂名。
4. **重复造轮子**：先做 existing-projects.md 对照；切割 CCC 已有流程（CellKit Actions 判例）、自研 CCC 已有的钱包选择器（Cell Sandbox 判例）均属此类。
5. **生态已有同类且无突破**：CKB NFT Market（已有 Omiga）、Fiber Pay Studio（已有 Fiber Checkout）。
6. **范围过大**：超过 1-2 个月 MVP 的，建议缩减（TiKo、NNCBN 判例）；经济模型过大的引导 Community Fund DAO（Corven 判例）。
7. **AI 生成内容充数**：提案/周报由 AI 生成但无实质验证，中期评审不予通过（CKB Developer Onboarding 中期拒付判例）。

## 开发工具类（npm 包 / CLI / SDK / 脚手架）

- 验证标准：`npm install && npm test` 或 `docker compose up` 可复现；有 CI 更佳。
- 必查：与 CCC、OffCKB、ckb-cli、ckb-testtool 的功能重叠度；npm 包页面须指向项目自己的 GitHub 仓库（Fiber Checkout 中期整改判例）。
- 文档要求：图文教程以 md 形式放仓库（方便 AI 读取）——Fiber Checkout 整改先例。
- 常见缺陷：只包装 RPC 无增量价值（CellKit Actions）；受众不清（CKB-lint"技术上不成熟"判例）。

## 钱包 / 支付类

- 必答：与 JoyID、CKBull、Neuron、imToken、CCC 钱包连接器的差异；收款在线问题如何解决（Fiber 场景）。
- 验证：录屏必须展示钱包实际扣款与余额变化（Fiber Checkout 中期判例："视频未展示支付钱包和交易后金额变化，无法判断项目功能"）。
- 稳定币：不得默认 USDI（正在退出），须指明币种。
- 电商插件类：有合规上架风险先例（WooCommerce 拒绝判例：已有项目开发完成后无法在 Shopify 上架）。

## DeFi / DEX / Swap 类

- 先区分：链上 AMM（Cell Script AMM 判例："过于激进，距离 Spark 范围太远"）vs Fiber 通道内 swap（Fiber RGB++ Swap，可行但须证明交付能力）。
- 流动性假设是最大弱点：没有冷启动方案的 DEX 提案原则上不支持。
- "全流程做市/聚合器"通常超出 Spark 范围。

## 教育 / 文档 / 新手引导类（委员会重点管控类型）

- 硬性条件（委员会 2026-07-21 确立）：开发者必须**非常熟悉生态** + 内容包含新手切身体会 + 有资深社区成员质量把控。
- **事实性错误是死罪**：Cell Sandbox 关停判例——教育内容存在事实错误导致不能正确引导开发者，直接关停。
- 结构要求：无意义章节堆砌、结构不清 = 中期拒付（CKB Developer Onboarding 判例）。
- AI 批量生成内容不被认可（doctor 判例："AI 找比 AI 写更靠谱"）。
- 需说明目标受众与分发渠道，"写出来就会有人看"不成立。

## 社区 / 运营 / 游戏类

- 综合类（技术+社区）上限 $2,000，须同时交付技术产物与社区活动并详细论证。
- 验证难点要前置回答：CKB UGMP 判例——委员会追问"最终如何验证"（Demo 须完整走通：上传→铸造→展示全流程）。
- 数据上链成本谁承担必须说清楚（UGMP 判例："谁支付上链费用似乎没有想清楚"）。

## AI 结合类（新兴高频类型）

- 数据真实性：Wallet Behaviour 警告判例——"自己造机器人然后分析毫无意义"；AI 分析必须基于真实主网数据（如主网 gold 标注集）。
- 维护成本：AI 实时生成内容的长期成本谁覆盖（VibeQuest 追问判例）。
- 可继承性：即使项目终止，数据/维度/权重等产物能否被后续项目继承（Wallet Behaviour 转向要求）。
- 参考价值判断：区分"AI 提效工具"与"AI 噱头"（CCC Vibe-Coding Scaffold 拒绝判例）。

## 节点 / 基础设施类

- 硬件+运营混合预算：NNCBN 判例——Spark 不提供长期运营支持，最多支持硬件+首季运营（$1,080）。
- 公共物品属性强的可考虑，但须说明与官方节点、公共 RPC 的互补关系（Atlantik 社区全节点获通过）。

## 协议 / 底层研发类

- 超出 Spark 范围居多：QUIC（"不属于 Spark 范围"）、CKB Appchain Kit（核心问题未说明如何解决）。
- 高难度档（$5,000）需全体委员一致同意，论证门槛极高（CKB Probe $1,500 论证被驳，维持 $1,000）。
- 可引入外部专家：CKB-VM 团队曾受邀参与评审（CKB-Probe 判例）。

## 中期 / 结项评审要点

- 以**提案基线锁定版**为准逐项验收（Approved 时锁定提案内容）。
- 中期常见拒付理由：交付质量未达承诺标准、Demo 无法体验（UGMP 400 错误判例）、周报格式混乱（Builder Lab 整改判例）。
- 里程碑变更须委员会批准（Dular 转向判例：出入金正常前提下里程碑变动可接受）。
- 周报要求：减少纯技术细节堆砌，多用视频/截图让社区参与（CKB Probe 指导判例）。
