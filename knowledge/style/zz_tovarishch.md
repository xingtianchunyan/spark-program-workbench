# 委员会措辞 Skill — zz_tovarishch 版本 (Hongzhou)

> 基于 2026-08-09 至 2026-08-19 期间 zz_tovarishch 在 Nervos Talk 发布的 13 条正式委员会回复提炼。
> 与通用版 `committee_phrasing_skill.md` 并存：当回复以 zz_tovarishch 名义/风格发出时使用本版本。
> 核心差异：**更简洁凝练、决议前置、理由标签化、签名个人化（Hongzhou）、结尾固定 cc 三位委员**。

---

## 一、总体风格特征

1. **简洁凝练**：全篇通常 60–150 英文词，无废话，每个段落只承载一个信息点。
2. **决议前置**：第一句寒暄后立即给出委员会决议，不做铺垫。
3. **理由标签化**：拒绝/调整类回复用「**加粗标签：一句话说明**」列出 1–2 条核心考虑（如 "Scope & Program Fit: ..."），不展开长篇论证。
4. **语言跟随提案**：英文提案用英文回复，中文提案用中文回复（见 CKB-VM Sail 批准回帖）。
5. **固定收尾格式**：
   ```
   Best regards, / Best,
   Hongzhou
   On behalf of Spark committee
   cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan
   ```
   （或落款 "Spark Program Committee"；cc 行永远在最后，三位委员顺序可换）
6. **集体身份发言**：始终 "the committee" 作主语，个人仅出现在签名。
7. **语气**：礼貌但直接。该批评时直说（如 "The methodology contains a fatal weakness."），不绕弯，但必给出路。

---

## 二、通用骨架（所有类型共用）

```
Hi @申请人,
[一句感谢/承接] + [委员会决议一句话]
[理由或安排：标签化分点 或 1-2 句说明]
[下一步要求 / 鼓励 / 替代渠道]
Best regards, / Best,
Hongzhou（或 Spark Program Committee）
On behalf of Spark committee（可选）
cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan
```

### 高频开场
- "Hi @name, thank you for submitting the proposal. After evaluation, the committee has decided to ..."
- "Hi @name, Thank you for providing such a detailed progress report ..."
- "Hi @name, The committee reviewed your latest update. ..."
- "Hi @name, thank you for choosing Spark to start building your project."
- "Hi @name, thanks for your ongoing building within the community."

### 高频结尾（决议后的出路/鼓励）
- "We encourage you to submit future proposals."
- "We encourage you to keep building and exploring other opportunities within the community."
- "We still look forward to your continued contributions and engagement with the community."
- "Please share your updates on these items so we can resume the review process."
- "Please let us know if you accept this revised budget so we can proceed accordingly."

---

## 三、通过 (Approved)

### 结构
```
[感谢+批准决议] + [一句话认可项目价值] + [金额 USD→CKB 按现价换算] + [索取收款信息] + [邀请 Monthly Call]
```

### 措辞片段
- "the committee has reviewed your revised proposal and decided to approve [项目名]."
- "We recognize the project's potential to [价值一句话]."
- "Based on the agreed $[金额] budget and the current CKB price of $[现价], the grant amount is [换算] CKB."
- "Please reply to this thread with your CKB payout address, email address, and Discord handle to facilitate grant distribution and team communications."
- "We also invite you to participate in our upcoming Ecosystem Monthly Call to share progress."
- 中文版："委员会经过讨论，决定批准 [项目名] 立项。委员会认可本项目 [价值]。按照当前 CKB 价格 [现价] 美元换算，本期资助金额为 [换算] CKB。请在此回复提供你的 CKB 接收地址、联系邮箱以及 Discord 账号……"

### 完整示例（zk-Lock，2026-08-12）
```
Hi @Mulandi_Cecilia The committee has reviewed your revised proposal and decided to approve zk-Lock for CKB. We recognize the project's potential to deliver a reusable Groth16 lock script and developer toolchain, creating a standardized integration layer for zero-knowledge applications on CKB.
Based on the agreed $900 budget and the current CKB price of $0.00083, the grant amount is 1,084,338 CKB. Please reply to this thread with your CKB payout address, email address, and Discord handle to facilitate grant distribution and team communications. We also invite you to participate in our upcoming Ecosystem Monthly Call to share progress.
Best,
Hongzhou
On behalf of Spark committee
cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan
```

---

## 四、待定 / 澄清请求 (Pending)

### 结构
```
[感谢+移入 Pending 决议] + [2-3 个具体澄清问题（一段连续提出）] + [可选：一条附赠建议] + [邀请更新后恢复评审]
```

### 措辞片段
- "The committee has moved this application to Pending status and would like to request clarification on a few points before moving forward."
- "Please specify which ... Additionally, please clarify the exact format ..., specifically whether it will be ... or ..."
- "As an optional suggestion, you may also consider ..."
- "Please share your updates on these items so we can resume the review process."

### 完整示例（Spark Verify，2026-08-13）
```
Hi @Akane,
Thank you for submitting the proposal. The committee has moved this application to Pending status and would like to request clarification on a few points before moving forward.
Please specify which existing CKB project you intend to adapt as the example case in Week 5. Additionally, please clarify the exact format and presentation of the evidence report, specifically whether it will be a structured config file with line-by-line field explanations, a visual/dashboard presentation, or other content.
As an optional suggestion, you may also consider adopting a more general tool name such as ckb-verify rather than restricting it to Spark, which could encourage broader ecosystem adoption.
Please share your updates on these items so we can resume the review process.
Best regards,
Spark Program Committee
cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan
```

---

## 五、预算调整 / 缩减 (Budget Adjustment)

### 结构
```
[肯定努力] + [划出范围外部分] + [给出调整后金额（USD→CKB）] + [请申请人确认接受]
```

### 措辞片段
- "The committee commends your initiative and appreciates your effort to push [领域] forward."
- "However, after reviewing the proposal, we consider the [部分] out of scope for this grant."
- "As a result, we would like to adjust the approved budget to $[金额] USD equivalent in CKB for the remaining core development."
- "Please let us know if you accept this revised budget so we can proceed accordingly."

---

## 六、分期打款方案 (Staged Release)

### 结构
```
[肯定进展] + [说明风控原则] + [分期方案：阶段标签 + 百分比 + CKB 金额 + 触发条件] + [请确认]
```

### 措辞片段
- "our committee must adhere to standard risk-management and milestone-based allocation practices across all active Spark Program initiatives."
- "Immediate Disbursement: 20% ([金额] CKB) upon approval of this update to support current operational requirements."
- "Technical Completion: 20% ([金额] CKB) upon full delivery and code-level verification of all remaining core technical features."
- "Final Wrap-up & Closure: The remaining upon final project wrap-up, including comprehensive documentation, community onboarding material, and formal grant completion."
- "Please confirm if this structured payout schedule works for your team, and we will initiate the transfer accordingly."

---

## 七、拒绝 (Rejected)

### 结构
```
[感谢] + [decline 决议（at this stage / at its current scope）] + [1-2 条标签化核心考虑] + [鼓励/替代渠道]
```

### 措辞片段
**决议句：**
- "After reviewing the details, the committee has decided to decline funding for [项目名] at this stage."
- "After careful consideration, the committee regrets to inform you that we were unable to approve the project."
- "Regrettably, we won't be able to approve your application."

**理由标签（按场景选用，标签+一句话）：**
- "Resource Model Feasibility: We have reservations regarding the cost sustainability and server overhead of ..."
- "Scope & Program Fit: Building a full-fledged [X] is a heavy infrastructure initiative that exceeds the lightweight scope and budget bounds of the Spark Program."
- "Unsuitable Application Scenarios: The proposed ... does not align well with practical ecosystem priorities or current user adoption patterns."
- "Architectural Mismatch: The underlying architecture of ... does not fit cleanly with ..."
- "Practical Ecosystem Fit: The proposed ... does not align well with current ecosystem needs."
- "Execution Focus: With your concurrent proposal for [另一项目], the committee believes it is best to concentrate development resources on executing that single project successfully before taking on additional initiatives."

**替代渠道（大项目专用）：**
- "Given the scale and resource demands of this concept, we encourage you to refine the architecture and apply through the CKB Community Fund DAO, which is better suited to support larger infrastructure endeavors."

**鼓励：**
- "We appreciate your effort in exploring new use cases on Fiber, and we encourage you to submit future proposals."
- "We appreciate your dedication to the CKB ecosystem."

### 完整示例（Corven，2026-08-13）
```
Hi @lestonEth,
Thank you for submitting the proposal. After evaluation, the committee has decided to decline funding for this application at its current scope.
Our primary considerations include:

Resource Model Feasibility: We have reservations regarding the cost sustainability and server overhead of provisioning isolated cloud containers for every individual user.
Scope & Program Fit: Building a full-fledged cloud IDE platform is a heavy infrastructure initiative that exceeds the lightweight scope and budget bounds of the Spark Program.

Given the scale and resource demands of this concept, we encourage you to refine the architecture and apply through the CKB Community Fund DAO, which is better suited to support larger infrastructure endeavors.
Best regards,
Spark Program Committee
cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan
```

---

## 八、关闭 / 终止 (Closed / Terminated)

### 结构
```
[承接开场] + [终止决议] + [编号列出 1-2 条具体原因] + [程序性说明（关闭帖子）] + [鼓励继续参与]
```

### 措辞片段
- "However, after careful review and consideration, the committee has decided to discontinue the project. The reasons are as follows:"
- "The committee feels that the improvements did not meet expectations and did not sufficiently benefit target users."
- "Therefore, we regretfully have to terminate the project. We do, of course, encourage you to continue refining your application and to keep engaging with the community."
- 终止后追款/原则说明（针对申请人的继续申诉）："Funding is provided to support viable milestones rather than compensating for accumulated development hours." / "We will not be approving any further funding requests for work retroactively or following a project termination, as doing so contradicts the fund's operation framework."
- 程序性语句："As part of our standard procedure, this thread will now be closed."

---

## 九、警告 / 最终机会 (Final Warning)

### 结构
```
[承接] + [直指核心缺陷（可用 fatal weakness 等重话）] + [说明正确方向] + [给最后一次机会 + 明确不满足则关闭]
```

### 措辞片段
- "The committee reviewed your latest update. The methodology contains a fatal weakness."
- "Valuable machine learning must focus on ... The current data extraction limits prevent ..."
- "We provide one final opportunity to pivot the project direction."
- "If the project cannot deliver [要求], the committee will close this grant application."

---

## 十、使用说明

1. **身份**：以委员会集体身份发言（the committee），签名 "Hongzhou / On behalf of Spark committee" 或 "Spark Program Committee"。
2. **结尾必带 cc 行**：`cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan`。
3. **语言跟随提案**：中文提案用中文回复，英文提案用英文回复。
4. **篇幅控制**：能一段说完不分两段；拒绝类全篇 ≤120 词，通过类 ≤100 词（不含金额细节）。
5. **决议永远在第一段**：先结论后理由，理由用标签化短句。
6. **通过类四要素不可缺**：价值认可 / USD→CKB 换算（含现价）/ 索取地址+邮箱+Discord / Monthly Call 邀请。
7. **拒绝必给出路**：鼓励重提、聚焦另一项目、或引导至 Community Fund DAO。
8. 与通用版 `committee_phrasing_skill.md` 冲突时，以本版本的风格（简洁度、签名、cc 格式）为准。

---

*基于 2026-08-09 ~ 2026-08-19 的 13 条 zz_tovarishch 正式回复生成（含通过 2、待定 1、预算调整 1、分期打款 1、拒绝 5、终止 2、警告 1）。*
*原始样本存档：zz_formal_replies.txt / zz_recent_replies.json*
*最后更新：2026-08-19*
