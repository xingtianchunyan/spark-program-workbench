import json, os, sys, time, atexit, datetime, webbrowser, urllib.request, urllib.error, json as jsonlib, tkinter as tk, threading, re, socket
from tkinter import ttk, messagebox, simpledialog, scrolledtext

# ============================================================
# Spark Program 联络员桌面精灵
# 作者：Kimi Work (for 行天春彦 / xingtianchunyan)
# 功能：无边框悬浮窗、透明置顶、贴边隐藏、任务/看板/模板/链接
# 更新：Windows互斥锁防多开、Nervos Talk自动同步项目数据
# ============================================================

# 程序根目录：打包为 exe 时用 exe 所在目录，源码运行时用脚本所在目录
_BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(_BASE_DIR, "spark_task_data.json")
PROJECT_CACHE_FILE = os.path.join(_BASE_DIR, "spark_project_cache.json")
ENV_FILE = os.path.join(_BASE_DIR, ".env")
_DATA_LOCK = threading.RLock()

_SECRET_ENV_KEYS = {
    "kimi": "SPARK_KIMI_API_KEY",
    "siliconflow": "SPARK_AI_API_KEY",
}


def _parse_env_file(path=ENV_FILE):
    """读取简单 KEY=VALUE 文件；不执行 shell，也不展开变量。"""
    values = {}
    if not os.path.exists(path):
        return values
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                values[key] = value.replace(r"\n", "\n")
    except OSError:
        return {}
    return values


def _write_env_values(values, path=ENV_FILE):
    """原子写入本地 .env，并在支持的平台上限制为当前用户可读写。"""
    tmp = path + ".tmp"
    lines = ["# Spark Program Workbench local secrets. Do not share or commit this file.\n"]
    for key in sorted(values):
        value = str(values[key]).replace("\\", "\\\\").replace('"', '\\"').replace("\n", r"\n")
        lines.append(f'{key}="{value}"\n')
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def get_ai_api_key(provider="siliconflow"):
    env_key = _SECRET_ENV_KEYS.get(provider, _SECRET_ENV_KEYS["siliconflow"])
    return os.environ.get(env_key, _parse_env_file().get(env_key, "")).strip()


def set_ai_api_key(value, provider="siliconflow"):
    env_key = _SECRET_ENV_KEYS.get(provider, _SECRET_ENV_KEYS["siliconflow"])
    values = _parse_env_file()
    value = (value or "").strip()
    if value:
        values[env_key] = value
        os.environ[env_key] = value
    else:
        values.pop(env_key, None)
        os.environ.pop(env_key, None)
    _write_env_values(values)


def get_webbridge_token():
    return os.environ.get("SPARK_WEBBRIDGE_TOKEN", _parse_env_file().get("SPARK_WEBBRIDGE_TOKEN", "")).strip()


def webbridge_enabled():
    value = os.environ.get("SPARK_ENABLE_WEBBRIDGE", _parse_env_file().get("SPARK_ENABLE_WEBBRIDGE", "0"))
    return str(value).strip().lower() in ("1", "true", "yes", "on")

# 主题配色 — 深色玻璃质感
THEME = {
	"bg": "#1e1e2e",
    "card": "#252535",
    "card_hover": "#313244",
    "accent": "#cba6f7",
    "accent2": "#89b4fa",
    "text": "#cdd6f4",
    "text_dim": "#a6adc8",
    "border": "#45475a",
    "success": "#a6e3a1",
    "warning": "#f9e2af",
    "danger": "#f38ba8",
    "titlebar": "#1a1a2e",
}

# 7个Nervos Talk标签
TALK_TAGS = ["Spark-Program", "Submitted", "Pending", "In-Progress", "Rejection", "Completion", "Closure"]

# 状态标签集合（用于看板分类优先级判断：状态标签 > 官方用户 > Spark-Program 预审）
STATUS_TAGS = {"Submitted", "Pending", "In-Progress", "Rejection", "Completion", "Closure"}

# 提案模板参考URL（用于AI分类）
PROPOSAL_TEMPLATE_URL = "https://talk.nervos.org/t/spark-program-mini-grant-initiative/8752/6#h-1"

# 从知识库提取的项目数据
ONGOING_PROJECTS = [
    {
        "name": "Nervos Brain",
        "team": "Yunhe Su (苏云禾)",
        "start": "2026-03-16",
        "expected": "2026-05-18",
        "grant": "$2,000 USD (100% USDI)",
        "disbursed": "70%",
        "status": "进行中",
        "talk_url": "https://talk.nervos.org/t/spark-program-nervos-brain/9995",
        "wallet": "ckb1qrg6n7rh4mfltcruh8zjkcdtjmgx7fg2u6yre3ls5fprmvyhdlyzzqvslewz3qys2vssznx77m2uwa5v3vxvqdsedwrm8"
    },
    {
        "name": "CKB UGMP",
        "team": "HNO3Miracle",
        "start": "2026-04-19",
        "expected": "2026-06-07",
        "grant": "$700 USD (100% USDI)",
        "disbursed": "20%",
        "status": "进行中",
        "talk_url": "https://talk.nervos.org/t/spark-program-ckb-ugmp/10098",
        "wallet": "ckb1qrg6n7rh4mfltcruh8zjkcdtjmgx7fg2u6yre3ls5fprmvyhdlyzzqtcn0lylg3s4jgy4ec5wwu29mpr0xe0uvsjcn5yu"
    },
    {
        "name": "Dular",
        "team": "duongja",
        "start": "2026-05-21",
        "expected": "2026-07-02",
        "grant": "$2,000 USD (100% CKB, 1,409,000 CKB)",
        "disbursed": "20%",
        "status": "进行中",
        "talk_url": "https://talk.nervos.org/t/spark-program-dular/10212",
        "wallet": "ckb1qrg6n7rh4mfltcruh8zjkcdtjmgx7fg2u6yre3ls5fprmvyhdlyzzqgmrw6q4f2tahs6qv588764w9dhuts09gqry34sp"
    }
]

SUBMITTED_PROJECTS = [
    {"name": "CKB-Sweep", "author": "Bafarfi", "url": "https://talk.nervos.org/t/spark-program-ckb-sweep/10148", "status": "已提交"},
    {"name": "CKB-VM Sail Formal Verification", "author": "TinyuengKwan", "url": "https://talk.nervos.org/t/spark-program-ckb-vm-sail-formal-verification/10214", "status": "已提交"},
    {"name": "Cell Sandbox", "author": "zynor", "url": "https://talk.nervos.org/t/spark-program-cell-sandbox/10326", "status": "已提交"}
]

QUICK_LINKS = {
    "Notion Dashboard": "https://app.notion.com/p/CKB-Spark-Program-Dashboard-2a0639bc0d1b833ca3ca010fd134795b",
    "Notion 项目列表": "https://app.notion.com/p/e95639bc0d1b83f5952d811d03c925b3",
    "Notion 资金池": "https://app.notion.com/p/146639bc0d1b83c18f1181a8fa1c9261",
    "Notion 归档": "https://app.notion.com/p/913639bc0d1b82b991b401163fb8d007",
    "Spark 联系人列表": "https://www.notion.so/Spark-Program-Mini-Grants-1da24205dae080978340d534c2f4a94d?source=copy_link",
    "Nervos Talk (Spark 子区)": "https://talk.nervos.org/c/daos-funding/spark-program/77",
    "Discord Spark 频道": "https://discord.com/channels/657799690070523914/1364236242190995558"
}

# 项目进入 In-Progress 时自动创建的四项任务。前两项已进入自动写入阶段，
# Dashboard 与联系人列表保留为后续自动化阶段，不能因为尚未实现而停止建任务。
INPROGRESS_NOTION_TASKS = [
    ("更新dashboard", "Notion Dashboard"),
    ("更新项目列表", "Notion 项目列表"),
    ("更新资金池状态", "Notion 资金池"),
    ("更新Spark联系人列表", "Spark 联系人列表"),
]

NOTION_REQUIRED_STEPS = {
    "更新项目列表": "project_list",
    "更新资金池状态": "fund_pool",
}

NOTION_ALL_STEPS = {
    "更新dashboard": "dashboard",
    "更新项目列表": "project_list",
    "更新资金池状态": "fund_pool",
    "更新Spark联系人列表": "contacts",
}

TEMPLATES = {
    "预审反馈": {
        "desc": "联络员预审：比对官方模板格式、主帖资金标准、内容逻辑，提供优化意见",
        "template": """Hi @[申请人], thanks for submitting the [项目名称] proposal.
Before we move to committee review, I'd like to share some personal pre-review feedback (as the program liaison, not a committee decision) to help strengthen your proposal:

**1. Format & Completeness** (checked against the official proposal template):
- [缺失或不符合格式要求的部分，逐条列出并注明属于模板哪一节]

**2. Funding Standards**:
- [申请金额与主帖资助标准的比对结果；如超标请指出并给出建议档位与理由]

**3. Content Logic**:
- [提案内容的真实性与可行性分析：问题是否真实、方案能否解决问题、计划是否合理]

**Suggestions**:
1. [具体优化建议 1]
2. [具体优化建议 2]

Please revise and reply in this thread. Looking forward to the updated version!"""
    },
    "Pending (打回待完善)": {
        "desc": "提案有潜力但需修改，给出具体修改方向",
        "template": """Hi @[申请人],
Thank you for submitting the [项目名称] proposal.
After thorough discussion, the committee has decided to place your proposal in **Pending** status.
This is not a rejection, but an invitation to revise.

Main Revision Recommendations:
1. [修改方向 1]: [具体问题 + 建议]
2. [修改方向 2]: [具体问题 + 建议]
3. [修改方向 3]: ...

Please revise and resubmit based on the feedback above.
We will arrange a new round of review as soon as possible."""
    },
    "Approved (通过通知)": {
        "desc": "提案通过评审，给出资助金额和后续流程",
        "template": """Hi @[申请人],
I'm glad to share that the Spark Program Committee has approved your [项目名称] proposal for a grant of [金额] USD.

The committee recognizes [项目价值简述].

Here are the next steps:
1. **Funding & Wallet Address**
   - Total grant: [金额]
   - First installment (20%, [金额]): will be disbursed shortly
   - Please provide a CKB wallet address
   - Remaining 80% follows a flexible model
2. **Weekly Sync**
   - Text-based updates on this thread at a fixed time each week, with committee feedback.
   - Or a brief live video call.
   - Please let us know your preference and a time that works for you.
3. **Proposal Content Lock**
   - We'll lock the current version of your proposal post as the reference baseline for deliverable verification.

Congratulations and looking forward to the collaboration!"""
    },
    "Rejection (拒绝通知)": {
        "desc": "经过多轮评审后决定拒绝，需说明原因",
        "template": """Hi @[申请人],
Thank you for taking the time to revise the proposal based on our earlier feedback.
The committee has completed its review and, after careful discussion, regrets to reject the [项目名称] at this time.

We want to be transparent about the reasoning behind this decision:

1. [原因 1]: [具体阐述]
2. [原因 2]: [具体阐述]
3. [原因 3]: [具体阐述]

We appreciate the work your team has put into [项目].
If in the future you plan to [新方向], we would welcome a new application.

Keep building, and feel free to reach out anytime!"""
    },
    "打款通知": {
        "desc": "首笔或后续资金发放通知",
        "template": """Hi @[申请人],
All noted. [同步机制] works well, and [首次更新时间] is fine.

The [第 N 笔] installment ([金额], [百分比]) has been disbursed:
[交易链接]

Please confirm once received. Looking forward to the [首次/下次] progress update on [日期]."""
    },
    "周报互动-鼓励": {
        "desc": "申请人提交周报后，委员会进行反馈",
        "template": """Hi @[申请人],
[对周报的肯定，如"连续两周准时交付周报，节奏很稳，继续保持"]

[针对周报内容的提问或提醒，通常是进入下一阶段前的对齐]

If you need help on [某方面], feel free to reach out. The committee is happy to support."""
    },
    "周报互动-轻柔提醒": {
        "desc": "长时间未更新进展，第一次温和询问（关心+邀请更新）",
        "template": """Hi @[申请人],

We have noticed that it has been some time since the last progress update, so we are reaching out to check on the latest developments.

First, I hope you and your team are doing well. Please take good care of your health and maintain a balance between work and rest.

From the project’s perspective, we are eager to understand the current progress—whether it is advancing smoothly, encountering any obstacles, or what the overall status is. Even a brief update can help the committee and the community stay informed and provide support when needed.

Please share the latest status at your earliest convenience. If there are any difficulties affecting progress, please do not hesitate to let us know—the committee is willing to provide every assistance.

We look forward to your reply.

Best,
xingtian"""
    },
    "周报互动-压力提醒": {
        "desc": "轻柔提醒未获回应后，施压要求尽快回复（明示沉默后果）",
        "template": """Hi @[申请人],

We previously reached out to check on the project's status and your team's well-being, but we have not yet received a response.

We genuinely hope everything is okay on your end. That said, the committee has a responsibility to ensure that grant resources are being actively utilized and that projects remain accountable to the community.

Please provide a progress update at your earliest convenience. If the project has encountered blockers or if the timeline needs adjustment, we are open to discussing it — but we do need to hear from you.

Continued silence will lead the committee to determine that the project is no longer active and terminate its funding.

Please contact us as soon as possible. Rather than lose a promising project, we would prefer to work with you to overcome these challenges.

Best,
xingtian"""
    },
    "周报互动-最后通牒": {
        "desc": "正式通知：限期回复，否则依据结项政策 3.3 终止项目并停止拨款",
        "template": """Hi @[申请人],
This is an official notice from the Spark Program Committee.

Despite the Committee’s repeated attempts to make contact and request progress updates, no formal response or update regarding the project’s current status has been received to date.

According to the Completion Policy in Section 3.3 of the “Spark Program: Mini-Grant Initiative” (https://talk.nervos.org/t/spark-program-mini-grant-initiative/8752#h-33-12), the Committee will terminate the project and stop remaining fund distributions under the following situations:

- Continuous lack of substantial progress for an extended period (**2 weeks**) without reasonable cause
- Project direction completely deviates from the original application goals / fund usage severely deviates from the project plan without Committee approval
- Discovery of material falsification or other integrity issues

Therefore, the Committee hereby provides notice that if no formal update or response is received within [X days / by YYYY-MM-DD], this project will be formally terminated, and its funding will be discontinued accordingly.

If you are facing difficulties or wish to discuss adjusting the direction, please reply immediately. The committee is willing to maintain a dialogue, but this must be based on proactive communication.

We look forward to your prompt response.

Best,
xingtian"""
    },
    "里程碑评审": {
        "desc": "项目进入关键阶段，提前对齐",
        "template": """Hi @[申请人],
[对前期进展的肯定]
As you move into the [下一阶段] phase, the committee would like to align with you on [关键问题]:

[具体问题 1]
[具体问题 2]
[具体问题 3]

If you need help on [某方面], feel free to reach out. The committee is happy to support."""
    },
    "提醒结项": {
        "desc": "项目接近结项日期，提醒准备结项材料",
        "template": """Hi @[申请人],
We noticed that [项目名称] is approaching the planned completion date ([日期]).
Please prepare the following for project closure review:

1. Final deliverables verification (against the locked proposal baseline)
2. Completion report (see template below)
3. Demo / evidence links (GitHub, live URL, screenshots, transaction hashes, etc.)
4. Any deviations or adjustments from the original plan, with explanations

Please submit the completion report at least [提前天数] before the planned closure date."""
    },
    "结项报告模板": {
        "desc": "项目正式结项时发布的报告格式",
        "template": """# Spark Program | [项目名称] Completion Report

## 1. Project Overview
[项目概述，包括目标、团队、周期]

## 2. Deliverables Checklist
[对照原始提案的交付物清单，逐项确认完成状态]
- [交付物 1]: ✅ 完成 / ⚠️ 部分完成 / ❌ 未完成
- [交付物 2]: ...

## 3. Verification & Testing
[验收方式、测试结果、外部验证]

## 4. Issues & Solutions
[开发过程中遇到的问题及解决方案]

## 5. Budget Utilization
[资金使用情况，实际支出 vs. 预算]

## 6. Next Steps / Future Plans
[后续计划、扩展方向]

## 7. Committee Assessment
[委员会评审意见：是否达到预期、是否建议后续资助]

## 8. Spark Committee Insights (顶格/首次类型项目必填)
[项目对生态的实际贡献评估、评审经验沉淀、后续建议、决策复盘]"""
    },
    "关停通知": {
        "desc": "终止项目资助的正式通知",
        "template": """# 星火计划：终止对 [项目名称] 项目的资助

## 项目基本信息
- 项目名称：
- 开发者/团队：
- 项目简介：
- 相关链接：

## 终止决定及原因
经过委员会慎重评估，我们遗憾地宣布，对 [项目名称] 项目的资助已正式终止。
申请人在项目执行期间提交了中期资助申请，声称已完成 [阶段] 的工作内容。委员会依据提案基线，对申请人自述已完成的交付物进行了审核，发现即便仅围绕其声称完成的内容评估，交付质量仍未达到提案承诺的基本标准——[具体问题描述]。

## 具体情况梳理
### 1. 中期资助审核阶段（日期 委员会反馈）
申请人提交中期资助申请后，委员会对已提交内容进行了审核，发现 [N] 个主要问题：
- [问题1]
- [问题2]
- [问题3]

### 2. 整改后复审阶段（日期 委员会复审）
- [整改项1]：是否达到要求 → [评估结果]
- [整改项2]：是否达到要求 → [评估结果]

结论：经整改后，交付质量仍未能达到 [标准]。

## 后续安排
- **资金处理**：已发放的 [金额] 不再追回；尚未发放的 [金额] 停止拨付。
- **项目资产**：代码仓库归开发者所有，委员会不保留任何资产要求。
- **重新申请**：欢迎开发者在充分准备后，以更高质量的提案重新申请。

## 结语
委员会感谢 [开发者] 在项目中投入的时间和努力。终止资助是一个经过慎重考虑的困难决定，我们的目标始终是确保每一笔社区资金都能产生应有的价值。"""
    },

    # ---------------- zz_tovarishch 版本（简洁凝练风格） ----------------

    "[zz] 通过通知": {
        "desc": "批准提案，含 USD→CKB 换算与收款信息索取（zz 简洁版）",
        "template": """The Spark committee has reviewed this application and decided to approve funding.

[一句话肯定提案价值，例如：This project addresses a real need in the CKB ecosystem and the committee considers it worth supporting.]

Based on the agreed $[金额] budget and the current CKB price of $[CKB现价], the grant amount is [换算结果] CKB.

To proceed with the disbursement, please provide:
- Your CKB address
- Your email address
- Your Discord handle

You are also welcome to join the CKB Ecosystem Monthly Call to share your project progress with the community.

Best,
Hongzhou
On behalf of Spark committee

cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan"""
    },

    "[zz] 待定与澄清": {
        "desc": "移入 Pending 并提出具体澄清问题（zz 简洁版）",
        "template": """The Spark committee has reviewed this application and moved it to Pending status. Before we can proceed, we need clarification on the following items:

1. [澄清问题1]
2. [澄清问题2]
3. [澄清问题3]

As an optional suggestion: [可选的改进建议，没有则删除此行].

Please share your updates on these items so we can resume the review process.

Best,
Hongzhou
On behalf of Spark committee

cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan"""
    },

    "[zz] 预算调整": {
        "desc": "划出范围外部分并缩减预算（zz 简洁版）",
        "template": """The Spark committee has reviewed this application. We consider the [范围外部分，例如：marketing / non-technical portion] out of scope for Spark funding, and would adjust the approved budget to $[调整后金额] USD equivalent in CKB.

[可选：一句说明调整理由]

Please let us know if you accept this revised budget, so we can move forward.

Best,
Hongzhou
On behalf of Spark committee

cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan"""
    },

    "[zz] 分期打款方案": {
        "desc": "按里程碑分期释放资金（zz 简洁版）",
        "template": """The Spark committee has reviewed this application and approved funding, with a milestone-based payout schedule to manage risk:

- Immediate Disbursement (20%): [金额] CKB
- Technical Completion (20%): [金额] CKB, released upon [技术验收条件]
- Final Wrap-up & Closure (remaining): [金额] CKB, released upon [结项验收条件]

Please confirm if this structured payout schedule works for you, and provide your CKB address, email, and Discord handle.

Best,
Hongzhou
On behalf of Spark committee

cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan"""
    },

    "[zz] 拒绝通知": {
        "desc": "拒绝申请，标签化理由+出路指引（zz 简洁版）",
        "template": """The Spark committee has reviewed this application and decided to decline funding at its current scope. The reasons are as follows:

- [标签1]: [一句话说明]
- [标签2]: [一句话说明]

[出路，按情况选择其一或组合：
- You are welcome to revise the proposal and reapply once these issues are addressed.
- We suggest focusing on [另一方向] instead.
- Given the scale of the economic model, we recommend applying to the Community Fund DAO instead of Spark's micro-grant.]

Best,
Hongzhou
On behalf of Spark committee

cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan"""
    },

    "[zz] 关停通知": {
        "desc": "终止项目资助（zz 简洁版）",
        "template": """The Spark committee has reviewed the project status and decided to discontinue the project. The reasons are as follows:

1. [原因1]
2. [原因2]

As part of our standard procedure, this thread will now be closed.

[一句鼓励，例如：We appreciate the effort put into this project and welcome a stronger proposal in the future.]

Best,
Hongzhou
On behalf of Spark committee

cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan"""
    },

    "[zz] 警告与最终机会": {
        "desc": "指出致命缺陷并给最后一次转向机会（zz 简洁版）",
        "template": """The Spark committee has reviewed the latest progress and found that the current direction contains a fatal weakness: [致命缺陷一句话，例如：the analysis is based on self-generated bot data, which makes the results meaningless].

The correct direction should be: [正确方向 1-2 句，例如：build the gold-labeled dataset on mainnet data so that later projects can inherit the behavioral dimensions and weights of the analysis].

We provide one final opportunity to pivot. If the project cannot deliver [具体要求], the committee will close this grant application.

Best,
Hongzhou
On behalf of Spark committee

cc @Hanssen @yixiu.ckbfans.bit @xingtianchunyan"""
    }
}


# ============================================================
# 数据管理
# ============================================================

def _atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _migrate_legacy_credentials(data):
    """把旧版 JSON 凭据迁入 .env，并彻底删除未使用的论坛密码。"""
    changed = False
    migrations = (
        ("kimi_api_key", "kimi"),
        ("ai_api_key", data.get("ai_provider", "siliconflow")),
    )
    for field, provider in migrations:
        legacy_value = str(data.get(field, "") or "").strip()
        if not legacy_value:
            if field in data:
                data.pop(field, None)
                changed = True
            continue
        if not get_ai_api_key(provider):
            try:
                set_ai_api_key(legacy_value, provider)
            except OSError:
                # 迁移失败时保留旧值，避免凭据意外丢失。
                continue
        data.pop(field, None)
        changed = True

    # 密码从未参与登录流程，不再保存用户名或密码。
    for field in ("nervos_username", "nervos_password"):
        if field in data:
            data.pop(field, None)
            changed = True
    return changed


def _migrate_notion_sync_state(data):
    """v2: queued 表示已建本地任务；created 仅表示 Notion 已写入并核验成功。"""
    if int(data.get("notion_sync_state_version", 0) or 0) >= 2:
        return False
    previously_queued = [str(x) for x in data.get("notion_tasks_created", []) if str(x)]
    data["notion_tasks_queued"] = sorted(set(previously_queued))
    data["notion_tasks_created"] = []
    jobs = data.setdefault("notion_sync_jobs", {})
    title_to_post = {}
    try:
        with open(PROJECT_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        for post in cache.get("classified", {}).get("in_progress", []):
            title = str(post.get("title", "")).strip()
            pid = str(post.get("id", "")).strip()
            if title and pid:
                title_to_post[title] = post
    except (OSError, ValueError, TypeError):
        pass
    for pid in previously_queued:
        post = next((p for p in title_to_post.values() if str(p.get("id", "")) == pid), {})
        jobs[pid] = {
            "idempotency_key": f"nervos:{pid}:in-progress:v1",
            "project": post.get("title", ""),
            "topic_url": post.get("url", ""),
            "status": "queued",
            "created_at": "",
            "steps": {
                "project_list": {"status": "pending", "notion_page_ids": []},
                "fund_pool": {"status": "pending", "notion_page_ids": []},
            },
        }
    for task in data.get("tasks", []):
        step = NOTION_REQUIRED_STEPS.get(task.get("title"))
        post = title_to_post.get(str(task.get("project", "")).strip())
        if step and post:
            task["nervos_topic_id"] = str(post.get("id"))
            task["notion_step"] = step
    data["notion_sync_state_version"] = 2
    return True


def _migrate_notion_future_tasks(data):
    """v3: 保留 Dashboard/联系人任务，并显式标记为 planned 自动化步骤。"""
    if int(data.get("notion_sync_state_version", 0) or 0) >= 3:
        return False
    title_to_post = {}
    try:
        with open(PROJECT_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        for post in cache.get("classified", {}).get("in_progress", []):
            title = str(post.get("title", "")).strip()
            if title and str(post.get("id", "")).strip():
                title_to_post[title] = post
    except (OSError, ValueError, TypeError):
        pass
    for pid, job in data.setdefault("notion_sync_jobs", {}).items():
        steps = job.setdefault("steps", {})
        steps.setdefault("dashboard", {"status": "planned", "notion_page_ids": []})
        steps.setdefault("contacts", {"status": "planned", "notion_page_ids": []})
    for task in data.get("tasks", []):
        automation_step = NOTION_ALL_STEPS.get(task.get("title"))
        post = title_to_post.get(str(task.get("project", "")).strip())
        if automation_step:
            task["automation_step"] = automation_step
        if automation_step and post and not task.get("nervos_topic_id"):
            task["nervos_topic_id"] = str(post.get("id"))
    data["notion_sync_state_version"] = 3
    return True


def _migrate_notion_planned_status(data):
    """v4: 历史 Dashboard/联系人任务补充 planned 状态，不改变完成状态。"""
    if int(data.get("notion_sync_state_version", 0) or 0) >= 4:
        return False
    for task in data.get("tasks", []):
        if task.get("automation_step") in {"dashboard", "contacts"}:
            task.setdefault("automation_status", "planned")
    data["notion_sync_state_version"] = 4
    return True


def _migrate_notion_workspace_links(data):
    """v5: 将已有 Notion 任务链接切换到当前可写的复制版 Dashboard。"""
    if int(data.get("notion_sync_state_version", 0) or 0) >= 5:
        return False
    step_to_link_key = {
        "dashboard": "Notion Dashboard",
        "project_list": "Notion 项目列表",
        "fund_pool": "Notion 资金池",
        "contacts": "Spark 联系人列表",
    }
    changed = False
    for task in data.get("tasks", []):
        link_key = step_to_link_key.get(task.get("automation_step"))
        if link_key and task.get("link") != QUICK_LINKS.get(link_key, ""):
            task["link"] = QUICK_LINKS.get(link_key, "")
            changed = True
    data["notion_sync_state_version"] = 5
    return True

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            credentials_changed = _migrate_legacy_credentials(data)
            notion_state_changed = _migrate_notion_sync_state(data)
            notion_future_changed = _migrate_notion_future_tasks(data)
            notion_planned_changed = _migrate_notion_planned_status(data)
            notion_links_changed = _migrate_notion_workspace_links(data)
            if credentials_changed or notion_state_changed or notion_future_changed or notion_planned_changed or notion_links_changed:
                with _DATA_LOCK:
                    _atomic_write_json(DATA_FILE, data)
            return data
        except Exception:
            pass
    return {"tasks": [], "meetings": [], "characters": [], "templates": {}}


# 默认人物数据（基于Nervos Talk帖子语言特点蒸馏）
DEFAULT_CHARACTERS = [
    {
        "id": "zz_tovarishch",
        "name": "zz_tovarishch",
        "display_name": "💰 zz_tovarishch（版主/资金管理员）",
        "description": "版主风格，极度简洁、事实性、流程导向。专注于拨款确认和资金管理。",
        "enabled": True,
        "content": """## 人物：zz_tovarishch（版主/资金管理员）

### 语言特点
- 极度简洁、事实性、流程导向，每帖都是标准格式。
- 中英夹杂，公式化表达。
- 语气正式、中性、事务性，不带感情色彩。

### 常用句式
- "M2 付款 $XX*X% @0.00XX = X,XXX,XXX CKB"
- "注意：委员会暂时使用另一个单签钱包支付了..."
- "已经将问题转给维护团队"
- "Hi XXX, 欢迎来到Nervos Talk..."

### 立场
- 规则执行者、资金管理严谨、重视透明度和可追溯性。
- 回复简短直接，不展开过多解释。"""
    },
    {
        "id": "xingtianchunyan",
        "name": "xingtianchunyan",
        "display_name": "📋 xingtianchunyan（预审联络员）",
        "description": "联络员风格，详细、结构化、礼貌、鼓励性。预审反馈分点列出，注重引导。",
        "enabled": True,
        "content": """## 人物：xingtianchunyan（预审联络员）

### 语言特点
- 详细、结构化、礼貌、鼓励性。预审反馈分点列出，每条都有"建议"。
- 中英文双语自然切换，正式中带有人情味。
- 使用专业术语但解释清晰。

### 常用句式
- "你好 @xxx，感谢你对 Spark Program 的关注！"
- "以下是我的一些个人看法，不代表委员会立场。"
- "建议：... / 期待你的更新版本。"
- "如果你愿意继续推进的话..."
- "Best，xingtianchunyan"

### 立场
- 桥梁角色，帮助申请人完善提案，注重提案质量而非简单否定。
- 即使拒绝也会先肯定价值，语气温暖但坚定。"""
    },
    {
        "id": "Hanssen",
        "name": "Hanssen",
        "display_name": "🔧 Hanssen（技术评审员）",
        "description": "技术评审风格，技术深度、质疑式、中英双语。从架构/设计角度提问。",
        "enabled": True,
        "content": """## 人物：Hanssen（技术评审员）

### 语言特点
- 技术深度、质疑式、中英双语自然切换。
- 从架构/设计角度提问，逻辑严密。
- 理性、建设性、技术导向。

### 常用句式
- "Hi @xxx！我是 Spark Committee 的 Hanssen。"
- "我想与你确认几个问题："
- "我的主要观点在于："
- "从技术角度，我认为..."
- "感谢你的辛勤付出！/ Thanks for your reply and I trust that things will improve."

### 立场
- 技术守门人，关注可行性、安全性和设计合理性。
- 不怕问尖锐问题，但态度友善，建设性质疑。
- 不通过技术不过关的提案。"""
    },
    {
        "id": "yixiu.ckbfans.bit",
        "name": "yixiu.ckbfans.bit",
        "display_name": "📚 yixiu.ckbfans.bit（社区建设者/文档工程师）",
        "description": "社区建设风格，文档化、分析型、结构化、行动导向。关注生态建设和开发者体验。",
        "enabled": True,
        "content": """## 人物：yixiu.ckbfans.bit（社区建设者/文档工程师）

### 语言特点
- 文档化、分析型、结构化、行动导向。
- 长篇报告分章节，有"目标/具体行动/现状"。
- 中英文双语，社区导向，喜欢引用俗语。

### 常用句式
- "本文记录了..."
- "具体行动：... / 目标：..."
- "感谢 @xxx 及时提出这些重要的观察。"
- "我的小建议是："
- "中国有句古话：'千里之堤，溃于蚁穴'。"
- "Hey folks, ... / 大家好！..."

### 立场
- 生态建设者，关注开发者体验、社区治理、文档和基础设施。
- 分析全面，建议具体，注重长期可持续性。
- 善于从社区角度思考问题，推动改进。"""
    }
]


def generate_id():
    import uuid
    return uuid.uuid4().hex[:8]


def save_data(data):
    # 防止未来代码误把凭据重新写回 JSON。
    clean = dict(data)
    for field in ("kimi_api_key", "ai_api_key", "nervos_username", "nervos_password"):
        clean.pop(field, None)
    data.clear()
    data.update(clean)
    with _DATA_LOCK:
        _atomic_write_json(DATA_FILE, data)


# ============================================================
# AI API 调用（支持 Kimi / Silicon Flow）
# ============================================================

def call_ai_api(provider, api_key, prompt, model=None, timeout=90):
    """调用 AI API 生成回复。支持 Kimi 和 Silicon Flow。"""
    if not api_key:
        return None, "API Key 未设置"
    
    if provider == "siliconflow":
        base_url = "https://api.siliconflow.cn/v1/chat/completions"
        default_model = "deepseek-ai/DeepSeek-V3"
    else:  # kimi
        base_url = "https://api.moonshot.cn/v1/chat/completions"
        default_model = "moonshot-v1-8k"
    
    model = model or default_model
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5
    }
    
    try:
        req = urllib.request.Request(
            base_url,
            data=jsonlib.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = jsonlib.loads(resp.read().decode())
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return None, "API 返回了空内容"
        return content, None
    except urllib.error.HTTPError as e:
        try:
            body = jsonlib.loads(e.read().decode())
            err_msg = body.get("error", {}).get("message", str(e))
        except Exception:
            err_msg = str(e)
        return None, f"API 错误 ({e.code}): {err_msg}"
    except (TimeoutError, socket.timeout):
        return None, (f"请求超时（{timeout} 秒无响应）。AI 生成较慢，"
                      f"可点击重试；若反复超时，请检查网络或在设置中更换模型/服务商")
    except Exception as e:
        if "timed out" in str(e).lower():
            return None, (f"请求超时（{timeout} 秒无响应）。AI 生成较慢，"
                          f"可点击重试；若反复超时，请检查网络或在设置中更换模型/服务商")
        return None, f"请求失败: {str(e)}"


# ============================================================
# 预审参考内容（官方提案模板帖 #6 + 主帖规则）
# ============================================================

PRE_REVIEW_REF_CACHE_FILE = os.path.join(_BASE_DIR, "spark_pre_review_reference.json")
PRE_REVIEW_MAIN_POST_JSON = "https://talk.nervos.org/t/spark-program-mini-grant-initiative/8752.json"
PRE_REVIEW_REF_MAX_AGE_DAYS = 7      # 缓存有效期（天）

# 模板帖自动识别特征（模板帖楼层可能因删帖/重排而变化，按内容特征打分定位，
# 命中数 >= 3 才认定为模板帖；对应 8752 主帖下"提案模板：各章节内容详细说明"回复）
PRE_REVIEW_TEMPLATE_MARKERS = [
    "提案模板", "To-Do", "How to Verify", "Funding Breakdown",
    "Deliverables", "Project Overview", "CKB Alignment", "Team Profile",
]

# 在线获取失败时的兜底资助标准（2026-06 规则，与知识库一致）
FALLBACK_FUNDING_RULES = """资助档位标准（Spark Program 2026-06 规则）:
1. 纯技术项目: up to $1,000 — 开发工具、npm 包、CLI 等纯代码交付
2. 纯社区项目: up to $1,000 — 社区营销、种子用户测试、社区运营活动
3. 技术+社区综合: up to $2,000 — 需同时包含技术交付与社区活动两部分，并提供详细论证
4. 特殊/高难度: up to $5,000 — 需全体委员会成员一致同意
验证方式要求: 必须可通过非代码审查的方式低成本、可复现地验证（如 docker compose up、npm install && npm test、录屏、链上交易哈希）。
语言要求（2026-06 起）: 中英文均可，论坛已接入 AI 智能翻译，不再强制双语版本——预审时不得再以"缺少中文/双语版本"为由提出意见。"""

# 兜底提案模板部分清单（对应主帖下"提案模板：各章节内容详细说明"回复的标准章节）
FALLBACK_TEMPLATE_SECTIONS = """提案模板要求的标准章节（在线获取失败时的兜底清单）:
0. 标题与标签 / Title & Tags
1. 项目概述 / Project Overview
2. 团队简介 / Team Profile
3. 项目背景 / Project Background
4. 解决方案 / Solution
5. 技术方案 / Technical Approach
6. 执行计划 / To-Do List
7. 所需资金与资金分配明细 / Required Funding & Funding Breakdown
8. 交付物 + 验证方式 / Deliverables + How to Verify
9. 当前状态 vs. 资助范围 / Current State vs. Funded Work
10. CKB 契合度 / CKB Alignment"""


# ============================================================
# 知识库检索注入层（knowledge/ 目录 → 按需召回，替代全文截断）
# ============================================================

KNOWLEDGE_DIR = os.path.join(_BASE_DIR, "knowledge")

# 模板名关键词 → 事实知识路由（按顺序全部匹配，命中即纳入）
# ids: 必带块 id 中包含的子串；tags: 必带块标签；files: 必带整个文件的全部块
KNOWLEDGE_ROUTES = [
    ("Pending",  {"tags": ["预审", "预算"]}),
    ("待定",     {"tags": ["预审", "预算"]}),
    ("Approved", {"tags": ["打款", "预算"]}),
    ("通过",     {"tags": ["打款", "预算"]}),
    ("Rejection", {"tags": ["拒绝", "案例"]}),
    ("拒绝",     {"tags": ["拒绝", "案例"]}),
    ("关停",     {"tags": ["结项"], "ids": ["结项政策"]}),
    ("最后通牒", {"tags": ["催更"], "ids": ["结项政策"]}),
    ("轻柔提醒", {"tags": ["催更"]}),
    ("压力提醒", {"tags": ["催更"]}),
    ("打款",     {"tags": ["打款"]}),
    ("结项",     {"tags": ["结项"]}),
    ("评审结果", {"tags": ["结项"]}),
]

# 帖子关键词 → 召回标签（命中帖子标题/正文即召回对应知识块）
POST_KEYWORD_TAGS = {
    "fiber": "fiber", "rgb++": "rgb++", "rgbpp": "rgb++",
    "spore": "spore", "dob": "spore",
    "wallet": "钱包", "钱包": "钱包", "joyid": "钱包",
    "dex": "defi", "swap": "defi", "amm": "defi", "支付": "defi", "payment": "defi",
    "教程": "教育", "教育": "教育", "guide": "教育", "文档": "教育", "onboarding": "教育",
    "sdk": "开发工具", "cli": "开发工具", "npm": "开发工具", "ccc": "开发工具",
    "节点": "节点", "node": "节点", "indexer": "节点", "rpc": "节点",
    "ai ": "ai", "神经网络": "ai", "llm": "ai",
    "cell": "cell模型", "utxo": "cell模型",
    "dao": "dao",
}

# 措辞 Skill 章节路由：模板关键词 → 章节标题包含的子串
PHRASING_SECTION_MAP = {
    "通过": ["二、通过", "七、通用"],
    "Approved": ["二、通过", "七、通用"],
    "分期": ["二、通过", "七、通用"],
    "预算调整": ["三、待定", "七、通用"],
    "Pending": ["三、待定", "七、通用"],
    "待定": ["三、待定", "七、通用"],
    "Rejection": ["四、拒绝", "七、通用"],
    "拒绝": ["四、拒绝", "七、通用"],
    "关停": ["五、关闭", "七、通用"],
    "完成": ["六、完成", "七、通用"],
    "结项": ["六、完成", "七、通用"],
}
PHRASING_DEFAULT_SECTIONS = ["一、决议类型定义", "七、通用"]

ZZ_SECTION_MAP = {
    "通过通知": ["一、总体风格", "二、通用骨架", "三、通过"],
    "分期打款": ["一、总体风格", "二、通用骨架", "六、分期打款"],
    "预算调整": ["一、总体风格", "二、通用骨架", "五、预算调整"],
    "待定": ["一、总体风格", "二、通用骨架", "四、待定"],
    "拒绝": ["一、总体风格", "二、通用骨架", "七、拒绝"],
    "关停": ["一、总体风格", "二、通用骨架", "八、关闭"],
    "警告": ["一、总体风格", "二、通用骨架", "九、警告"],
}
ZZ_DEFAULT_SECTIONS = ["一、总体风格", "二、通用骨架", "十、使用说明"]

_KNOWLEDGE_CACHE = {"mtime": 0.0, "chunks": []}


def load_knowledge_chunks():
    """加载 knowledge/ 下全部知识块（含内容），按目录 mtime 缓存。
    依赖 build_knowledge_index.py 的切块逻辑；失败时返回空列表（不影响主流程）。"""
    try:
        mtime = 0.0
        for root, _, files in os.walk(KNOWLEDGE_DIR):
            for fn in files:
                if fn.endswith(".md"):
                    mtime = max(mtime, os.path.getmtime(os.path.join(root, fn)))
        if _KNOWLEDGE_CACHE["chunks"] and mtime <= _KNOWLEDGE_CACHE["mtime"]:
            return _KNOWLEDGE_CACHE["chunks"]
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location(
            "bki", os.path.join(_BASE_DIR, "build_knowledge_index.py"))
        bki = _ilu.module_from_spec(spec)
        spec.loader.exec_module(bki)
        chunks = bki.load_chunks_with_content(KNOWLEDGE_DIR)
        _KNOWLEDGE_CACHE["mtime"] = mtime
        _KNOWLEDGE_CACHE["chunks"] = chunks
        return chunks
    except Exception:
        return _KNOWLEDGE_CACHE.get("chunks", [])


def _post_recall_tags(post):
    """从帖子标题+正文提取召回标签。"""
    text = (post.get("title", "") + " " + (post.get("content_full") or post.get("content", ""))).lower()
    return {tag for kw, tag in POST_KEYWORD_TAGS.items() if kw in text}


def _post_precedent_match(chunk, post):
    """判例库项目块：标题中的项目名是否出现在帖子标题中。"""
    if "precedents.md" not in chunk["file"]:
        return False
    proj = re.split(r"[（(]", chunk["heading"])[0].strip().lower()
    title = post.get("title", "").lower()
    return len(proj) >= 4 and proj in title


def retrieve_knowledge_context(template_name, post, max_chars=6000):
    """按模板路由 + 帖子关键词召回事实知识块，组装为注入文本（不含风格层）。
    优先级：路由 ids/files → 路由 tags → 判例项目名匹配 → 帖子关键词标签；按预算截断。"""
    chunks = [c for c in load_knowledge_chunks() if not c["file"].startswith("style/")]
    if not chunks:
        return ""
    route_ids, route_tags, route_files = set(), set(), set()
    for kw, route in KNOWLEDGE_ROUTES:
        if kw in template_name:
            route_ids.update(route.get("ids", []))
            route_tags.update(route.get("tags", []))
            route_files.update(route.get("files", []))
    recall_tags = _post_recall_tags(post)

    picked, seen = [], set()

    def _add(pred):
        for c in chunks:
            if c["id"] in seen:
                continue
            if pred(c):
                seen.add(c["id"])
                picked.append(c)

    _add(lambda c: any(s in c["id"] for s in route_ids))
    _add(lambda c: c["file"] in route_files)
    _add(lambda c: route_tags & set(c["tags"]))
    _add(lambda c: _post_precedent_match(c, post))
    # 关键词召回：优先 ckb/ 技术块，判例块靠后
    _add(lambda c: recall_tags & set(c["tags"]) and c["file"].startswith("ckb/"))
    _add(lambda c: recall_tags & set(c["tags"]) and "precedents.md" in c["file"])

    out, total = [], 0
    for c in picked:
        if total + c["chars"] > max_chars:
            continue
        out.append(f"### [{c['file']}] {c['heading']}\n{c['content']}")
        total += c["chars"]
    return "\n\n".join(out)


def route_phrasing_sections(template_name, rel_path, section_map, default_sections, max_chars=6000):
    """从措辞 Skill 文件中按模板类型抽取对应章节（替代全文截断）。
    rel_path 相对 knowledge/ 目录；找不到文件时尝试工作区根目录的旧位置。"""
    for base in (KNOWLEDGE_DIR, _BASE_DIR):
        path = os.path.join(base, rel_path)
        if os.path.exists(path):
            break
    else:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return ""
    parts = re.split(r"(?m)^(## .+)$", text)
    sections = {}
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections[title] = (title + "\n" + body).strip()
    wanted = default_sections
    for kw, secs in section_map.items():
        if kw in template_name:
            wanted = secs
            break
    out, total = [], 0
    for title, content in sections.items():
        if any(w in title for w in wanted) and total + len(content) <= max_chars:
            out.append(content)
            total += len(content)
    return "\n\n".join(out)


def load_funding_rules_text():
    """资助档位标准：优先取知识库 rules.md 的资金档位章节，文件缺失时回退到内置常量。"""
    for c in load_knowledge_chunks():
        if c["file"] == "spark/rules.md" and "资金档位" in c["heading"]:
            return c["content"]
    return FALLBACK_FUNDING_RULES


def _strip_html(text):
    """去掉 HTML 标签并解码 HTML 实体，尽量保留换行结构，输出纯文本。"""
    import html as _html
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6]|tr|blockquote)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch_pre_review_reference(force=False):
    """获取预审参考内容：主帖规则(#1) + 官方提案模板帖（按内容特征自动定位）。
    带本地缓存（默认 7 天有效），获取失败时回退到过期缓存，再回退到内置兜底规则。
    返回 dict: {"main_post": str, "template_post": str, "updated": str, "error": str|None}
    """
    # 1. 优先读取有效缓存
    if not force and os.path.exists(PRE_REVIEW_REF_CACHE_FILE):
        try:
            with open(PRE_REVIEW_REF_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = jsonlib.load(f)
            updated = cache.get("updated", "")
            if updated and cache.get("main_post") and cache.get("template_post"):
                age = (datetime.datetime.now() - datetime.datetime.fromisoformat(updated)).days
                if age < PRE_REVIEW_REF_MAX_AGE_DAYS:
                    return cache
        except Exception:
            pass
    # 2. 在线获取（一次请求同时拿到主帖和模板帖）
    try:
        req = urllib.request.Request(PRE_REVIEW_MAIN_POST_JSON, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = jsonlib.loads(resp.read().decode("utf-8"))
        posts = data.get("post_stream", {}).get("posts", [])
        main_post = ""
        best_score, best_text, best_number = 0, "", None
        for p in posts:
            cooked = p.get("cooked", "")
            if p.get("post_number") == 1:
                main_post = _strip_html(cooked)
                continue
            # 按内容特征打分，自动定位"提案模板"回复（楼层可能因删帖重排而变化）
            score = sum(1 for mk in PRE_REVIEW_TEMPLATE_MARKERS if mk.lower() in cooked.lower())
            if score > best_score:
                best_score, best_text, best_number = score, _strip_html(cooked), p.get("post_number")
        template_post = best_text if best_score >= 3 else ""
        if not main_post or not template_post:
            raise ValueError(f"主帖或模板帖定位失败（共 {len(posts)} 层回复，模板最高匹配分 {best_score}）")
        cache = {
            "main_post": main_post[:16000],
            "template_post": template_post[:8000],
            "updated": datetime.datetime.now().isoformat(),
            "error": None,
        }
        try:
            with open(PRE_REVIEW_REF_CACHE_FILE, "w", encoding="utf-8") as f:
                jsonlib.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return cache
    except Exception as e:
        # 3. 回退：过期缓存
        if os.path.exists(PRE_REVIEW_REF_CACHE_FILE):
            try:
                with open(PRE_REVIEW_REF_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache = jsonlib.load(f)
                if cache.get("main_post") and cache.get("template_post"):
                    cache["error"] = f"在线更新失败（{e}），使用 {cache.get('updated', '?')[:10]} 的缓存"
                    return cache
            except Exception:
                pass
        # 4. 最终回退：内置兜底规则
        return {
            "main_post": FALLBACK_FUNDING_RULES,
            "template_post": FALLBACK_TEMPLATE_SECTIONS,
            "updated": "",
            "error": f"无法获取参考帖（{e}），已使用内置兜底规则",
        }





# ============================================================
# 主应用
# ============================================================

class SparkTaskTracker:
    def __init__(self, root):
        self.root = root
        self.alpha = 0.92
        self.is_topmost = True
        self._normal_geo = None
        self._drag_x = 0
        self._drag_y = 0
        self._hover_timer = None
        self._btn_minimize = None
        self._btn_alpha = None
        self._btn_topmost = None
        self._btn_close = None

        self.data = load_data()
        # 确保回复措辞知识库字段存在
        if "reply_tone_knowledge" not in self.data:
            self.data["reply_tone_knowledge"] = ""
        # 确保人物字段存在，无数据时填充默认人物
        if "characters" not in self.data or not self.data.get("characters"):
            self.data["characters"] = [dict(c) for c in DEFAULT_CHARACTERS]
        # 确保看板白名单字段存在
        if "board_whitelist" not in self.data:
            self.data["board_whitelist"] = {}
        # 合并用户自定义模板（覆盖硬编码的默认模板）
        for name, custom in self.data.get("templates", {}).items():
            if name in TEMPLATES:
                TEMPLATES[name]["template"] = custom.get("template", TEMPLATES[name]["template"])
        self.task_filter = tk.StringVar(value="未完成")

        self._setup_window()
        self._apply_theme()
        self._build_ui()
        self.refresh_task_list()
        self.root.after(2000, self._check_project_reminders)
        self._load_project_cache_display()

    # =================== 窗口系统 ===================

    def _setup_window(self):
        self.root.title("Spark Program")
        # 设置窗口图标
        icon_path = os.path.join(_BASE_DIR, "spark_icon.ico")
        if os.path.exists(icon_path):
            self.root.iconbitmap(icon_path)
        self.root.geometry("880x620+120+80")
        self.root.attributes("-alpha", self.alpha)
        self.root.attributes("-topmost", self.is_topmost)
        self.root.configure(bg=THEME["bg"])
        # Windows 圆角（DWM）+ 去掉原生边框（保留最小化功能）
        self.root.after(50, self._setup_windows_frame)

    def _setup_windows_frame(self):
        """通过 Windows API 去掉标题栏和边框，保留最小化按钮，添加任务栏图标。"""
        try:
            import ctypes
            hwnd = self.root.winfo_id()

            GWL_STYLE = -16
            WS_CAPTION = 0x00C00000
            WS_THICKFRAME = 0x00040000
            WS_MINIMIZEBOX = 0x00020000
            WS_SYSMENU = 0x00080000

            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000

            # 修改样式：去掉标题栏和边框，保留最小化按钮和系统菜单
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            style = style & ~WS_CAPTION & ~WS_THICKFRAME
            style = style | WS_MINIMIZEBOX | WS_SYSMENU
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)

            # 扩展样式：确保在任务栏显示
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex_style = ex_style | WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)

            # 重新计算窗口非客户区
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
            )

            # Windows 11 圆角
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
            except Exception:
                pass
        except Exception:
            pass

    def _apply_theme(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        bg = THEME["bg"]
        card = THEME["card"]
        accent = THEME["accent"]
        text = THEME["text"]
        text_dim = THEME["text_dim"]
        border = THEME["border"]
        hover = THEME["card_hover"]
        danger = THEME["danger"]

        style.configure(".", background=bg, foreground=text, font=("Microsoft YaHei", 10))
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=text)

        style.configure("TButton", background=card, foreground=text,
                        bordercolor=border, lightcolor=hover, darkcolor=card,
                        padding=5, font=("Microsoft YaHei", 10))
        style.map("TButton", background=[("active", hover), ("pressed", hover)],
                  foreground=[("active", accent)])

        style.configure("TNotebook", background=bg, tabmargins=[2, 5, 2, 0])
        style.configure("TNotebook.Tab", background=card, foreground=text_dim,
                        padding=[10, 5], font=("Microsoft YaHei", 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", accent), ("active", hover)],
                  foreground=[("selected", bg), ("active", text)])

        style.configure("Treeview", background=card, foreground=text,
                        fieldbackground=card, rowheight=28)
        style.configure("Treeview.Heading", background=hover, foreground=text,
                        font=("Microsoft YaHei", 10, "bold"))
        style.map("Treeview", background=[("selected", accent)],
                  foreground=[("selected", bg)])

        style.configure("TScrollbar", background=card, troughcolor=bg,
                        arrowcolor=text, bordercolor=border)
        style.configure("TCombobox", fieldbackground=card, background=card,
                        foreground=text, arrowcolor=text)
        style.configure("TEntry", fieldbackground=card, foreground=text,
                        insertcolor=text, bordercolor=border)
        style.configure("TLabelframe", background=bg, bordercolor=border)
        style.configure("TLabelframe.Label", background=bg, foreground=accent,
                        font=("Microsoft YaHei", 10, "bold"))

        style.configure("Close.TButton", background=card, foreground=danger,
                        font=("Microsoft YaHei", 10, "bold"))
        style.map("Close.TButton", background=[("active", danger)],
                  foreground=[("active", bg)])

    # =================== 自定义标题栏 ===================

    def _build_titlebar(self, parent):
        tb = tk.Frame(parent, bg=THEME["titlebar"], height=36)
        tb.pack(fill=tk.X, padx=0, pady=0)
        tb.pack_propagate(False)

        # 拖拽区域
        drag = tk.Frame(tb, bg=THEME["titlebar"])
        drag.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        drag.bind("<Button-1>", self._start_drag)
        drag.bind("<B1-Motion>", self._on_drag)

        tk.Label(drag, text="✨", bg=THEME["titlebar"], font=("Segoe UI", 14)).pack(side=tk.LEFT, padx=8)
        tk.Label(drag, text="Spark Program 联络员", bg=THEME["titlebar"],
                 fg=THEME["accent"], font=("Microsoft YaHei", 11, "bold")).pack(side=tk.LEFT)
        tk.Label(drag, text="桌面精灵", bg=THEME["titlebar"],
                 fg=THEME["text_dim"], font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=5)

        # 控制按钮
        ctrl = tk.Frame(tb, bg=THEME["titlebar"])
        ctrl.pack(side=tk.RIGHT, padx=5)

        self._btn_minimize = self._make_ctrl_btn(ctrl, "🗕", self._minimize, tooltip="收起/恢复")
        self._btn_alpha = self._make_ctrl_btn(ctrl, "🔍", self._cycle_alpha, tooltip="切换透明度")
        self._btn_topmost = self._make_ctrl_btn(ctrl, "📌", self._toggle_topmost, tooltip="置顶/取消置顶")
        self._btn_close = self._make_ctrl_btn(ctrl, "✕", self._on_close, tooltip="关闭", fg=THEME["danger"])

        return tb

    def _make_ctrl_btn(self, parent, text, cmd, tooltip="", fg=None):
        color = fg or THEME["text_dim"]
        lbl = tk.Label(parent, text=text, bg=THEME["titlebar"], fg=color,
                       font=("Segoe UI", 13), cursor="hand2")
        lbl.pack(side=tk.LEFT, padx=4)
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.config(bg=THEME["card_hover"]))
        lbl.bind("<Leave>", lambda e: lbl.config(bg=THEME["titlebar"]))
        return lbl

    # =================== 窗口控制 ===================

    def _start_drag(self, event):
        self._drag_x = event.x_root - self.root.winfo_rootx()
        self._drag_y = event.y_root - self.root.winfo_rooty()

    def _on_drag(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _minimize(self):
        self.root.iconify()

    def _on_close(self):
        self.root.destroy()

    def _toggle_topmost(self):
        self.is_topmost = not self.is_topmost
        self.root.attributes("-topmost", self.is_topmost)
        if self.is_topmost:
            self._btn_topmost.config(text="📌", fg=THEME["accent"])
        else:
            self._btn_topmost.config(text="📍", fg=THEME["text_dim"])

    def _cycle_alpha(self):
        alphas = [1.0, 0.92, 0.85, 0.75, 0.6, 0.92]
        idx = alphas.index(self.alpha) if self.alpha in alphas else 0
        self.alpha = alphas[(idx + 1) % len(alphas)]
        self.root.attributes("-alpha", self.alpha)

    # =================== UI 构建 ===================

    def _build_ui(self):
        self._build_titlebar(self.root)

        self.content = tk.Frame(self.root, bg=THEME["bg"])
        self.content.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.notebook = ttk.Notebook(self.content)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_tasks = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_tasks, text="📋 任务")
        self._build_tab_tasks()

        self.tab_projects = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_projects, text="📊 看板")
        self._build_tab_projects()

        self.tab_timeline = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_timeline, text="📅 时间线")
        self._build_tab_timeline()

        self.tab_templates = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_templates, text="📝 模板")
        self._build_tab_templates()

        self.tab_ai = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_ai, text="🤖 AI起草")
        self._build_tab_ai()

        self.tab_links = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_links, text="🔗 链接")
        self._build_tab_links()

        self.tab_knowledge = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_knowledge, text="🧠 知识库")
        self._build_tab_knowledge()

        self.tab_meetings = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_meetings, text="📋 纪要")
        self._build_tab_meetings()

        self.tab_meeting_items = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_meeting_items, text="🏛 上会项目")
        self._build_tab_meeting_items()

    # =================== Tab 5: AI起草 ===================

    def _build_tab_ai(self):
        """AI起草标签页：直接调用 API 生成最终草稿。"""
        # ===== 顶部：模板下拉菜单 =====
        top_frame = tk.Frame(self.tab_ai, bg=THEME["bg"])
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(top_frame, text="起草模板:", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT)
        self.ai_template_var = tk.StringVar(value="-- 选择模板 --")
        template_names = ["-- 选择模板 --"] + list(TEMPLATES.keys())
        self.ai_template_combo = ttk.Combobox(top_frame, textvariable=self.ai_template_var,
                                               values=template_names, width=40, state="readonly",
                                               font=("Microsoft YaHei", 10))
        self.ai_template_combo.pack(side=tk.LEFT, padx=5)
        self.ai_template_combo.bind("<<ComboboxSelected>>", self._on_ai_template_select)
        self.ai_template_desc_label = tk.Label(top_frame, text="", bg=THEME["bg"], fg=THEME["text_dim"],
                                                font=("Microsoft YaHei", 9))
        self.ai_template_desc_label.pack(side=tk.LEFT, padx=5)

        # 风格人物下拉（默认不注入任何人物；可选项来自知识库页启用的人物）
        tk.Label(top_frame, text="  风格:", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT, padx=(15, 0))
        self.ai_style_var = tk.StringVar(value="默认（委员会口吻）")
        self.ai_style_combo = ttk.Combobox(top_frame, textvariable=self.ai_style_var,
                                            values=self._style_options(), width=22, state="readonly",
                                            font=("Microsoft YaHei", 10),
                                            postcommand=lambda: self.ai_style_combo.config(values=self._style_options()))
        self.ai_style_combo.pack(side=tk.LEFT, padx=5)

        # 分隔线
        ttk.Separator(self.tab_ai, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=5)

        # ===== 服务商选择 =====
        provider_frame = tk.Frame(self.tab_ai, bg=THEME["bg"])
        provider_frame.pack(fill=tk.X, padx=5, pady=2)

        tk.Label(provider_frame, text="AI服务商:", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT)

        self.ai_provider_var = tk.StringVar(value=self.data.get("ai_provider", "siliconflow"))
        self.ai_model_var = tk.StringVar(value=self.data.get("ai_model", ""))

        tk.Radiobutton(provider_frame, text="Kimi", variable=self.ai_provider_var, value="kimi",
                       bg=THEME["bg"], fg=THEME["text"], selectcolor=THEME["card"],
                       font=("Microsoft YaHei", 9), command=self._update_ai_model_list).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(provider_frame, text="Silicon Flow", variable=self.ai_provider_var, value="siliconflow",
                       bg=THEME["bg"], fg=THEME["text"], selectcolor=THEME["card"],
                       font=("Microsoft YaHei", 9), command=self._update_ai_model_list).pack(side=tk.LEFT, padx=5)

        tk.Label(provider_frame, text="模型:", bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=(15, 0))
        self.ai_model_combo = ttk.Combobox(provider_frame, textvariable=self.ai_model_var, width=30, state="readonly")
        self.ai_model_combo.pack(side=tk.LEFT, padx=5)
        self._update_ai_model_list()

        # ===== API Key =====
        api_frame = tk.Frame(self.tab_ai, bg=THEME["bg"])
        api_frame.pack(fill=tk.X, padx=5, pady=2)

        self.ai_api_key_var = tk.StringVar(value=get_ai_api_key(self.ai_provider_var.get()))
        has_key = bool(self.ai_api_key_var.get().strip())

        self.ai_api_status = tk.Label(api_frame, text="✅ 已设置" if has_key else "❌ 未设置",
                                      bg=THEME["bg"], fg=THEME["success"] if has_key else THEME["danger"],
                                      font=("Microsoft YaHei", 10, "bold"))
        self.ai_api_status.pack(side=tk.LEFT, padx=5)

        self.ai_api_entry = tk.Entry(api_frame, textvariable=self.ai_api_key_var, width=45,
                                     bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                                     bd=1, relief=tk.FLAT, show="●")
        self.ai_api_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        tk.Button(api_frame, text="👁", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._toggle_ai_api_key).pack(side=tk.LEFT, padx=3)

        tk.Button(api_frame, text="💾 保存", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._save_ai_settings).pack(side=tk.LEFT, padx=3)

        # ===== 项目选择 =====
        proj_frame = tk.Frame(self.tab_ai, bg=THEME["bg"])
        proj_frame.pack(fill=tk.X, padx=5, pady=2)

        tk.Label(proj_frame, text="项目帖子:", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT)

        self.ai_proj_var = tk.StringVar()
        self.ai_proj_urls = {}
        proj_list = ["-- 手动输入 URL --"]
        # 从看板缓存加载 Pre-review / Submitted / Pending / In-Progress 帖子
        cache = load_project_cache()
        if cache and "classified" in cache:
            c = cache["classified"]
            for label, key in [("[Pre-review]", "pre_review"), ("[Submitted]", "submitted"),
                               ("[Pending]", "pending"), ("[In-Progress]", "in_progress")]:
                for post in c.get("spark_program", {}).get(key, []) if key == "pre_review" else c.get(key, []):
                    title = post.get("title", "")
                    if title:
                        display = f"{label} {title}"
                        proj_list.append(display)
                        self.ai_proj_urls[display] = post.get("url", "")
        self.ai_proj_combo = ttk.Combobox(proj_frame, textvariable=self.ai_proj_var, values=proj_list, width=55, state="readonly")
        self.ai_proj_combo.pack(side=tk.LEFT, padx=5)
        self.ai_proj_combo.set(proj_list[0])

        self.ai_url_var = tk.StringVar()
        tk.Entry(proj_frame, textvariable=self.ai_url_var, width=35, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        self.ai_proj_var.trace("w", self._on_ai_proj_change)

        # ===== 中间左右分栏：会议纪要(半宽) + 补充内容(半宽) =====
        mid_frame = tk.Frame(self.tab_ai, bg=THEME["bg"])
        mid_frame.pack(fill=tk.X, padx=5, pady=2)
        mid_frame.columnconfigure(0, weight=1)
        mid_frame.columnconfigure(1, weight=1)

        # 左侧：会议纪要
        self.ai_meeting_frame = tk.LabelFrame(mid_frame, text="  会议纪要选取  ", bg=THEME["bg"],
                                               fg=THEME["accent2"], font=("Microsoft YaHei", 10, "bold"),
                                               bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                               highlightthickness=1)
        self.ai_meeting_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        
        meeting_top = tk.Frame(self.ai_meeting_frame, bg=THEME["bg"])
        meeting_top.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(meeting_top, text="选择会议:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=2)
        self.ai_meeting_var = tk.StringVar(value="-- 选择会议 --")
        self.ai_meeting_combo = ttk.Combobox(meeting_top, textvariable=self.ai_meeting_var, 
                                                width=35, state="readonly", font=("Microsoft YaHei", 9))
        self.ai_meeting_combo.pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)
        tk.Button(meeting_top, text="📋 加载", bg=THEME["accent2"], fg=THEME["bg"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._load_selected_meeting).pack(side=tk.LEFT, padx=2)
        
        self.ai_meeting_info = scrolledtext.ScrolledText(self.ai_meeting_frame, font=("Microsoft YaHei", 9),
                                                          wrap=tk.WORD, bg=THEME["card"], fg=THEME["text"],
                                                          insertbackground=THEME["text"], relief=tk.FLAT, bd=1, height=6)
        self.ai_meeting_info.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)
        self.ai_meeting_info.insert("1.0", "从上方下拉菜单选择会议，点击【加载】查看该会议的决议信息。")
        self.ai_meeting_info.config(state=tk.DISABLED)
        self.ai_meeting_status = tk.Label(self.ai_meeting_frame, text="", bg=THEME["bg"], fg=THEME["text_dim"],
                                           font=("Microsoft YaHei", 9))
        self.ai_meeting_status.pack(anchor="w", padx=5, pady=(0, 3))
        self._ai_meeting_map = {}  # 会议显示文本 -> 决议信息

        # 右侧：补充内容
        self.ai_supplement_frame = tk.LabelFrame(mid_frame, text="  补充内容  ", bg=THEME["bg"],
                                                   fg=THEME["accent"], font=("Microsoft YaHei", 10, "bold"),
                                                   bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                                   highlightthickness=1)
        self.ai_supplement_frame.grid(row=0, column=1, sticky="nsew", padx=(2, 0))
        self.ai_supplement_text = scrolledtext.ScrolledText(self.ai_supplement_frame, font=("Microsoft YaHei", 9),
                                                              wrap=tk.WORD, bg=THEME["card"], fg=THEME["text"],
                                                              insertbackground=THEME["text"], relief=tk.FLAT, bd=1, height=6)
        self.ai_supplement_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.ai_supplement_text.insert("1.0", "在此输入补充内容，生成草稿时将同时参考会议纪要和补充内容。\n例如：额外的评审意见、特定要求、背景信息等。")
        tk.Label(self.ai_supplement_frame, text="生成时将同时参考此处的补充内容", bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Microsoft YaHei", 8)).pack(anchor="w", padx=5, pady=(0, 3))

        # 按钮行
        btn_frame = tk.Frame(self.tab_ai, bg=THEME["bg"])
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        self.ai_gen_btn = tk.Button(btn_frame, text="⚡ 生成草稿", bg=THEME["accent"], fg=THEME["bg"],
                                    activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                                    bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 11, "bold"),
                                    command=self._ai_generate)
        self.ai_gen_btn.pack(side=tk.LEFT, padx=3)

        tk.Button(btn_frame, text="📋 复制", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 10),
                  command=self._ai_copy).pack(side=tk.LEFT, padx=3)

        tk.Button(btn_frame, text="🌐 快捷翻译", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 10),
                  command=self._ai_translate).pack(side=tk.LEFT, padx=3)

        tk.Button(btn_frame, text="🔄 刷新本页", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 10),
                  command=self._refresh_ai_tab).pack(side=tk.LEFT, padx=3)

        # 状态
        self.ai_status_label = tk.Label(self.tab_ai, text="选择模板后点击 ⚡ 生成草稿",
                                        bg=THEME["bg"], fg=THEME["success"], font=("Microsoft YaHei", 10))
        self.ai_status_label.pack(anchor="w", padx=5, pady=2)

        # 结果区域
        tk.Label(self.tab_ai, text="最终回复草稿:", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=5, pady=2)

        self.ai_result_text = scrolledtext.ScrolledText(self.tab_ai, font=("Microsoft YaHei", 10), wrap=tk.WORD,
                                                          bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                                                          relief=tk.FLAT, bd=2, height=10)
        self.ai_result_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.ai_result_text.insert("1.0", "选择模板和项目后，点击【⚡ 生成草稿】直接生成最终回复...")
        self.ai_result_text.config(state=tk.DISABLED)

        # 当前模板/项目/Prompt 存储
        self._ai_current_template = None
        self._ai_current_template_info = None
        self._ai_current_prompt = ""

    def _style_options(self):
        """风格下拉选项：默认 + 知识库页已启用的人物。"""
        opts = ["默认（委员会口吻）"]
        for char in self.data.get("characters", []):
            if char.get("enabled", True):
                opts.append(char.get("display_name", char.get("name", "")))
        return opts

    def _selected_style_id(self):
        """当前风格下拉选中项对应的人物 id；默认返回空串。"""
        sel = self.ai_style_var.get() if hasattr(self, "ai_style_var") else ""
        for char in self.data.get("characters", []):
            if char.get("display_name", char.get("name")) == sel:
                return char.get("id", "")
        return ""

    def _on_ai_template_select(self, event=None):
        """模板下拉菜单选中事件。"""
        name = self.ai_template_var.get()
        if name and name != "-- 选择模板 --":
            info = TEMPLATES.get(name, {})
            self._ai_current_template = name
            self._ai_current_template_info = info
            self.ai_template_desc_label.config(text=info.get("desc", ""))
            self.ai_status_label.config(text=f"✅ 已加载模板: {name}，选择项目后点击 ⚡ 生成草稿")
            self.ai_status_label.config(fg=THEME["success"])
        else:
            self._ai_current_template = None
            self._ai_current_template_info = None
            self.ai_template_desc_label.config(text="")
            self.ai_status_label.config(text="⚠️ 未选择模板，请从下拉菜单选择")
            self.ai_status_label.config(fg=THEME["warning"])

    def _refresh_ai_tab(self):
        """刷新AI起草页状态：重新加载项目列表、会议列表、模板列表。"""
        # 刷新项目列表
        self.ai_proj_urls = {}
        proj_list = ["-- 手动输入 URL --"]
        cache = load_project_cache()
        if cache and "classified" in cache:
            c = cache["classified"]
            for label, key in [("[Pre-review]", "pre_review"), ("[Submitted]", "submitted"),
                               ("[Pending]", "pending"), ("[In-Progress]", "in_progress")]:
                for post in c.get("spark_program", {}).get(key, []) if key == "pre_review" else c.get(key, []):
                    title = post.get("title", "")
                    if title:
                        display = f"{label} {title}"
                        proj_list.append(display)
                        self.ai_proj_urls[display] = post.get("url", "")
        self.ai_proj_combo['values'] = proj_list
        cur = self.ai_proj_var.get()
        if cur not in proj_list:
            self.ai_proj_var.set(proj_list[0])
            self.ai_url_var.set("")

        # 刷新会议列表
        self._refresh_ai_meeting_combo()

        # 刷新模板列表
        template_names = ["-- 选择模板 --"] + list(TEMPLATES.keys())
        self.ai_template_combo['values'] = template_names
        cur_t = self.ai_template_var.get()
        if cur_t not in template_names:
            self.ai_template_var.set("-- 选择模板 --")
            self._ai_current_template = None
            self._ai_current_template_info = None
            self.ai_template_desc_label.config(text="")

        self.ai_status_label.config(text="🔄 页面已刷新，请重新选择模板和项目")
        self.ai_status_label.config(fg=THEME["accent"])

    def _update_ai_model_list(self):
        MODELS = {
            "kimi": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "moonshot-v1-auto"],
            "siliconflow": [
                "deepseek-ai/DeepSeek-V4-Flash",
                "deepseek-ai/DeepSeek-V3",
                "deepseek-ai/DeepSeek-R1",
                "Qwen/Qwen3.6-27B",
                "Qwen/Qwen3-235B-A22B",
                "Qwen/QwQ-32B",
                "Pro/moonshotai/Kimi-K2.6",
                "Pro/Qwen/Qwen3-235B-A22B",
                "zai-org/GLM-5.2",
                "Pro/MiniMaxAI/MiniMax-M2.5"
            ]
        }
        p = self.ai_provider_var.get()
        models = MODELS.get(p, [])
        self.ai_model_combo['values'] = models
        current = self.ai_model_var.get()
        if current not in models:
            self.ai_model_var.set(models[0] if models else "")
        if hasattr(self, "ai_api_key_var"):
            key = get_ai_api_key(p)
            self.ai_api_key_var.set(key)
            if hasattr(self, "ai_api_status"):
                self.ai_api_status.config(text="✅ 已设置" if key else "❌ 未设置",
                                          fg=THEME["success"] if key else THEME["danger"])

    def _toggle_ai_api_key(self):
        if self.ai_api_entry.cget("show") == "●":
            self.ai_api_entry.config(show="")
        else:
            self.ai_api_entry.config(show="●")

    def _save_ai_settings(self):
        provider = self.ai_provider_var.get()
        self.data["ai_provider"] = provider
        self.data["ai_model"] = self.ai_model_var.get()
        set_ai_api_key(self.ai_api_key_var.get().strip(), provider)
        save_data(self.data)
        key = self.ai_api_key_var.get().strip()
        self.ai_api_status.config(text="✅ 已设置" if key else "❌ 未设置",
                                  fg=THEME["success"] if key else THEME["danger"])

    def _on_ai_proj_change(self, *args):
        p = self.ai_proj_var.get()
        if p in self.ai_proj_urls:
            url = self.ai_proj_urls[p]
            self.ai_url_var.set(url)
        elif p == "-- 手动输入 URL --":
            self.ai_url_var.set("")
        # 刷新会议下拉框
        self._refresh_ai_meeting_combo()

    def _refresh_ai_meeting_combo(self):
        """刷新会议纪要下拉框，列出所有会议。"""
        meetings = self.data.get("meetings", [])
        items = ["-- 选择会议 --"]
        self._ai_meeting_map = {}
        for m in sorted(meetings, key=lambda x: x.get("date", ""), reverse=True):
            date = m.get("date", "")
            title = m.get("title", "")
            display = f"{date} {title}"
            items.append(display)
            # 收集该会议的所有决议信息
            resolutions = m.get("resolutions", [])
            if resolutions:
                info_lines = []
                for r in resolutions:
                    info_lines.append(f"项目: {r.get('project', '')}")
                    info_lines.append(f"决议: {r.get('decision', '')}")
                    info_lines.append(f"轮次: {r.get('round', '')}")
                    info_lines.append(f"预算: {r.get('budget', '')}")
                    info_lines.append(f"备注: {r.get('note', '')}")
                    info_lines.append("---")
                self._ai_meeting_map[display] = "\n".join(info_lines)
            else:
                self._ai_meeting_map[display] = "该会议暂无决议记录。"
        self.ai_meeting_combo['values'] = items
        self.ai_meeting_var.set(items[0] if items else "")

    def _load_selected_meeting(self):
        """加载选中的会议纪要决议信息。"""
        display = self.ai_meeting_var.get()
        if not display or display == "-- 选择会议 --":
            self.ai_meeting_info.config(state=tk.NORMAL)
            self.ai_meeting_info.delete("1.0", tk.END)
            self.ai_meeting_info.insert("1.0", "请先从下拉菜单选择一个会议。")
            self.ai_meeting_info.config(state=tk.DISABLED)
            self.ai_meeting_status.config(text="⚠️ 未选择会议", fg=THEME["warning"])
            return
        info = self._ai_meeting_map.get(display, "")
        self.ai_meeting_info.config(state=tk.NORMAL)
        self.ai_meeting_info.delete("1.0", tk.END)
        self.ai_meeting_info.insert("1.0", info)
        self.ai_meeting_info.config(state=tk.DISABLED)
        self.ai_meeting_status.config(text=f"✅ 已加载 {display.split()[0]} 的决议", fg=THEME["success"])

    def _switch_to_ai_tab(self, template_name=None):
        """从模板页切换到AI起草标签页，并自动加载选中的模板。"""
        self.notebook.select(self.tab_ai)
        if template_name:
            self.ai_template_var.set(template_name)
            self._on_ai_template_select()
        else:
            sel = self.template_listbox.curselection()
            if sel:
                name = self.template_listbox.get(sel[0])
                self._switch_to_ai_tab(name)
            else:
                self.ai_status_label.config(text="⚠️ 未选择模板，请从下拉菜单选择")
                self.ai_status_label.config(fg=THEME["warning"])

    def _find_latest_resolution_for_url(self, url):
        """根据帖子 URL 查找该项目的最新委员会决议。返回 (resolution_info, meeting_date) 或 ("", "")。"""
        if not url:
            return "", ""
        meetings = self.data.get("meetings", [])
        if not meetings:
            return "", ""
        # 按日期降序排序
        sorted_meetings = sorted(meetings, key=lambda m: m.get("date", ""), reverse=True)
        for m in sorted_meetings:
            for r in m.get("resolutions", []):
                main_url = r.get("main_post_url", "")
                if main_url and url.startswith(main_url.rstrip("/")):
                    info = f"项目: {r.get('project', '')}\n决议: {r.get('decision', '')}\n轮次: {r.get('round', '')}\n预算: {r.get('budget', '')}\n备注: {r.get('note', '')}"
                    return info, m.get("date", "")
        # 如果主帖没匹配上，尝试匹配标题中的项目名称
        try:
            import re
            m = re.search(r"/t/([^/]+)/(\d+)", url)
            if m:
                slug = m.group(1).replace("-", " ").lower()
                for mt in sorted_meetings:
                    for r in mt.get("resolutions", []):
                        proj = r.get("project", "").lower()
                        if proj in slug or slug in proj:
                            info = f"项目: {r.get('project', '')}\n决议: {r.get('decision', '')}\n轮次: {r.get('round', '')}\n预算: {r.get('budget', '')}\n备注: {r.get('note', '')}"
                            return info, mt.get("date", "")
        except Exception:
            pass
        return "", ""

    def _ai_generate(self):
        """AI 生成草稿核心逻辑。"""
        if not self._ai_current_template:
            self.ai_status_label.config(text="❌ 未选择模板，请从下拉菜单选择模板")
            self.ai_status_label.config(fg=THEME["danger"])
            return

        # 检查 API Key
        api_key = self.ai_api_entry.get().strip()
        if not api_key:
            api_key = get_ai_api_key(self.ai_provider_var.get())
            if api_key:
                self.ai_api_key_var.set(api_key)
        if not api_key:
            self.ai_status_label.config(text="❌ API Key 为空，请填入后点击 💾 保存")
            self.ai_status_label.config(fg=THEME["danger"])
            return

        provider = self.ai_provider_var.get()
        model = self.ai_model_var.get()
        if not model:
            self.ai_status_label.config(text="❌ 请选择模型")
            return

        # 保存设置
        self.data["ai_provider"] = provider
        self.data["ai_model"] = model
        set_ai_api_key(api_key, provider)
        save_data(self.data)
        self.ai_api_status.config(text="✅ 已设置", fg=THEME["success"])

        url = self.ai_url_var.get().strip()
        if not url or not url.startswith("http"):
            self.ai_status_label.config(text="❌ 请输入有效的 Talk 帖子 URL")
            return

        self.ai_status_label.config(text="⏳ 正在获取帖子内容...")
        self.ai_gen_btn.config(state=tk.DISABLED, text="⏳ 获取中...")
        self.root.update()

        html = self._fetch_talk_post(url)
        if not html:
            self.ai_status_label.config(text="❌ 无法获取帖子内容")
            self.ai_gen_btn.config(state=tk.NORMAL, text="⚡ 生成草稿")
            return

        post = self._parse_talk_post(html)
        if not post.get("title"):
            self.ai_status_label.config(text="❌ 无法解析帖子内容")
            self.ai_gen_btn.config(state=tk.NORMAL, text="⚡ 生成草稿")
            return

        # 查找最新决议和知识库（从手动选取的会议纪要中读取）
        resolution_info = self.ai_meeting_info.get("1.0", tk.END).strip()
        meeting_date = self.ai_meeting_var.get().split()[0] if self.ai_meeting_var.get() else ""
        if not resolution_info or resolution_info.startswith("从上方") or resolution_info.startswith("请先从"):
            resolution_info = ""
            meeting_date = ""
        tone_knowledge = self.data.get("reply_tone_knowledge", "")

        # 读取补充内容
        supplement_content = self.ai_supplement_text.get("1.0", tk.END).strip()

        template_info = self._ai_current_template_info
        current_template = self._ai_current_template
        style_id = self._selected_style_id()

        # AI 调用与 Prompt 构建都放到后台线程：Prompt 构建可能联网
        # （预审参考帖、CKB 汇率），避免阻塞 UI
        is_pre_review = "预审" in current_template
        # 预审两阶段生成、zz 模板注入大段措辞 Skill，均显著拉长生成时间
        api_timeout = 300 if (is_pre_review or "[zz]" in current_template) else 180

        if is_pre_review:
            self.ai_status_label.config(text="⏳ 正在获取官方模板帖与主帖规则（预审参考，三步分析）...")
        elif resolution_info:
            self.ai_status_label.config(text=f"⏳ 已关联 {meeting_date} 决议，正在构建 Prompt...")
        else:
            self.ai_status_label.config(text="⏳ 正在构建 Prompt...")
        self.ai_gen_btn.config(state=tk.DISABLED, text="⏳ AI 生成中...")
        self.root.update()

        def _worker():
            try:
                prompt = self._build_kimi_prompt(current_template, template_info.get("desc", ""),
                                                  template_info.get("template", ""), post,
                                                  resolution_info=resolution_info, tone_knowledge=tone_knowledge,
                                                  supplement_content=supplement_content, style=style_id)
                self._ai_current_prompt = prompt
                self.root.after(0, lambda: self.ai_status_label.config(
                    text=f"⏳ 正在调用 {provider.upper()} AI 生成草稿（最长等待 {api_timeout} 秒）..."))
                draft, err = call_ai_api(provider, api_key, prompt, model, timeout=api_timeout)
                if err or not is_pre_review:
                    self.root.after(0, lambda: self._ai_generate_finish(draft, err, analysis=None))
                    return
                # 预审反馈：两阶段生成。第一阶段产出中文分析报告，
                # 第二阶段再按预审反馈模板格式化输出（Markdown）。
                analysis = draft
                try:
                    analysis_file = os.path.join(_BASE_DIR, "pre_review_analysis_last.md")
                    with open(analysis_file, "w", encoding="utf-8") as f:
                        f.write(f"# 预审分析报告：{post.get('title', '')}\n\n{analysis}")
                except Exception:
                    pass
                self.root.after(0, lambda: self.ai_status_label.config(
                    text="⏳ 分析完成，正在按预审反馈模板格式化输出（Markdown）..."))
                format_prompt = self._build_pre_review_format_prompt(
                    analysis, current_template, template_info.get("template", ""), post,
                    tone_knowledge=tone_knowledge, supplement_content=supplement_content)
                self._ai_current_prompt = prompt + "\n\n===== 阶段二格式化 Prompt =====\n\n" + format_prompt
                final_draft, err2 = call_ai_api(provider, api_key, format_prompt, model, timeout=api_timeout)
                self.root.after(0, lambda: self._ai_generate_finish(final_draft, err2, analysis=analysis))
            except Exception as e:
                err_msg = str(e)
                self.root.after(0, lambda: self._ai_generate_finish(None, f"后台生成异常: {err_msg}", analysis=None))

        threading.Thread(target=_worker, daemon=True).start()

    def _ai_generate_finish(self, draft, err, analysis=None):
        """AI 生成完成后的主线程收尾（由后台线程通过 root.after 调回）。"""
        if err or not draft:
            if analysis:
                # 阶段二失败时保底展示分析报告，不让白跑
                self.ai_result_text.config(state=tk.NORMAL)
                self.ai_result_text.delete("1.0", tk.END)
                self.ai_result_text.insert("1.0", analysis)
                self.ai_status_label.config(text=f"⚠️ 格式化输出失败（{err}），已显示分析报告，可点击重试")
                self.ai_status_label.config(fg=THEME["warning"])
            else:
                self.ai_status_label.config(text=f"❌ {err or 'AI 返回了空内容'}")
                self.ai_status_label.config(fg=THEME["danger"])
            self.ai_gen_btn.config(state=tk.NORMAL, text="⚡ 重试")
            return

        self.ai_result_text.config(state=tk.NORMAL)
        self.ai_result_text.delete("1.0", tk.END)
        self.ai_result_text.insert("1.0", draft)
        self.ai_result_text.config(state=tk.NORMAL)

        self.ai_status_label.config(fg=THEME["success"])
        self.ai_gen_btn.config(state=tk.NORMAL, text="🔄 重新生成")

        self.root.clipboard_clear()
        self.root.clipboard_append(draft)
        suffix = "（两阶段：分析+格式化）" if analysis else ""
        self.ai_status_label.config(text=f"✅ 草稿已生成并复制到剪贴板{suffix}！共 {len(draft)} 字")

    def _ai_copy(self):
        content = self.ai_result_text.get("1.0", tk.END).strip()
        if not content or content.startswith("选择"):
            self.ai_status_label.config(text="❌ 还没有内容")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.ai_status_label.config(text="✅ 已复制")

    # =================== 快捷翻译（Kimi WebBridge → 沉浸式翻译） ===================

    WEBBRIDGE_URL = "http://127.0.0.1:10086/command"
    IMMERSIVE_TRANSLATE_URL = "https://immersivetranslate.com/zh-Hans/translate/"

    def _webbridge_cmd(self, action, args=None, session="spark-translate", timeout=30):
        """调用本地 Kimi WebBridge 守护进程。返回 (data_dict, error_msg)。
        守护进程响应包一层 {"ok":..., "data":...}，这里统一解包出 data。
        Python urllib 直接以 UTF-8 字节发送 JSON，不存在 shell 中文损坏问题。"""
        allowed_actions = {"navigate", "fill", "click", "wait", "getText"}
        if action not in allowed_actions:
            return None, f"WebBridge action not allowed: {action}"
        if not webbridge_enabled():
            return None, "WebBridge 默认关闭；请在本机 .env 中设置 SPARK_ENABLE_WEBBRIDGE=1"
        token = get_webbridge_token()
        if not token:
            return None, "WebBridge 缺少 SPARK_WEBBRIDGE_TOKEN，已拒绝连接"
        payload = {"action": action, "args": args or {}, "session": session}
        req = urllib.request.Request(
            self.WEBBRIDGE_URL,
            data=jsonlib.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "X-Spark-Bridge-Token": token,
            },
            method="POST")
        try:
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    raise urllib.error.HTTPError(req.full_url, code, "redirect denied", headers, fp)

            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(req, timeout=min(max(timeout, 1), 60)) as resp:
                raw = resp.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    return None, "WebBridge response too large"
                body = jsonlib.loads(raw.decode("utf-8"))
            if isinstance(body, dict) and "data" in body:
                if body.get("ok") is False:
                    return None, str(body.get("error") or body)
                return body["data"], None
            return body, None
        except Exception as e:
            return None, str(e)

    def _webbridge_ensure_daemon(self):
        """WebBridge 守护进程未运行时尝试启动（已运行时为无操作）。"""
        exe = os.path.expanduser(r"~\.kimi-webbridge\bin\kimi-webbridge.exe")
        if not os.path.exists(exe):
            return False
        try:
            import subprocess
            subprocess.Popen([exe, "start"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            time.sleep(2)
            return True
        except Exception:
            return False

    def _ai_translate(self):
        """快捷翻译：通过 Kimi WebBridge 把当前草稿发送到沉浸式翻译文本页并完成翻译。"""
        content = self.ai_result_text.get("1.0", tk.END).strip()
        if not content or content.startswith("选择"):
            self.ai_status_label.config(text="❌ 还没有内容可翻译")
            return
        if not messagebox.askyesno(
                "发送到外部翻译服务",
                "草稿将被粘贴到 immersivetranslate.com 的在线翻译页面。\n\n"
                "请确认草稿中不含 API Key、密码、未公开个人信息或其它敏感内容。\n\n继续发送吗？"):
            self.ai_status_label.config(text="已取消发送")
            return
        self.ai_status_label.config(text="⏳ 正在通过 WebBridge 打开沉浸式翻译...")
        self.root.update()
        threading.Thread(target=self._ai_translate_worker, args=(content,), daemon=True).start()

    def _ai_translate_worker(self, content):
        def status(msg):
            self.root.after(0, lambda m=msg: self.ai_status_label.config(text=m))

        # 1. 打开沉浸式翻译文本页（WebBridge 未运行时先尝试启动守护进程）
        nav_args = {"url": self.IMMERSIVE_TRANSLATE_URL, "newTab": True,
                    "group_title": "Spark 草稿翻译"}
        r, err = self._webbridge_cmd("navigate", nav_args)
        if err and self._webbridge_ensure_daemon():
            r, err = self._webbridge_cmd("navigate", nav_args)
        if err or not r or not r.get("success"):
            status("❌ WebBridge 连接失败：请确认浏览器已安装并启用 Kimi WebBridge 扩展")
            return

        # 2. 等待页面加载就绪（实测：输入框是 contenteditable div，不在无障碍树中，
        #    用 CSS 选择器即可；通过 evaluate 探测其出现来判断加载完成）
        status("⏳ 翻译页面已打开，等待加载...")
        ready = False
        for _ in range(10):
            time.sleep(2)
            r, _ = self._webbridge_cmd(
                "evaluate",
                {"code": "(() => { const el = document.querySelector('[contenteditable=true]'); return el && el.offsetParent ? 'ready' : 'no'; })()"},
                timeout=20)
            if r and "ready" in str(r.get("value", "")):
                ready = True
                break
        if not ready:
            status("❌ 翻译页面加载超时，请稍后重试")
            return

        # 3. 填入草稿文本（fill 对 contenteditable 会触发正确的 input 事件）
        status("⏳ 正在粘贴草稿到翻译输入框...")
        r, err = self._webbridge_cmd("fill", {"selector": "[contenteditable=true]", "value": content}, timeout=30)
        if err or not r or not r.get("success"):
            status(f"❌ 填入翻译框失败: {err or '页面元素未就绪，请稍后重试'}")
            return

        # 4. 点击"翻译"按钮（实测按钮文本恰为"翻译"；找不到则说明页面已自动翻译）
        time.sleep(1)
        status("⏳ 正在触发翻译...")
        r, _ = self._webbridge_cmd(
            "evaluate",
            {"code": "(() => { const btns = [...document.querySelectorAll('button')]; const b = btns.find(x => x.innerText.trim() === '翻译'); if (b) { b.click(); return 'clicked'; } return 'auto'; })()"},
            timeout=20)
        clicked = r and "clicked" in str(r.get("value", ""))
        status("✅ 草稿已发送到沉浸式翻译" + ("，已触发翻译" if clicked else "（页面将自动翻译）") +
               "，请到浏览器「Spark 草稿翻译」标签组查看")

    # =================== Tab 1: 任务清单 ===================

    def _build_tab_tasks(self):
        top = tk.Frame(self.tab_tasks, bg=THEME["bg"])
        top.pack(fill=tk.X, pady=5)

        tk.Label(top, text="筛选", bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        filter_combo = ttk.Combobox(top, textvariable=self.task_filter,
                                     values=["全部", "未完成", "已完成"],
                                     width=10, state="readonly")
        filter_combo.pack(side=tk.LEFT, padx=5)
        filter_combo.bind("<<ComboboxSelected>>", lambda e: (self._selected_task_indices.clear(), self.refresh_task_list()))

        ttk.Button(top, text="➕ 添加", command=self.add_task_dialog).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="✅ 标记", command=self.toggle_task_status).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="🗑️ 删除", command=self.delete_task).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="📋 复制", command=self.copy_task).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="🔗 打开链接", command=self.open_task_link).pack(side=tk.LEFT, padx=3)

        # Batch operations row
        batch_frame = tk.Frame(self.tab_tasks, bg=THEME["bg"])
        batch_frame.pack(fill=tk.X, pady=2)
        self._select_all_btn = tk.Button(batch_frame, text="☐ 全选", bg=THEME["card"], fg=THEME["text"],
                                          activebackground=THEME["accent"], activeforeground=THEME["bg"],
                                          bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                                          command=self._select_all_tasks)
        self._select_all_btn.pack(side=tk.LEFT, padx=3)
        tk.Button(batch_frame, text="✅ 批量完成", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._batch_complete_tasks).pack(side=tk.LEFT, padx=3)
        tk.Button(batch_frame, text="🗑️ 批量删除", bg=THEME["danger"], fg=THEME["bg"],
                  activebackground="#ff6c6c", activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._batch_delete_tasks).pack(side=tk.LEFT, padx=3)
        self._batch_count_label = tk.Label(batch_frame, text="已选: 0", bg=THEME["bg"], fg=THEME["text_dim"],
                                           font=("Microsoft YaHei", 9))
        self._batch_count_label.pack(side=tk.LEFT, padx=10)

        # Initialize batch selection state
        self._selected_task_indices = set()

        quick = tk.Frame(self.tab_tasks, bg=THEME["bg"])
        quick.pack(fill=tk.X, pady=5)
        quick_tasks = [
            "更新 Notion Dashboard", "Nervos Talk 回帖", "Nervos Talk 发帖",
            "跟进周报", "中期审核", "准备结项", "撰写报告"
        ]
        for i, t in enumerate(quick_tasks):
            btn = tk.Button(quick, text=t, bg=THEME["card"], fg=THEME["text"],
                            activebackground=THEME["card_hover"], activeforeground=THEME["accent"],
                            bd=1, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                            command=lambda x=t: self.quick_add_task(x))
            btn.grid(row=i // 4, column=i % 4, padx=3, pady=3, sticky="ew")
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg=THEME["card_hover"]))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg=THEME["card"]))

        list_frame = tk.Frame(self.tab_tasks, bg=THEME["bg"])
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        cols = ("sel", "title", "status", "type", "project", "deadline", "created")
        self.task_tree = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="browse")
        self.task_tree.heading("sel", text="☐")
        self.task_tree.column("sel", width=40, anchor="center")
        self.task_tree.heading("title", text="任务名称")
        self.task_tree.column("title", width=240, anchor="w")
        self.task_tree.heading("status", text="状态")
        self.task_tree.column("status", width=55, anchor="center")
        self.task_tree.heading("type", text="类型")
        self.task_tree.column("type", width=90, anchor="w")
        self.task_tree.heading("project", text="项目")
        self.task_tree.column("project", width=110, anchor="w")
        self.task_tree.heading("deadline", text="截止")
        self.task_tree.column("deadline", width=85, anchor="center")
        self.task_tree.heading("created", text="创建")
        self.task_tree.column("created", width=85, anchor="center")

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=vsb.set)
        self.task_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.task_detail = scrolledtext.ScrolledText(self.tab_tasks, height=5,
            font=("Microsoft YaHei", 10), bg=THEME["card"], fg=THEME["text"],
            insertbackground=THEME["text"], relief=tk.FLAT)
        self.task_detail.pack(fill=tk.X, pady=5)
        self.task_detail.insert("1.0", "点击任务查看详情...")
        self.task_detail.config(state=tk.DISABLED)

        # 双击任务名称打开链接
        self.task_tree.bind("<<TreeviewSelect>>", self.on_task_select)
        self.task_tree.bind("<ButtonRelease-1>", self._on_task_click)
        self.task_tree.bind("<Double-Button-1>", self._on_task_double_click)

    # =================== Tab 2: 项目看板 ===================

    def _build_tab_projects(self):
        # 初始化列表框数据映射（解决白名单从缓存读取不一致的问题）
        self._lb_post_map = {}  # key -> {index: post_dict}

        # Refresh button row
        refresh_row = tk.Frame(self.tab_projects, bg=THEME["bg"])
        refresh_row.pack(fill=tk.X, pady=5)
        tk.Label(refresh_row, text="Data Sync", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT)
        self._refresh_btn = tk.Button(refresh_row, text="Refresh", bg=THEME["card"], fg=THEME["text"],
                                       activebackground=THEME["accent"], activeforeground=THEME["bg"],
                                       bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                                       command=self._refresh_project_data)
        self._refresh_btn.pack(side=tk.LEFT, padx=8)
        self._last_refresh_label = tk.Label(refresh_row, text="Last refresh: never", bg=THEME["bg"], fg=THEME["text_dim"],
                                             font=("Microsoft YaHei", 9))
        self._last_refresh_label.pack(side=tk.LEFT)
        tk.Button(refresh_row, text="Manual Add", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._add_manual_project).pack(side=tk.RIGHT, padx=8)

        # 关联模式状态栏（默认隐藏）
        self._link_bar = tk.Frame(self.tab_projects, bg=THEME["bg"])
        self._link_bar.pack(fill=tk.X, pady=0)
        self._link_bar.pack_forget()  # 默认隐藏
        self._link_status_label = tk.Label(self._link_bar, text="", bg=THEME["bg"], fg=THEME["accent"],
                                           font=("Microsoft YaHei", 10, "bold"))
        self._link_status_label.pack(side=tk.LEFT, padx=8)
        tk.Button(self._link_bar, text="✅ 关联", bg=THEME["success"], fg=THEME["bg"],
                  activebackground="#a6e3a1", activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._link_selected_post).pack(side=tk.LEFT, padx=5)
        tk.Button(self._link_bar, text="❌ 取消", bg=THEME["danger"], fg=THEME["bg"],
                  activebackground="#ff6c6c", activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._cancel_link_mode).pack(side=tk.LEFT, padx=5)

        # Stats overview
        self._stats_frame = tk.Frame(self.tab_projects, bg=THEME["bg"])
        self._stats_frame.pack(fill=tk.X, pady=5)
        self._update_stats_display()

        # 白名单全局操作栏
        self._whitelist_bar = tk.Frame(self.tab_projects, bg=THEME["bg"])
        self._whitelist_bar.pack(fill=tk.X, pady=5, padx=5)
        tk.Label(self._whitelist_bar, text="白名单操作:", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT, padx=5)
        self._whitelist_count_label = tk.Label(self._whitelist_bar, text="已选中 0 个帖子", bg=THEME["bg"],
                                               fg=THEME["text_dim"], font=("Microsoft YaHei", 9))
        self._whitelist_count_label.pack(side=tk.LEFT, padx=5)
        self._whitelist_target_var = tk.StringVar(value="选择目标标签页...")
        self._whitelist_combo = ttk.Combobox(self._whitelist_bar, textvariable=self._whitelist_target_var,
                                             values=["Pre-review", "Submitted", "Pending", "In-Progress", "Rejection", "Completion", "Closure"],
                                             state="readonly", width=18, font=("Microsoft YaHei", 9))
        self._whitelist_combo.pack(side=tk.LEFT, padx=2)
        self._whitelist_btn = tk.Button(self._whitelist_bar, text="➕ 添加", bg=THEME["accent"], fg=THEME["bg"],
                                        activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                                        bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                                        command=self._toggle_whitelist, state=tk.DISABLED)
        self._whitelist_btn.pack(side=tk.LEFT, padx=2)
        self._whitelist_status_label = tk.Label(self._whitelist_bar, text="", bg=THEME["bg"], fg=THEME["text_dim"],
                                                font=("Microsoft YaHei", 9))
        self._whitelist_status_label.pack(side=tk.LEFT, padx=5)

        # Secondary Notebook (8 tabs)
        self._projects_notebook = ttk.Notebook(self.tab_projects)
        self._projects_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._project_tab_frames = {}
        self._project_tab_listboxes = {}

        tab_configs = [
            ("Official", "Official"),
            ("Spark-Program", "Spark-Program"),
            ("Submitted", "Submitted"),
            ("Pending", "Pending"),
            ("In-Progress", "In-Progress"),
            ("Rejection", "Rejection"),
            ("Completion", "Completion"),
            ("Closure", "Closure"),
        ]

        for key, label in tab_configs:
            frame = tk.Frame(self._projects_notebook, bg=THEME["bg"])
            self._projects_notebook.add(frame, text=label)
            self._project_tab_frames[key] = frame

            if key == "Spark-Program":
                pre_frame = tk.LabelFrame(frame, text="  Pre-review Proposals (AI)  ", bg=THEME["bg"],
                                          fg=THEME["accent"], font=("Microsoft YaHei", 10, "bold"),
                                          bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                          highlightthickness=1)
                pre_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
                pre_lb = tk.Listbox(pre_frame, font=("Microsoft YaHei", 10), bg=THEME["card"], fg=THEME["text"],
                                    selectmode=tk.EXTENDED,
                                    selectbackground=THEME["accent"], selectforeground=THEME["bg"],
                                    bd=0, relief=tk.FLAT, highlightthickness=0)
                pre_lb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
                pre_lb.bind("<Double-Button-1>", lambda e, lb=pre_lb, k="pre": self._open_project_url_by_listbox(e, lb, k))
                pre_lb.bind("<<ListboxSelect>>", lambda e: self._on_board_select())
                self._project_tab_listboxes[f"{key}_pre"] = pre_lb

                non_frame = tk.LabelFrame(frame, text="  Non-Proposal Posts (AI)  ", bg=THEME["bg"],
                                          fg=THEME["warning"], font=("Microsoft YaHei", 10, "bold"),
                                          bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                          highlightthickness=1)
                non_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
                non_lb = tk.Listbox(non_frame, font=("Microsoft YaHei", 10), bg=THEME["card"], fg=THEME["text"],
                                    selectmode=tk.EXTENDED,
                                    selectbackground=THEME["accent"], selectforeground=THEME["bg"],
                                    bd=0, relief=tk.FLAT, highlightthickness=0)
                non_lb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
                non_lb.bind("<Double-Button-1>", lambda e, lb=non_lb, k="non": self._open_project_url_by_listbox(e, lb, k))
                non_lb.bind("<<ListboxSelect>>", lambda e: self._on_board_select())
                self._project_tab_listboxes[f"{key}_non"] = non_lb
            elif key == "Official":
                lb = tk.Listbox(frame, font=("Microsoft YaHei", 10), bg=THEME["card"], fg=THEME["text"],
                                selectmode=tk.EXTENDED,
                                selectbackground=THEME["accent"], selectforeground=THEME["bg"],
                                bd=0, relief=tk.FLAT, highlightthickness=0)
                lb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
                lb.bind("<Double-Button-1>", lambda e, lb=lb, k="official": self._open_project_url_by_listbox(e, lb, k))
                lb.bind("<<ListboxSelect>>", lambda e: self._on_board_select())
                self._project_tab_listboxes[key] = lb
            else:
                lb = tk.Listbox(frame, font=("Microsoft YaHei", 10), bg=THEME["card"], fg=THEME["text"],
                                selectmode=tk.EXTENDED,
                                selectbackground=THEME["accent"], selectforeground=THEME["bg"],
                                bd=0, relief=tk.FLAT, highlightthickness=0)
                lb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
                lb.bind("<Double-Button-1>", lambda e, lb=lb, k=key: self._open_project_url_by_listbox(e, lb, k))
                lb.bind("<<ListboxSelect>>", lambda e: self._on_board_select())
                self._project_tab_listboxes[key] = lb

    def _update_stats_display(self):
        for w in self._stats_frame.winfo_children():
            w.destroy()
        cache = load_project_cache()
        counts = {"total": 0, "official": 0, "spark": 0, "submitted": 0, "pending": 0, "in_progress": 0, "rejection": 0, "completion": 0, "closure": 0}
        if cache and "classified" in cache:
            c = cache["classified"]
            counts["official"] = len(c.get("spark_program", {}).get("official", []))
            counts["spark"] = len(c.get("spark_program", {}).get("pre_review", [])) + len(c.get("spark_program", {}).get("non_proposal", []))
            counts["submitted"] = len(c.get("submitted", []))
            counts["pending"] = len(c.get("pending", []))
            counts["in_progress"] = len(c.get("in_progress", []))
            counts["rejection"] = len(c.get("rejection", []))
            counts["completion"] = len(c.get("completion", []))
            counts["closure"] = len(c.get("closure", []))
            counts["total"] = counts["official"] + counts["spark"] + counts["submitted"] + counts["pending"] + counts["in_progress"] + counts["rejection"] + counts["completion"] + counts["closure"]
        stat_data = [
            ("Total", str(counts["total"])),
            ("Official", str(counts["official"])),
            ("Spark", str(counts["spark"])),
            ("Submitted", str(counts["submitted"])),
            ("Pending", str(counts["pending"])),
            ("In-Progress", str(counts["in_progress"])),
            ("Rejection", str(counts["rejection"])),
            ("Completion", str(counts["completion"])),
            ("Closure", str(counts["closure"])),
        ]
        for label, num in stat_data:
            card = tk.Frame(self._stats_frame, bg=THEME["card"], bd=1, relief=tk.FLAT)
            card.pack(side=tk.LEFT, padx=2, pady=3, fill=tk.Y)
            tk.Label(card, text=num, bg=THEME["card"], fg=THEME["accent"],
                     font=("Microsoft YaHei", 12, "bold")).pack(padx=10, pady=(6, 0))
            tk.Label(card, text=label, bg=THEME["card"], fg=THEME["text_dim"],
                     font=("Microsoft YaHei", 8)).pack(padx=10, pady=(0, 6))

    def _update_project_tab_display(self, cache):
        """更新看板各标签页显示。"""
        if not cache or "classified" not in cache:
            return
        c = cache["classified"]
        official_posts = c.get("spark_program", {}).get("official", [])
        self._update_listbox("Official", official_posts)
        pre_posts = c.get("spark_program", {}).get("pre_review", [])
        non_posts = c.get("spark_program", {}).get("non_proposal", [])
        self._update_listbox("Spark-Program_pre", pre_posts)
        self._update_listbox("Spark-Program_non", non_posts)
        for key in ["Submitted", "Pending", "In-Progress", "Rejection", "Completion", "Closure"]:
            posts = c.get(key.lower().replace("-", "_"), [])
            self._update_listbox(key, posts)

    def _update_listbox(self, key, posts):
        lb = self._project_tab_listboxes.get(key)
        if not lb:
            return
        lb.delete(0, tk.END)
        self._lb_post_map[key] = {}  # 清空并重建映射
        whitelist = self.data.get("board_whitelist", {})
        for i, post in enumerate(posts):
            title = post.get("title", "Unknown")
            author = post.get("original_poster_username") or post.get("author", "Unknown")
            status = post.get("ai_classified", "")
            post_id = str(post.get("id", ""))
            wl_target = whitelist.get(post_id, "")
            if wl_target:
                prefix = f"[W→{wl_target}] "
            elif key == "Official":
                prefix = "[Official] "
            elif status == "proposal":
                prefix = "[P] "
            elif status == "non_proposal":
                prefix = "[X] "
            else:
                prefix = ""
            lb.insert(tk.END, f"{prefix}{title}  ({author})")
            self._lb_post_map[key][i] = post  # 存储帖子数据映射

    def _open_project_url_by_listbox(self, event, listbox, key):
        """双击打开帖子URL。在EXTENDED模式下使用event.y获取精确位置。"""
        idx = listbox.nearest(event.y)
        map_key = "Spark-Program_" + key if key in ["pre", "non"] else key
        posts_map = self._lb_post_map.get(map_key, {})
        post = posts_map.get(idx)
        if post:
            url = post.get("url", "")
            if url:
                webbrowser.open(url)

    def _on_board_select(self):
        """处理所有看板列表的选中事件，统计选中数量并更新操作栏状态。"""
        total = 0
        for lb in self._project_tab_listboxes.values():
            total += len(lb.curselection())
        if total > 0:
            self._whitelist_count_label.config(text=f"已选中 {total} 个帖子", fg=THEME["accent"])
            self._whitelist_btn.config(state=tk.NORMAL)
        else:
            self._whitelist_count_label.config(text="已选中 0 个帖子", fg=THEME["text_dim"])
            self._whitelist_btn.config(state=tk.DISABLED)

    def _toggle_whitelist(self):
        """批量添加白名单：将所有选中帖子添加到目标标签页。"""
        TARGET_MAP = {"Pre-review": "pre_review", "Submitted": "submitted", "Pending": "pending",
                        "In-Progress": "in_progress", "Rejection": "rejection", "Completion": "completion", "Closure": "closure"}
        DISPLAY_MAP = {"pre_review": "Pre-review", "submitted": "Submitted", "pending": "Pending",
                       "in_progress": "In-Progress", "rejection": "Rejection", "completion": "Completion", "closure": "Closure"}
        
        target_display = self._whitelist_target_var.get()
        target = TARGET_MAP.get(target_display, "")
        if not target or target_display == "选择目标标签页...":
            messagebox.showinfo("提示", "请先选择目标标签页")
            return
        
        # 收集所有选中的帖子
        selected_posts = []
        for key, lb in self._project_tab_listboxes.items():
            for idx in lb.curselection():
                posts_map = self._lb_post_map.get(key, {})
                post = posts_map.get(idx)
                if post:
                    selected_posts.append(post)
        
        if not selected_posts:
            messagebox.showinfo("提示", "请先在看板中选中帖子")
            return
        
        whitelist = self.data.get("board_whitelist", {})
        added_count = 0
        skipped_count = 0
        added_posts = []
        for post in selected_posts:
            post_id = str(post.get("id", ""))
            if not post_id:
                continue
            if post_id in whitelist:
                skipped_count += 1
                continue
            whitelist[post_id] = target
            added_posts.append(post)
            added_count += 1

        if added_count > 0:
            save_data(self.data)
            self._whitelist_status_label.config(text=f"已添加 {added_count} 个帖子 → {DISPLAY_MAP.get(target, target)}", fg=THEME["success"])
            # In-Progress 新增项目：四项任务继续创建；其中两项已接入自动验证。
            notion_tasks = 0
            if target == "in_progress":
                notion_tasks = self._create_inprogress_tasks_for(added_posts)
            self._do_refresh(silent=True)
            msg = f"已成功添加 {added_count} 个帖子到白名单！\n\n目标标签页: {DISPLAY_MAP.get(target, target)}\n"
            if skipped_count > 0:
                msg += f"\n（跳过 {skipped_count} 个已在白名单中的帖子）"
            msg += "\n帖子已添加到目标标签页，不再显示在原位置。\n请切换到目标标签页查看。"
            if notion_tasks:
                msg += f"\n\n已同步创建 {notion_tasks} 项 Notion 更新任务（任务页查看，附对应链接）。"
            messagebox.showinfo("✓ 白名单添加成功", msg)
        else:
            if skipped_count > 0:
                messagebox.showinfo("提示", f"选中的 {skipped_count} 个帖子已在白名单中")
            else:
                messagebox.showinfo("提示", "没有可添加的帖子")

    def _create_inprogress_tasks_for(self, posts):
        """为新项目创建待执行任务；这里只记 queued，不代表 Notion 已写入。"""
        if not posts:
            return 0
        queued_set = set(self.data.setdefault("notion_tasks_queued", []))
        completed_set = set(self.data.setdefault("notion_tasks_created", []))
        jobs = self.data.setdefault("notion_sync_jobs", {})
        today = datetime.date.today().strftime("%Y-%m-%d")
        n = 0
        for post in posts:
            pid = str(post.get("id", ""))
            if not pid or pid in queued_set or pid in completed_set:
                continue
            queued_set.add(pid)
            proj = post.get("title", "")
            jobs[pid] = {
                "idempotency_key": f"nervos:{pid}:in-progress:v1",
                "project": proj,
                "topic_url": post.get("url", ""),
                "status": "queued",
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "steps": {
                    "dashboard": {"status": "planned", "notion_page_ids": []},
                    "project_list": {"status": "pending", "notion_page_ids": []},
                    "fund_pool": {"status": "pending", "notion_page_ids": []},
                    "contacts": {"status": "planned", "notion_page_ids": []},
                },
            }
            for title, link_key in INPROGRESS_NOTION_TASKS:
                task = {
                    "title": title, "type": "更新 Notion Dashboard",
                    "project": proj, "deadline": "", "note": "",
                    "link": QUICK_LINKS.get(link_key, ""),
                    "created": today, "done": False,
                    "nervos_topic_id": pid,
                    "automation_step": NOTION_ALL_STEPS[title],
                }
                if title in NOTION_REQUIRED_STEPS:
                    task["notion_step"] = NOTION_REQUIRED_STEPS[title]
                else:
                    task["automation_status"] = "planned"
                self.data.setdefault("tasks", []).append(task)
                n += 1
        if n:
            self.data["notion_tasks_queued"] = sorted(queued_set)
            save_data(self.data)
            if hasattr(self, "task_tree"):
                self._selected_task_indices.clear()
                self.refresh_task_list()
        return n

    def _seed_and_sync_inprogress_tasks(self, classified):
        """刷新后调用：检测新进入 In-Progress 的项目并自动建任务。
        升级后首次刷新只“播种”现状（记录已有项目，不补建历史任务）。
        返回本次自动创建的任务数。"""
        current = [p for p in (classified or {}).get("in_progress", []) if str(p.get("id", ""))]
        if not self.data.get("notion_sync_seeded"):
            queued_set = set(self.data.setdefault("notion_tasks_queued", []))
            queued_set.update(str(p["id"]) for p in current)
            self.data["notion_tasks_queued"] = sorted(queued_set)
            self.data["notion_sync_seeded"] = True
            save_data(self.data)
            return 0
        known = set(self.data.get("notion_tasks_queued", [])) | set(self.data.get("notion_tasks_created", []))
        new_posts = [p for p in current if str(p["id"]) not in known]
        return self._create_inprogress_tasks_for(new_posts)

    def mark_notion_step_verified(self, post_id, step, notion_page_ids=None):
        """仅供 Notion 写入器在回读核验成功后调用；失败或仅发起请求时不得调用。"""
        pid = str(post_id)
        if step not in {"project_list", "fund_pool"}:
            raise ValueError(f"Unknown Notion step: {step}")
        jobs = self.data.setdefault("notion_sync_jobs", {})
        job = jobs.get(pid)
        if not job:
            raise ValueError(f"No queued Notion sync job for topic {pid}")
        verified_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        step_state = job.setdefault("steps", {}).setdefault(step, {})
        step_state.update({
            "status": "verified",
            "verified_at": verified_at,
            "notion_page_ids": sorted(set(str(x) for x in (notion_page_ids or []) if str(x))),
        })
        for task in self.data.get("tasks", []):
            if str(task.get("nervos_topic_id", "")) == pid and task.get("notion_step") == step:
                task["done"] = True
                task["completed_at"] = verified_at
        required_verified = all(
            job.get("steps", {}).get(required, {}).get("status") == "verified"
            for required in ("project_list", "fund_pool")
        )
        if required_verified:
            job["status"] = "core_verified"
            job["core_verified_at"] = verified_at
            completed = set(self.data.setdefault("notion_tasks_created", []))
            completed.add(pid)
            self.data["notion_tasks_created"] = sorted(completed)
        else:
            job["status"] = "partial"
        save_data(self.data)

    def _apply_board_whitelist(self, classified):
        """应用白名单：已白名单的帖子从原列表中移除，添加到目标分类。"""
        whitelist = self.data.get("board_whitelist", {})
        if not whitelist:
            return classified
        
        # 建立帖子ID到所有分类的映射
        all_posts_map = {}
        for key in ["pre_review", "non_proposal", "official"]:
            for post in classified.get("spark_program", {}).get(key, []):
                all_posts_map[str(post.get("id", ""))] = (post, "spark_program", key)
        for key in ["submitted", "pending", "in_progress", "rejection", "completion", "closure"]:
            for post in classified.get(key, []):
                all_posts_map[str(post.get("id", ""))] = (post, key, None)
        
        # 应用白名单：从原分类移除，添加到目标分类
        for post_id, target in whitelist.items():
            if post_id in all_posts_map:
                post, current_cat, current_sub = all_posts_map[post_id]
                # 从当前分类中移除
                if current_cat == "spark_program" and current_sub:
                    try:
                        classified["spark_program"][current_sub].remove(post)
                    except ValueError:
                        pass
                elif current_cat != "spark_program":
                    try:
                        classified[current_cat].remove(post)
                    except ValueError:
                        pass
                # 添加到目标分类
                if target in ["pre_review", "non_proposal", "official"]:
                    classified["spark_program"].setdefault(target, [])
                    if post not in classified["spark_program"][target]:
                        classified["spark_program"][target].append(post)
                elif target in ["submitted", "pending", "in_progress", "rejection", "completion", "closure"]:
                    if post not in classified.get(target, []):
                        classified.setdefault(target, [])
                        classified[target].append(post)
        return classified

    def _build_tab_timeline(self):
        """时间线标签页：按首次发帖时间展示所有 Spark Program 相关帖子。"""
        top = tk.Frame(self.tab_timeline, bg=THEME["bg"])
        top.pack(fill=tk.X, padx=5, pady=5)
        tk.Label(top, text="📅 按首次发帖时间排序（2026年4月起）", bg=THEME["bg"],
                 fg=THEME["accent"], font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT)
        tk.Button(top, text="🔄 刷新", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._refresh_timeline).pack(side=tk.RIGHT, padx=5)

        self.timeline_listbox = tk.Listbox(self.tab_timeline, font=("Microsoft YaHei", 10),
                                            bg=THEME["card"], fg=THEME["text"],
                                            selectbackground=THEME["accent"],
                                            selectforeground=THEME["bg"],
                                            bd=0, relief=tk.FLAT, highlightthickness=0)
        self.timeline_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.timeline_listbox.bind("<Double-Button-1>", self._open_timeline_url)
        self._timeline_post_map = {}
        self._refresh_timeline()

    def _refresh_timeline(self):
        """刷新时间线列表。"""
        self.timeline_listbox.delete(0, tk.END)
        self._timeline_post_map.clear()
        cache = load_project_cache()
        posts = []
        seen_ids = set()

        def add_posts(post_list):
            for p in post_list:
                pid = p.get("id")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    posts.append(p)

        if cache and "classified" in cache:
            c = cache["classified"]
            sp = c.get("spark_program", {})
            add_posts(sp.get("official", []))
            add_posts(sp.get("pre_review", []))
            add_posts(sp.get("non_proposal", []))
            for key in ["submitted", "pending", "in_progress", "rejection", "completion", "closure"]:
                add_posts(c.get(key, []))

        filtered = [p for p in posts if p.get("created_at", "") >= "2026-04-01"]
        filtered.sort(key=lambda p: p.get("created_at", ""))

        for i, post in enumerate(filtered):
            date = post.get("created_at", "")[:10]
            title = post.get("title", "Unknown")
            author = post.get("original_poster_username") or post.get("author", "Unknown")
            display = f"📅 {date} | {title}  ({author})"
            self.timeline_listbox.insert(tk.END, display)
            self._timeline_post_map[i] = post

    def _open_timeline_url(self, event):
        idx = self.timeline_listbox.nearest(event.y)
        post = self._timeline_post_map.get(idx)
        if post:
            url = post.get("url", "")
            if url:
                webbrowser.open(url)

    def _build_tab_templates(self):
        self._template_editing_name = None

        left = tk.Frame(self.tab_templates, bg=THEME["bg"])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

        tk.Label(left, text="选择模板", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))
        self.template_listbox = tk.Listbox(left, width=24, font=("Microsoft YaHei", 11),
            selectmode=tk.SINGLE, bg=THEME["card"], fg=THEME["text"],
            selectbackground=THEME["accent"], selectforeground=THEME["bg"],
            bd=0, relief=tk.FLAT, highlightthickness=0)
        for name in TEMPLATES.keys():
            self.template_listbox.insert(tk.END, name)
        self.template_listbox.pack(fill=tk.Y, expand=True)
        self.template_listbox.bind("<<ListboxSelect>>", self.on_template_select)

        right = tk.Frame(self.tab_templates, bg=THEME["bg"])
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.template_desc = tk.Label(right, text="", bg=THEME["bg"], fg=THEME["text_dim"],
                                      font=("Microsoft YaHei", 10, "italic"))
        self.template_desc.pack(anchor="w", pady=2)

        self.template_text = scrolledtext.ScrolledText(right, font=("Microsoft YaHei", 10),
            wrap=tk.WORD, bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
            relief=tk.FLAT, bd=2)
        self.template_text.pack(fill=tk.BOTH, expand=True)
        self.template_text.insert("1.0", "← 从左侧选择模板")
        self.template_text.config(state=tk.DISABLED)

        btn_frame = tk.Frame(right, bg=THEME["bg"])
        btn_frame.pack(fill=tk.X, pady=8)
        self._make_action_btn(btn_frame, "📋 复制", self.copy_template)
        self._make_action_btn(btn_frame, "✏️ 编辑", self.edit_template)
        self._make_action_btn(btn_frame, "💾 保存", self.save_template)
        self._make_action_btn(btn_frame, "📝 创建任务", self.create_task_from_template)
        self._make_ai_btn(btn_frame, "🤖 去起草", self._switch_to_ai_tab)

    def _make_ai_btn(self, parent, text, cmd):
        btn = tk.Button(parent, text=text, bg=THEME["accent"], fg=THEME["bg"],
                        activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                        bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 10, "bold"))
        btn.pack(side=tk.LEFT, padx=4)
        btn.config(command=cmd)
        btn.bind("<Enter>", lambda e: btn.config(bg=THEME["accent2"]))
        btn.bind("<Leave>", lambda e: btn.config(bg=THEME["accent"]))
        return btn

    def _make_action_btn(self, parent, text, cmd):
        btn = tk.Button(parent, text=text, bg=THEME["card"], fg=THEME["text"],
                        activebackground=THEME["accent"], activeforeground=THEME["bg"],
                        bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 10))
        btn.pack(side=tk.LEFT, padx=4)
        btn.config(command=cmd)
        btn.bind("<Enter>", lambda e: btn.config(bg=THEME["card_hover"]))
        btn.bind("<Leave>", lambda e: btn.config(bg=THEME["card"]))
        return btn

    # =================== Tab 4: 快速链接 ===================

    def _build_tab_links(self):
        ql = tk.LabelFrame(self.tab_links, text="  常用链接  ", bg=THEME["bg"],
                           fg=THEME["accent"], font=("Microsoft YaHei", 10, "bold"),
                           bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                           highlightthickness=1)
        ql.pack(fill=tk.X, pady=5)
        for name, url in QUICK_LINKS.items():
            r = tk.Frame(ql, bg=THEME["card"])
            r.pack(fill=tk.X, padx=5, pady=3)
            tk.Label(r, text=name, bg=THEME["card"], fg=THEME["text"],
                     font=("Microsoft YaHei", 11), width=22, anchor="w").pack(side=tk.LEFT, padx=8)
            tk.Button(r, text="打开", bg=THEME["card_hover"], fg=THEME["text"],
                      activebackground=THEME["accent"], activeforeground=THEME["bg"],
                      bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                      command=lambda u=url: webbrowser.open(u)).pack(side=tk.RIGHT, padx=8)

    # =================== Tab 5.5: 知识库 ===================

    def _build_tab_knowledge(self):
        """知识库标签页：📚 内容浏览（knowledge/ 目录）+ 🎭 人物角色 + 🧠 措辞知识库。"""
        kb_nb = ttk.Notebook(self.tab_knowledge)
        kb_nb.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        self._kb_tab_content = tk.Frame(kb_nb, bg=THEME["bg"])
        self._kb_tab_chars = tk.Frame(kb_nb, bg=THEME["bg"])
        self._kb_tab_tone = tk.Frame(kb_nb, bg=THEME["bg"])
        kb_nb.add(self._kb_tab_content, text=" 📚 知识库内容 ")
        kb_nb.add(self._kb_tab_chars, text=" 🎭 人物角色 ")
        kb_nb.add(self._kb_tab_tone, text=" 🧠 措辞知识库 ")

        self._build_kb_content_browser(self._kb_tab_content)
        self._build_kb_characters(self._kb_tab_chars)
        self._build_kb_tone(self._kb_tab_tone)

    # ---------- 子页 1：知识库内容浏览 ----------

    def _build_kb_content_browser(self, parent):
        """左侧文件列表 + 右侧内容查看/编辑，直接浏览 knowledge/ 下的规则、判例与技术资料。"""
        # 顶部操作栏
        top = tk.Frame(parent, bg=THEME["bg"])
        top.pack(fill=tk.X, padx=5, pady=4)
        tk.Label(top, text="📚 知识库文件", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 11, "bold")).pack(side=tk.LEFT)
        tk.Label(top, text="AI 起草时按需召回这些文件中的知识块", bg=THEME["bg"],
                 fg=THEME["text_dim"], font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=8)
        tk.Button(top, text="🔄 刷新", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._kb_refresh_files).pack(side=tk.RIGHT, padx=3)
        tk.Button(top, text="📂 打开目录", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=lambda: os.startfile(KNOWLEDGE_DIR)).pack(side=tk.RIGHT, padx=3)

        # 标签筛选行：全部文件 / 全部知识块 / 各标签
        filter_row = tk.Frame(parent, bg=THEME["bg"])
        filter_row.pack(fill=tk.X, padx=5, pady=(0, 3))
        tk.Label(filter_row, text="🏷 筛选:", bg=THEME["bg"], fg=THEME["accent2"],
                 font=("Microsoft YaHei", 9, "bold")).pack(side=tk.LEFT)
        self.kb_tag_var = tk.StringVar(value="全部文件")
        self.kb_tag_combo = ttk.Combobox(filter_row, textvariable=self.kb_tag_var, width=18,
                                          state="readonly", font=("Microsoft YaHei", 9),
                                          postcommand=self._kb_refresh_tag_options)
        self.kb_tag_combo.pack(side=tk.LEFT, padx=4)
        self.kb_tag_combo.bind("<<ComboboxSelected>>", lambda e: self._kb_apply_filter())
        tk.Label(filter_row, text="（选标签后左侧切换为该标签下的知识块）", bg=THEME["bg"],
                 fg=THEME["text_dim"], font=("Microsoft YaHei", 8)).pack(side=tk.LEFT)

        # 主体：左列表 + 右内容
        body = tk.Frame(parent, bg=THEME["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

        left = tk.Frame(body, bg=THEME["bg"])
        left.pack(side=tk.LEFT, fill=tk.Y)
        self.kb_file_list = tk.Listbox(left, width=34, font=("Microsoft YaHei", 9),
                                       bg=THEME["card"], fg=THEME["text"],
                                       selectbackground=THEME["accent"], selectforeground=THEME["bg"],
                                       relief=tk.FLAT, bd=1, activestyle="none", exportselection=False)
        kb_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.kb_file_list.yview)
        self.kb_file_list.config(yscrollcommand=kb_scroll.set)
        self.kb_file_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        kb_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.kb_file_list.bind("<<ListboxSelect>>", self._kb_show_file)

        right = tk.Frame(body, bg=THEME["bg"])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))
        # 右侧标题 + 编辑/保存
        rtop = tk.Frame(right, bg=THEME["bg"])
        rtop.pack(fill=tk.X)
        self.kb_file_label = tk.Label(rtop, text="← 选择左侧文件查看内容", bg=THEME["bg"],
                                      fg=THEME["accent2"], font=("Microsoft YaHei", 9, "bold"))
        self.kb_file_label.pack(side=tk.LEFT)
        self.kb_save_btn = tk.Button(rtop, text="💾 保存", bg=THEME["success"], fg=THEME["bg"],
                                     activebackground=THEME["accent"], activeforeground=THEME["bg"],
                                     bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                                     command=self._kb_save_file)
        self.kb_edit_btn = tk.Button(rtop, text="✏️ 编辑", bg=THEME["card"], fg=THEME["text"],
                                     activebackground=THEME["accent"], activeforeground=THEME["bg"],
                                     bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                                     command=self._kb_edit_file)

        self.kb_content_text = scrolledtext.ScrolledText(right, font=("Microsoft YaHei", 9), wrap=tk.WORD,
                                                         bg=THEME["card"], fg=THEME["text"],
                                                         insertbackground=THEME["text"],
                                                         relief=tk.FLAT, bd=2)
        self.kb_content_text.pack(fill=tk.BOTH, expand=True, pady=(3, 0))
        self.kb_content_text.config(state=tk.DISABLED)

        # 底部状态
        self.kb_status_label = tk.Label(parent, text="", bg=THEME["bg"], fg=THEME["text_dim"],
                                        font=("Microsoft YaHei", 9))
        self.kb_status_label.pack(anchor="w", padx=5, pady=2)

        self._kb_current_file = None
        self._kb_files = []
        self._kb_mode = "files"      # files | chunks
        self._kb_chunks = []         # 当前筛选出的知识块
        self._kb_refresh_files()

    def _kb_refresh_tag_options(self):
        """标签下拉的候选值：全部文件 / 全部知识块 / 各标签（按块数倒序）。"""
        tag_count = {}
        for c in load_knowledge_chunks():
            for tag in c["tags"]:
                tag_count[tag] = tag_count.get(tag, 0) + 1
        tags = sorted(tag_count, key=lambda t: (-tag_count[t], t))
        values = ["全部文件", "全部知识块"] + [f"🏷 {t} ({tag_count[t]})" for t in tags]
        self.kb_tag_combo.config(values=values)

    def _kb_apply_filter(self):
        """根据筛选下拉切换 文件浏览 / 知识块浏览 模式。"""
        sel = self.kb_tag_var.get()
        if sel == "全部文件":
            self._kb_mode = "files"
            self._kb_refresh_files()
            self.kb_file_label.config(text="← 选择左侧文件查看内容")
            return
        self._kb_mode = "chunks"
        tag = sel[2:].split(" (")[0] if sel.startswith("🏷 ") else None
        chunks = load_knowledge_chunks()
        if tag:
            self._kb_chunks = [c for c in chunks if tag in c["tags"]]
        else:
            self._kb_chunks = list(chunks)
        # 按文件+标题排序，方便浏览
        self._kb_chunks.sort(key=lambda c: (c["file"], c["heading"]))
        self.kb_file_list.delete(0, tk.END)
        for c in self._kb_chunks:
            self.kb_file_list.insert(tk.END, f"🧩 [{c['file'].split('/')[0]}] {c['heading'][:28]}")
        self.kb_file_label.config(text=f"← {len(self._kb_chunks)} 个知识块"
                                       + (f"（标签: {tag}）" if tag else ""))
        self.kb_content_text.config(state=tk.NORMAL)
        self.kb_content_text.delete("1.0", tk.END)
        self.kb_content_text.insert("1.0", "点击左侧知识块查看内容。知识块是 AI 起草时实际召回的最小单元。")
        self.kb_content_text.config(state=tk.DISABLED)
        self.kb_edit_btn.pack_forget()
        self.kb_save_btn.pack_forget()
        self.kb_status_label.config(
            text=f"标签筛选: {tag or '全部'} — {len(self._kb_chunks)} 个知识块（只读浏览，编辑请切回「全部文件」）")

    def _kb_refresh_files(self):
        """扫描 knowledge/ 目录刷新文件列表与知识块统计（并切回文件浏览模式）。"""
        self._kb_mode = "files"
        self._kb_chunks = []
        files = []
        for root, _, fns in os.walk(KNOWLEDGE_DIR):
            for fn in sorted(fns):
                if fn.endswith(".md"):
                    files.append(os.path.relpath(os.path.join(root, fn), KNOWLEDGE_DIR).replace("\\", "/"))
        self._kb_files = sorted(files)
        self.kb_file_list.delete(0, tk.END)
        for rel in self._kb_files:
            self.kb_file_list.insert(tk.END, f"📄 {rel}")
        chunks = load_knowledge_chunks()
        self.kb_status_label.config(
            text=f"共 {len(self._kb_files)} 个文件 / {len(chunks)} 个知识块 ｜ 保存后自动纳入下次 AI 起草召回")
        # 尽量保持原选中文件
        if self._kb_current_file in self._kb_files:
            idx = self._kb_files.index(self._kb_current_file)
            self.kb_file_list.selection_set(idx)
            self.kb_file_list.see(idx)

    def _kb_show_file(self, event=None):
        sel = self.kb_file_list.curselection()
        if not sel:
            return
        # 知识块模式：只读展示块内容
        if getattr(self, "_kb_mode", "files") == "chunks":
            if sel[0] >= len(self._kb_chunks):
                return
            c = self._kb_chunks[sel[0]]
            self.kb_file_label.config(text=f"🧩 [{c['file']}] {c['heading']}（{c['chars']} 字符 ｜ 标签: {', '.join(c['tags']) or '无'}）")
            self.kb_content_text.config(state=tk.NORMAL)
            self.kb_content_text.delete("1.0", tk.END)
            self.kb_content_text.insert("1.0", c["content"])
            self.kb_content_text.config(state=tk.DISABLED)
            return
        rel = self._kb_files[sel[0]]
        path = os.path.join(KNOWLEDGE_DIR, rel)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            self.kb_status_label.config(text=f"❌ 读取失败: {e}")
            return
        self._kb_current_file = rel
        self.kb_file_label.config(text=f"📄 {rel}（{len(content)} 字符）")
        self.kb_content_text.config(state=tk.NORMAL)
        self.kb_content_text.delete("1.0", tk.END)
        self.kb_content_text.insert("1.0", content)
        self.kb_content_text.config(state=tk.DISABLED)
        self.kb_edit_btn.pack(side=tk.RIGHT, padx=3)
        self.kb_save_btn.pack_forget()

    def _kb_edit_file(self):
        if not self._kb_current_file:
            return
        if "precedents.md" in self._kb_current_file:
            if not messagebox.askyesno("注意", "判例库的项目时间线由脚本自动生成，手工编辑会在下次重建时被覆盖。\n"
                                             "（典型判例速查表可安全编辑）\n\n仍要继续编辑吗？"):
                return
        self.kb_content_text.config(state=tk.NORMAL)
        self.kb_save_btn.pack(side=tk.RIGHT, padx=3)
        self.kb_edit_btn.pack_forget()
        self.kb_status_label.config(text=f"✏️ 编辑中: {self._kb_current_file} —— 完成后点击 💾 保存")

    def _kb_save_file(self):
        if not self._kb_current_file:
            return
        content = self.kb_content_text.get("1.0", tk.END).rstrip() + "\n"
        path = os.path.join(KNOWLEDGE_DIR, self._kb_current_file)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            messagebox.showwarning("保存失败", str(e))
            return
        self.kb_content_text.config(state=tk.DISABLED)
        self.kb_edit_btn.pack(side=tk.RIGHT, padx=3)
        self.kb_save_btn.pack_forget()
        # 失效运行时缓存，下次起草立即召回新内容；后台重建索引快照
        _KNOWLEDGE_CACHE["mtime"] = 0.0
        def _rebuild():
            try:
                if getattr(sys, "frozen", False):
                    import importlib.util as _ilu
                    script = os.path.join(os.path.dirname(sys.executable), "build_knowledge_index.py")
                    spec = _ilu.spec_from_file_location("build_knowledge_index", script)
                    mod = _ilu.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "main"):
                        mod.main()
                else:
                    import subprocess
                    script = os.path.join(_BASE_DIR, "build_knowledge_index.py")
                    subprocess.run([sys.executable, script], capture_output=True, timeout=60)
            except Exception:
                pass
        threading.Thread(target=_rebuild, daemon=True).start()
        chunks = load_knowledge_chunks()
        self.kb_status_label.config(text=f"✅ 已保存 {self._kb_current_file}，当前共 {len(chunks)} 个知识块")

    # ---------- 子页 2：人物角色 ----------

    def _build_kb_characters(self, parent):
        char_frame = tk.LabelFrame(parent, text=" 🎭 人物角色档案（AI 起草页「风格」下拉的可选项） ",
                                    bg=THEME["bg"], fg=THEME["accent2"],
                                    font=("Microsoft YaHei", 10, "bold"),
                                    bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                    highlightthickness=1)
        char_frame.pack(fill=tk.X, padx=5, pady=5)

        self._character_vars = {}  # 存储复选框变量
        self._character_checkboxes = []  # 存储复选框控件引用
        for char in self.data.get("characters", []):
            row = tk.Frame(char_frame, bg=THEME["bg"])
            row.pack(fill=tk.X, padx=5, pady=2)
            var = tk.BooleanVar(value=char.get("enabled", True))
            self._character_vars[char["id"]] = var
            cb = tk.Checkbutton(row, text=char.get("display_name", char["name"]), variable=var,
                                bg=THEME["bg"], fg=THEME["text"], selectcolor=THEME["card"],
                                activebackground=THEME["bg"], activeforeground=THEME["text"],
                                font=("Microsoft YaHei", 9), cursor="hand2")
            cb.pack(side=tk.LEFT)
            self._character_checkboxes.append(cb)
            tk.Label(row, text=char.get("description", ""), bg=THEME["bg"], fg=THEME["text_dim"],
                     font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=10)
            tk.Button(row, text="✏️ 编辑", bg=THEME["card"], fg=THEME["text"],
                      activebackground=THEME["accent"], activeforeground=THEME["bg"],
                      bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                      command=lambda c=char: self._edit_character(c)).pack(side=tk.RIGHT, padx=3)

        btn_char = tk.Frame(char_frame, bg=THEME["bg"])
        btn_char.pack(fill=tk.X, padx=5, pady=3)
        tk.Button(btn_char, text="💾 保存人物状态", bg=THEME["accent2"], fg=THEME["bg"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._save_character_states).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_char, text="🔄 重置为默认", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._reset_characters).pack(side=tk.LEFT, padx=3)
        tk.Label(btn_char, text="提示: 勾选的人物将出现在 AI 起草页「风格」下拉中（默认不注入任何人物）", bg=THEME["bg"],
                 fg=THEME["text_dim"], font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=10)

        tk.Label(parent, text="人物档案决定「谁来说话」：措辞习惯、签名格式、语言偏好。\n"
                              "在 AI 起草页选择风格后才会注入，不选则不注入。",
                 bg=THEME["bg"], fg=THEME["text_dim"], font=("Microsoft YaHei", 9),
                 justify=tk.LEFT).pack(anchor="w", padx=8, pady=6)

    # ---------- 子页 3：措辞知识库 ----------

    def _build_kb_tone(self, parent):
        # 顶部：标题 + 操作按钮
        top = tk.Frame(parent, bg=THEME["bg"])
        top.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(top, text="🧠 回复措辞知识库", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 12, "bold")).pack(side=tk.LEFT)

        tk.Label(top, text="  AI 起草时自动参考此知识库，确保回复语气、立场、口吻一致",
                 bg=THEME["bg"], fg=THEME["text_dim"], font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=5)

        top_btn = tk.Frame(top, bg=THEME["bg"])
        top_btn.pack(side=tk.RIGHT)

        tk.Button(top_btn, text="🧠 AI 分析生成", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._update_tone_knowledge).pack(side=tk.LEFT, padx=3)
        tk.Button(top_btn, text="📋 措辞Skill", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._edit_committee_skill).pack(side=tk.LEFT, padx=3)
        tk.Button(top_btn, text="✏️ 编辑", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._edit_tone_knowledge).pack(side=tk.LEFT, padx=3)
        tk.Button(top_btn, text="💾 保存", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._save_tone_knowledge).pack(side=tk.LEFT, padx=3)

        # 主编辑区
        self.tone_knowledge_text = scrolledtext.ScrolledText(parent, font=("Microsoft YaHei", 10),
                                                              wrap=tk.WORD, bg=THEME["card"], fg=THEME["text"],
                                                              insertbackground=THEME["text"], relief=tk.FLAT, bd=2)
        self.tone_knowledge_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tone_knowledge_text.insert("1.0", self.data.get("reply_tone_knowledge",
                                                             "暂无知识库内容。\n\n点击【🧠 AI 分析生成】让 DeepSeek 分析所有会议决议，自动生成语气、立场、口吻等回复措辞规范。\n\n也可直接在此编辑，编辑后点击【💾 保存】。"))
        self.tone_knowledge_text.config(state=tk.DISABLED)

        # 底部提示栏
        bottom = tk.Frame(parent, bg=THEME["bg"])
        bottom.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(bottom, text="提示: 知识库在 AI 起草时自动作为上下文注入，建议定期更新", bg=THEME["bg"],
                 fg=THEME["text_dim"], font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)

    # =================== Tab 6: 委员会纪要 ===================

    def _build_tab_meetings(self):
        """构建委员会纪要标签页。"""
        # 左侧：会议列表
        left = tk.Frame(self.tab_meetings, bg=THEME["bg"])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

        tk.Label(left, text="会议列表", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))

        btn_frame = tk.Frame(left, bg=THEME["bg"])
        btn_frame.pack(fill=tk.X, pady=3)
        tk.Button(btn_frame, text="➕ 新建", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._add_meeting_dialog).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame, text="📥 导入", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._import_meeting_dialog).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame, text="🗑️ 删除", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._delete_meeting).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame, text="📤 导出", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._export_meetings).pack(side=tk.LEFT, padx=2)

        cols = ("date",)
        self.meeting_tree = ttk.Treeview(left, columns=cols, show="headings", height=12)
        self.meeting_tree.heading("date", text="日期")
        self.meeting_tree.column("date", width=70, anchor="center")
        self.meeting_tree.pack(fill=tk.BOTH, expand=True)
        self.meeting_tree.bind("<<TreeviewSelect>>", self._on_meeting_select)

        # 右侧：详情
        right = tk.Frame(self.tab_meetings, bg=THEME["bg"])
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 基本信息
        info_frame = tk.LabelFrame(right, text="  会议信息  ", bg=THEME["bg"],
                                    fg=THEME["accent"], font=("Microsoft YaHei", 10, "bold"),
                                    bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                    highlightthickness=1)
        info_frame.pack(fill=tk.X, pady=3)

        info_top = tk.Frame(info_frame, bg=THEME["bg"])
        info_top.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(info_top, text="日期:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        self.meeting_date_var = tk.StringVar()
        tk.Entry(info_top, textvariable=self.meeting_date_var, width=14,
                 bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                 bd=1, relief=tk.FLAT).pack(side=tk.LEFT, padx=3)

        tk.Label(info_top, text="标题:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=(10, 0))
        self.meeting_title_var = tk.StringVar()
        tk.Entry(info_top, textvariable=self.meeting_title_var, width=35,
                 bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                 bd=1, relief=tk.FLAT).pack(side=tk.LEFT, padx=3)

        tk.Button(info_top, text="💾 保存", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._save_meeting_info).pack(side=tk.LEFT, padx=3)

        # Talk 关联（紧凑布局）
        talk_frame = tk.LabelFrame(right, text="  Nervos Talk 关联  ", bg=THEME["bg"],
                                    fg=THEME["accent"], font=("Microsoft YaHei", 10, "bold"),
                                    bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                    highlightthickness=1)
        talk_frame.pack(fill=tk.X, pady=3)

        talk_row = tk.Frame(talk_frame, bg=THEME["bg"])
        talk_row.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(talk_row, text="回复帖 URL:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        self.meeting_url_var = tk.StringVar()
        tk.Entry(talk_row, textvariable=self.meeting_url_var, width=32,
                 bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                 bd=1, relief=tk.FLAT).pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)
        tk.Label(talk_row, text="项目:", bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=(8,0))
        self._talk_link_proj_var = tk.StringVar(value="")
        self._talk_link_proj_combo = ttk.Combobox(talk_row, textvariable=self._talk_link_proj_var,
                                                   values=[], width=16, state="readonly")
        self._talk_link_proj_combo.pack(side=tk.LEFT, padx=3)
        tk.Button(talk_row, text="🔗", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._open_meeting_talk).pack(side=tk.LEFT, padx=2)
        tk.Button(talk_row, text="📎 建立关联", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._link_meeting_talk).pack(side=tk.LEFT, padx=2)

        # 决议列表
        res_frame = tk.LabelFrame(right, text="  项目评审决议  ", bg=THEME["bg"],
                                  fg=THEME["accent"], font=("Microsoft YaHei", 10, "bold"),
                                  bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                  highlightthickness=1)
        res_frame.pack(fill=tk.X, pady=3)

        res_btn = tk.Frame(res_frame, bg=THEME["bg"])
        res_btn.pack(fill=tk.X, padx=5, pady=2)
        tk.Button(res_btn, text="➕ 添加决议", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._add_resolution_dialog).pack(side=tk.LEFT, padx=2)
        tk.Button(res_btn, text="🗑️ 删除", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._delete_resolution).pack(side=tk.LEFT, padx=2)
        tk.Button(res_btn, text="🔄 刷新主帖", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._refresh_resolution_posts).pack(side=tk.LEFT, padx=2)

        res_cols = ("project", "decision", "round", "budget", "note", "main_post", "talk_url")
        self.resolution_tree = ttk.Treeview(res_frame, columns=res_cols, show="headings", height=6)
        self.resolution_tree.heading("project", text="项目名称")
        self.resolution_tree.column("project", width=130, anchor="w")
        self.resolution_tree.heading("decision", text="决议")
        self.resolution_tree.column("decision", width=55, anchor="center")
        self.resolution_tree.heading("round", text="轮次")
        self.resolution_tree.column("round", width=45, anchor="center")
        self.resolution_tree.heading("budget", text="预算")
        self.resolution_tree.column("budget", width=65, anchor="center")
        self.resolution_tree.heading("note", text="备注")
        self.resolution_tree.column("note", width=210, anchor="w")
        self.resolution_tree.heading("main_post", text="主帖")
        self.resolution_tree.column("main_post", width=45, anchor="center")
        self.resolution_tree.heading("talk_url", text="关联帖")
        self.resolution_tree.column("talk_url", width=50, anchor="center")
        self.resolution_tree.pack(fill=tk.X, padx=5, pady=3)
        self.resolution_tree.bind("<<TreeviewSelect>>", self._on_resolution_select)
        self.resolution_tree.bind("<Double-Button-1>", self._on_resolution_double_click)

        # Google Meet 会议区域
        audio_frame = tk.LabelFrame(right, text="  Google Meet 会议  ", bg=THEME["bg"],
                                     fg=THEME["accent"], font=("Microsoft YaHei", 10, "bold"),
                                     bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                     highlightthickness=1)
        audio_frame.pack(fill=tk.BOTH, expand=True, pady=3)

        # 会议链接输入区
        link_frame = tk.Frame(audio_frame, bg=THEME["bg"])
        link_frame.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(link_frame, text="会议链接:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=2)
        self.meeting_link_var = tk.StringVar()
        tk.Entry(link_frame, textvariable=self.meeting_link_var, width=45,
                 bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                 bd=1, relief=tk.FLAT, font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        tk.Button(link_frame, text="💾 保存", bg=THEME["accent2"], fg=THEME["bg"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._save_meeting_link).pack(side=tk.LEFT, padx=2)
        tk.Button(link_frame, text="🔗 打开", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9),
                  command=self._open_meeting_link).pack(side=tk.LEFT, padx=2)
        self._audio_status_label = tk.Label(link_frame, text="", bg=THEME["bg"],
                                            fg=THEME["text_dim"], font=("Microsoft YaHei", 9))
        self._audio_status_label.pack(side=tk.LEFT, padx=5)

        # 操作指南
        guide_text = (
            "操作指南："
            "1. 在 Google Meet 中创建会议，复制会议链接粘贴到上方；"
            "2. 点击【保存】记录会议链接，点击【打开】快速进入会议；"
            "3. 佩戴耳机以保证通话质量，同时避免回声；"
            "4. 点击浏览器工具栏的通义听悟插件图标 → 选择'开启实时记录'；"
            "5. 选择当前 Google Meet 标签页，开始自动转写；"
            "6. 会议结束后，前往 tingwu.aliyun.com 查看转写结果；"
            "7. 在左侧会议列表点击【导入】，粘贴转写文本完成纪要。"
        )
        tk.Label(audio_frame, text=guide_text, bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Microsoft YaHei", 9), justify=tk.LEFT, wraplength=600).pack(anchor="w", padx=5, pady=2)

        tk.Label(audio_frame, text="转写文本:", bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Microsoft YaHei", 9)).pack(anchor="w", padx=5)
        self.transcript_text = tk.Text(audio_frame, height=6, font=("Microsoft YaHei", 9),
                                       bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                                       bd=1, relief=tk.FLAT)
        self.transcript_text.pack(fill=tk.X, padx=5, pady=2)
        self.transcript_text.insert("1.0", "通义听悟转写文本将显示在这里。\n通过左侧会议列表的【导入】功能粘贴。")

        tk.Label(audio_frame, text="AI 总结:", bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Microsoft YaHei", 9)).pack(anchor="w", padx=5)
        self.ai_summary_text = tk.Text(audio_frame, height=5, font=("Microsoft YaHei", 9),
                                         bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                                         bd=1, relief=tk.FLAT)
        self.ai_summary_text.pack(fill=tk.X, padx=5, pady=2)
        self.ai_summary_text.insert("1.0", "AI 总结将显示在这里。\n通过左侧会议列表的【导入】功能生成。")

        self._current_meeting_idx = None
        self._resolution_edit_widget = None
        self._resolution_edit_idx = None
        self._resolution_edit_col = None
        self._pending_link = None
        self._refresh_meeting_list()
        # 加载全局会议链接（与具体会议无关，每周固定）
        if hasattr(self, 'meeting_link_var'):
            self.meeting_link_var.set(self.data.get("meet_link", ""))

    def _build_tab_meeting_items(self):
        """构建上会项目标签页：展示需要上会的帖子。"""
        # 顶部操作栏
        top = tk.Frame(self.tab_meeting_items, bg=THEME["bg"])
        top.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(top, text="🏛 上会项目", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 12, "bold")).pack(side=tk.LEFT)

        self.meeting_items_status = tk.Label(top, text="点击 🔄 刷新获取需要上会的帖子", bg=THEME["bg"],
                                              fg=THEME["text_dim"], font=("Microsoft YaHei", 9))
        self.meeting_items_status.pack(side=tk.LEFT, padx=10)

        tk.Button(top, text="🔄 刷新", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=self._refresh_meeting_items).pack(side=tk.LEFT, padx=5)

        # 主内容区域：左右分栏
        main = tk.Frame(self.tab_meeting_items, bg=THEME["bg"])
        main.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)

        # 左栏：分类列表（紧凑）
        left = tk.Frame(main, bg=THEME["bg"])
        left.grid(row=0, column=0, sticky="nsew")

        # 右栏：详情面板
        right = tk.Frame(main, bg=THEME["bg"])
        right.grid(row=0, column=1, sticky="nsew", padx=5)
        self.meeting_items_detail_frame = right

        # 详情面板内容
        tk.Label(right, text="帖子详情", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))
        self.detail_title = tk.Label(right, text="请选择左侧帖子", bg=THEME["bg"], fg=THEME["text"],
                                      font=("Microsoft YaHei", 10, "bold"), wraplength=400, justify=tk.LEFT)
        self.detail_title.pack(anchor="w", fill=tk.X)
        self.detail_meta = tk.Label(right, text="", bg=THEME["bg"], fg=THEME["text_dim"],
                                     font=("Microsoft YaHei", 9))
        self.detail_meta.pack(anchor="w", fill=tk.X)
        self.detail_status = tk.Label(right, text="", bg=THEME["bg"], fg=THEME["warning"],
                                       font=("Microsoft YaHei", 9))
        self.detail_status.pack(anchor="w", fill=tk.X)

        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        tk.Label(right, text="AI总结:", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 9, "bold")).pack(anchor="w")
        self.detail_ai_summary = tk.Label(right, text="—", bg=THEME["bg"], fg=THEME["text"],
                                           font=("Microsoft YaHei", 9), wraplength=400, justify=tk.LEFT)
        self.detail_ai_summary.pack(anchor="w", fill=tk.X, pady=2)

        tk.Label(right, text="发帖人最新回复:", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 9, "bold")).pack(anchor="w", pady=(5, 0))
        self.detail_op_content = scrolledtext.ScrolledText(right, font=("Microsoft YaHei", 9), wrap=tk.WORD,
                                                            bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                                                            relief=tk.FLAT, bd=1, height=8)
        self.detail_op_content.pack(fill=tk.BOTH, expand=True, pady=2)
        self.detail_op_content.insert("1.0", "点击左侧列表中的帖子查看详情")
        self.detail_op_content.config(state=tk.DISABLED)

        # 三个分类区域（紧凑布局）
        self.meeting_items_frames = {}
        self.meeting_items_listboxes = {}
        self.meeting_items_maps = {}  # 索引 -> 帖子数据

        categories = [
            ("in_progress", "In-Progress", THEME["accent"]),
            ("pending", "Pending", THEME["warning"]),
            ("submitted", "Submitted", THEME["success"]),
        ]

        for key, label, color in categories:
            frame = tk.LabelFrame(left, text=f"  {label} (0)  ", bg=THEME["bg"],
                                  fg=color, font=("Microsoft YaHei", 10, "bold"),
                                  bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                  highlightthickness=1)
            frame.pack(fill=tk.X, expand=False, padx=2, pady=1)  # compact
            self.meeting_items_frames[key] = frame

            lb = tk.Listbox(frame, font=("Microsoft YaHei", 9), bg=THEME["card"], fg=THEME["text"],
                            selectbackground=THEME["accent"], selectforeground=THEME["bg"],
                            bd=0, relief=tk.FLAT, highlightthickness=0, height=4)
            lb.pack(fill=tk.X, expand=False, padx=2, pady=2)  # compact
            lb.bind("<Double-Button-1>", lambda e, k=key: self._open_meeting_item_url(k))
            lb.bind("<<ListboxSelect>>", lambda e, k=key: self._show_meeting_item_detail(k))
            self.meeting_items_listboxes[key] = lb
            self.meeting_items_maps[key] = {}

    # =================== 上会项目核心逻辑 ===================

    def _refresh_meeting_items(self):
        """刷新上会项目列表：获取帖子详情、判断是否需要上会、AI总结。"""
        self.meeting_items_status.config(text="⏳ 正在获取帖子数据...", fg=THEME["accent"])
        self.root.update()

        # 从缓存获取三个标签的帖子
        cache = load_project_cache()
        if not cache or "classified" not in cache:
            self.meeting_items_status.config(text="❌ 请先刷新看板数据", fg=THEME["danger"])
            return

        c = cache["classified"]
        all_posts = []
        for key in ["in_progress", "pending", "submitted"]:
            posts = c.get(key, [])
            all_posts.extend([(key, p) for p in posts])

        if not all_posts:
            self.meeting_items_status.config(text="✅ 当前没有需要上会的帖子", fg=THEME["success"])
            for key in ["in_progress", "pending", "submitted"]:
                self._clear_meeting_items_list(key)
            return

        # 清空列表
        for key in ["in_progress", "pending", "submitted"]:
            self._clear_meeting_items_list(key)

        self.meeting_items_status.config(text=f"⏳ 正在分析 {len(all_posts)} 个帖子...", fg=THEME["accent"])
        self.root.update()

        # 委员会成员
        COMMITTEE_MEMBERS = {"zz_tovarishch", "xingtianchunyan", "yixiu.ckbfans.bit", "hanssen"}

        meeting_items = {"in_progress": [], "pending": [], "submitted": []}
        total = len(all_posts)
        processed = 0

        for cat_key, post in all_posts:
            post_id = post.get("id")
            slug = post.get("slug", "")
            url = post.get("url", "")
            if not post_id or not url:
                continue

            # 获取帖子详情
            topic_data = self._fetch_topic_detail(post_id, slug)
            if not topic_data:
                continue

            posts_list = topic_data.get("post_stream", {}).get("posts", [])
            if not posts_list:
                continue

            # 主帖发帖人 = 第一个帖子的作者
            op_username = posts_list[0].get("username", "").lower()
            if not op_username:
                continue

            # 找到发帖人的所有回复（包括主帖）
            op_posts = [p for p in posts_list if p.get("username", "").lower() == op_username]
            if not op_posts:
                continue

            # 找到发帖人最新回复（按post_number最大）
            last_op_post = max(op_posts, key=lambda p: p.get("post_number", 0))
            last_op_post_number = last_op_post.get("post_number", 0)
            last_op_time = last_op_post.get("created_at", "")

            # 检查发帖人最后一条回复之后，是否有委员会成员回复
            committee_after_op = False
            committee_last_reply_time = ""
            for p in posts_list:
                if p.get("post_number", 0) > last_op_post_number:
                    author = p.get("username", "").lower()
                    if author in COMMITTEE_MEMBERS:
                        committee_after_op = True
                        if not committee_last_reply_time or p.get("created_at", "") > committee_last_reply_time:
                            committee_last_reply_time = p.get("created_at", "")

            needs_meeting = False
            overdue_7d = False

            if not committee_after_op:
                # 委员会未回复发帖人最后一条回复 → 需要上会
                needs_meeting = True
            elif cat_key == "in_progress" and committee_last_reply_time:
                # In-Progress 特殊判断：委员会已回复但发帖人超过7天未回应
                try:
                    if "." in committee_last_reply_time:
                        committee_time = datetime.datetime.strptime(committee_last_reply_time, "%Y-%m-%dT%H:%M:%S.%fZ")
                    else:
                        committee_time = datetime.datetime.strptime(committee_last_reply_time, "%Y-%m-%dT%H:%M:%SZ")
                    now = datetime.datetime.now()
                    if (now - committee_time).days > 7:
                        needs_meeting = True
                        overdue_7d = True
                except ValueError:
                    pass

            if needs_meeting:
                # 获取发帖人最新回复内容（纯文本）
                latest_op_content = self._strip_html(last_op_post.get("cooked", ""))

                # 判断是否有无关回复：如果发帖人发布了多条回复，且中间有社区成员回复
                irrelevant_mark = ""
                if len(op_posts) > 1:
                    op_post_numbers = sorted([p.get("post_number", 0) for p in op_posts])
                    for i in range(len(op_post_numbers) - 1):
                        for p in posts_list:
                            pn = p.get("post_number", 0)
                            if op_post_numbers[i] < pn < op_post_numbers[i + 1]:
                                author = p.get("username", "").lower()
                                if author not in COMMITTEE_MEMBERS and author != op_username:
                                    irrelevant_mark = "[无关] "
                                    break
                        if irrelevant_mark:
                            break

                item = {
                    "post": post,
                    "title": post.get("title", ""),
                    "author": post.get("original_poster_username") or post.get("author", ""),
                    "url": url,
                    "latest_op_content": latest_op_content[:300] if latest_op_content else "",
                    "irrelevant_mark": irrelevant_mark,
                    "last_op_time": last_op_time,
                    "overdue_7d": overdue_7d,
                }
                meeting_items[cat_key].append(item)

            processed += 1
            if processed % 5 == 0:
                self.meeting_items_status.config(text=f"⏳ 已分析 {processed}/{total}...", fg=THEME["accent"])
                self.root.update()

        # 按发帖人最新回复时间排序（早的在前）
        for key in meeting_items:
            meeting_items[key].sort(key=lambda x: x.get("last_op_time", ""), reverse=False)

        # 更新UI
        total_items = 0
        for key, label, _ in [("in_progress", "In-Progress", None), ("pending", "Pending", None), ("submitted", "Submitted", None)]:
            items = meeting_items[key]
            total_items += len(items)
            self._update_meeting_items_list(key, label, items)

        self.meeting_items_status.config(
            text=f"✅ 共找到 {total_items} 个需要上会的帖子" if total_items > 0 else "✅ 当前没有需要上会的帖子",
            fg=THEME["success"]
        )

        # 如果有需要上会的帖子且有API Key，启动AI总结
        api_key = get_ai_api_key(self.data.get("ai_provider", "siliconflow"))
        if total_items > 0 and api_key:
            self.meeting_items_status.config(text=f"✅ 找到 {total_items} 个帖子，启动AI总结...", fg=THEME["accent"])
            threading.Thread(target=self._ai_summarize_meeting_items, args=(meeting_items,), daemon=True).start()

    def _clear_meeting_items_list(self, key):
        """清空指定分类的列表。"""
        lb = self.meeting_items_listboxes.get(key)
        if lb:
            lb.delete(0, tk.END)
        self.meeting_items_maps[key] = {}

    def _update_meeting_items_list(self, key, label, items):
        """更新指定分类的列表显示。"""
        lb = self.meeting_items_listboxes.get(key)
        frame = self.meeting_items_frames.get(key)
        if not lb or not frame:
            return

        lb.delete(0, tk.END)
        self.meeting_items_maps[key] = {}

        frame.config(text=f"  {label} ({len(items)})  ")

        for i, item in enumerate(items):
            title = item.get("title", "Unknown")
            author = item.get("author", "Unknown")
            mark = item.get("irrelevant_mark", "")
            overdue = "[超7天] " if item.get("overdue_7d") else ""
            summary = item.get("ai_summary", "")
            display = f"{overdue}{mark}{title}  ({author})"
            if summary:
                display += f"  |  {summary}"
            lb.insert(tk.END, display)
            self.meeting_items_maps[key][i] = item

    def _fetch_topic_detail(self, topic_id, slug=""):
        """获取帖子详情（包含所有回复）。"""
        if slug:
            url = f"https://talk.nervos.org/t/{slug}/{topic_id}.json"
        else:
            url = f"https://talk.nervos.org/t/{topic_id}.json"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                return jsonlib.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"获取帖子详情失败 {topic_id}: {e}")
            return None

    def _strip_html(self, html_text):
        """去除HTML标签，保留纯文本。"""
        import re
        text = re.sub(r'<[^>]+>', ' ', html_text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _open_meeting_item_url(self, key):
        """双击打开选中的帖子URL。"""
        lb = self.meeting_items_listboxes.get(key)
        if not lb:
            return
        sel = lb.curselection()
        if not sel:
            return
        idx = sel[0]
        item = self.meeting_items_maps.get(key, {}).get(idx)
        if item:
            url = item.get("url", "")
            if url:
                webbrowser.open(url)

    def _show_meeting_item_detail(self, key):
        """显示选中帖子的详情。"""
        lb = self.meeting_items_listboxes.get(key)
        if not lb:
            return
        sel = lb.curselection()
        if not sel:
            return
        idx = sel[0]
        item = self.meeting_items_maps.get(key, {}).get(idx)
        if not item:
            return

        self.detail_title.config(text=item.get("title", "Unknown"))
        meta = f"作者: {item.get('author', 'Unknown')}  |  分类: {key}"
        self.detail_meta.config(text=meta)

        status_parts = []
        if item.get("overdue_7d"):
            status_parts.append("委员会回复超7天未收到发帖人回应")
        else:
            status_parts.append("委员会未回复发帖人最新消息")
        if item.get("irrelevant_mark"):
            status_parts.append("含无关回复")
        self.detail_status.config(text="  ".join(status_parts))

        ai_summary = item.get("ai_summary", "")
        self.detail_ai_summary.config(text=ai_summary if ai_summary else "—")

        content = item.get("latest_op_content", "")
        self.detail_op_content.config(state=tk.NORMAL)
        self.detail_op_content.delete("1.0", tk.END)
        self.detail_op_content.insert("1.0", content if content else "无内容")
        self.detail_op_content.config(state=tk.DISABLED)

    def _ai_summarize_meeting_items(self, meeting_items):
        """后台线程：用AI总结每个需要上会的帖子的发帖人最新回复。"""
        provider = self.data.get("ai_provider", "siliconflow")
        api_key = get_ai_api_key(provider)
        model = self.data.get("ai_model", "deepseek-ai/DeepSeek-V4-Flash")
        if not api_key or not model:
            return

        all_items = []
        for key in ["in_progress", "pending", "submitted"]:
            all_items.extend(meeting_items.get(key, []))

        for item in all_items:
            content = item.get("latest_op_content", "")
            if not content:
                continue

            prompt = f"""请用一句话（不超过30个字）简要总结以下Spark Program项目帖子的最新回复内容：

{content[:500]}

只输出总结，不要其他内容。"""

            try:
                summary, err = call_ai_api(provider, api_key, prompt, model)
                if summary and not err:
                    item["ai_summary"] = summary.strip().strip('"').strip("'").strip()
            except Exception as e:
                print(f"AI总结失败: {e}")
                continue

        # 更新UI
        self.root.after(0, lambda: self._update_meeting_items_after_ai(meeting_items))

    def _update_meeting_items_after_ai(self, meeting_items):
        """AI总结完成后更新UI。"""
        for key in ["in_progress", "pending", "submitted"]:
            items = meeting_items.get(key, [])
            label_map = {"in_progress": "In-Progress", "pending": "Pending", "submitted": "Submitted"}
            self._update_meeting_items_list(key, label_map.get(key, key), items)

        total = sum(len(meeting_items.get(k, [])) for k in ["in_progress", "pending", "submitted"])
        self.meeting_items_status.config(text=f"✅ 已分析 {total} 个帖子（AI总结完成）", fg=THEME["success"])

    # =================== 纪要操作 ===================

    def _refresh_meeting_list(self):
        for item in self.meeting_tree.get_children():
            self.meeting_tree.delete(item)
        meetings = self.data.get("meetings", [])
        for idx, m in enumerate(meetings):
            self.meeting_tree.insert("", tk.END, iid=str(idx),
                values=(m.get("date", ""),))

    def _find_post_by_project_name(self, project_name):
        """从看板缓存中查找与项目名称匹配的帖子 URL。"""
        if not project_name:
            return None
        cache = load_project_cache()
        if not cache:
            return None
        all_posts = cache.get("all_posts", {})
        proj_lower = project_name.lower().strip()
        for tag_posts in all_posts.values():
            for post in tag_posts:
                title = post.get("title", "").lower()
                if proj_lower in title or title in proj_lower:
                    return post.get("url", "")
        return None

    def _on_meeting_select(self, event=None):
        sel = self.meeting_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        meetings = self.data.get("meetings", [])
        if 0 <= idx < len(meetings):
            self._current_meeting_idx = idx
            m = meetings[idx]
            self.meeting_date_var.set(m.get("date", ""))
            self.meeting_title_var.set(m.get("title", ""))
            self.meeting_url_var.set(m.get("talk_topic_url", ""))

            # 取消正在进行的单元格编辑
            self._cancel_resolution_cell()

            # 决议
            for item in self.resolution_tree.get_children():
                self.resolution_tree.delete(item)
            for ridx, r in enumerate(m.get("resolutions", [])):
                # 自动匹配主帖 URL（如果为空）
                if not r.get("main_post_url", ""):
                    matched_url = self._find_post_by_project_name(r.get("project", ""))
                    if matched_url:
                        r["main_post_url"] = matched_url
                        save_data(self.data)
                talk_url = r.get("talk_url", "")
                main_url = r.get("main_post_url", "")
                self.resolution_tree.insert("", tk.END, iid=str(ridx),
                    values=(r.get("project", ""), r.get("decision", ""),
                            r.get("round", ""), r.get("budget", ""),
                            r.get("note", ""), "📋" if main_url else "", "🔗" if talk_url else ""))

            # 刷新Talk关联下拉
            self._refresh_talk_link_dropdown()

            self.meeting_link_var.set(self.data.get("meet_link", ""))

            # 转写 & 总结
            self.transcript_text.delete("1.0", tk.END)
            self.transcript_text.insert("1.0", m.get("transcript", "通义听悟转写文本将显示在这里。\n通过左侧会议列表的【导入】功能粘贴。"))
            self.ai_summary_text.delete("1.0", tk.END)
            self.ai_summary_text.insert("1.0", m.get("ai_summary", "AI 总结将显示在这里。\n通过左侧会议列表的【导入】功能生成。"))

    def _add_meeting_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("新建会议纪要")
        dlg.geometry("400x200")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=THEME["bg"])

        tk.Label(dlg, text="日期:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 10)).grid(row=0, column=0, sticky="w", padx=5, pady=5)
        date_var = tk.StringVar(value=datetime.date.today().strftime("%Y-%m-%d"))
        tk.Entry(dlg, textvariable=date_var, width=30, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=0, column=1, padx=5, pady=5)

        tk.Label(dlg, text="标题:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 10)).grid(row=1, column=0, sticky="w", padx=5, pady=5)
        title_var = tk.StringVar(value="委员会评审会议")
        tk.Entry(dlg, textvariable=title_var, width=30, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=1, column=1, padx=5, pady=5)

        def save():
            meeting = {
                "id": generate_id(),
                "date": date_var.get().strip(),
                "title": title_var.get().strip(),
                "talk_topic_url": "",
                "talk_content": "",
                "resolutions": [],
                "transcript": "",
                "ai_summary": "",
                "created": datetime.date.today().isoformat()
            }
            self.data.setdefault("meetings", []).append(meeting)
            save_data(self.data)
            self._refresh_meeting_list()
            dlg.destroy()

        tk.Button(dlg, text="保存", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 11, "bold"),
                  command=save).grid(row=2, column=0, columnspan=2, pady=15)

    def _import_meeting_dialog(self):
        """一键导入会议纪要对话框。支持粘贴文本+AI/正则解析。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("📥 导入会议纪要")
        dlg.geometry("700x650")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=THEME["bg"])

        # 模板参考
        template_frame = tk.LabelFrame(dlg, text="  支持格式参考（点击可展开/折叠）  ", bg=THEME["bg"],
                                        fg=THEME["accent"], font=("Microsoft YaHei", 9, "bold"),
                                        bd=1, relief=tk.FLAT, highlightbackground=THEME["border"],
                                        highlightthickness=1)
        template_frame.pack(fill=tk.X, padx=10, pady=5)
        template_text = tk.Text(template_frame, height=7, font=("Microsoft YaHei", 9),
                                bg=THEME["card"], fg=THEME["text_dim"], insertbackground=THEME["text"],
                                bd=1, relief=tk.FLAT)
        template_text.pack(fill=tk.X, padx=5, pady=3)
        template_text.insert("1.0",
            "【模板格式一】\n"
            "2024-01-15 委员会评审会议\n"
            "1. CKB Probe | 通过 | 50000 | 技术方案可行\n"
            "2. Cell Sandbox | 待定 | 30000 | 需要补充团队背景\n"
            "3. Dular | 拒绝 | 0 | 方向与项目不符\n\n"
            "【模板格式二】\n"
            "日期：2024-01-15\n"
            "标题：委员会评审会议\n"
            "- 项目：CKB Probe | 决议：通过 | 预算：50000 | 备注：技术方案可行\n"
            "- 项目：Cell Sandbox | 决议：待定 | 预算：30000 | 备注：需补充\n\n"
            "【AI智能解析】\n"
            "粘贴任何格式的会议记录，点击【AI解析】自动提取日期/项目/决议。"
        )
        template_text.config(state=tk.DISABLED)

        # 粘贴输入区
        tk.Label(dlg, text="粘贴会议记录:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=10, pady=3)
        input_text = tk.Text(dlg, height=10, font=("Microsoft YaHei", 10),
                             bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                             bd=1, relief=tk.FLAT)
        input_text.pack(fill=tk.X, padx=10, pady=3)
        input_text.insert("1.0", "在此粘贴会议记录内容...")

        # 解析按钮行
        btn_row = tk.Frame(dlg, bg=THEME["bg"])
        btn_row.pack(fill=tk.X, padx=10, pady=5)

        tk.Button(btn_row, text="⚡ 正则快速解析", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=lambda: _parse_regex()).pack(side=tk.LEFT, padx=3)

        tk.Button(btn_row, text="🤖 AI 智能解析", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=lambda: _parse_ai()).pack(side=tk.LEFT, padx=3)

        tk.Button(btn_row, text="💾 确认导入", bg=THEME["success"], fg=THEME["bg"],
                  activebackground="#a6e3a1", activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"),
                  command=lambda: _do_import()).pack(side=tk.LEFT, padx=3)

        # 状态
        status_label = tk.Label(dlg, text="", bg=THEME["bg"], fg=THEME["text_dim"],
                                font=("Microsoft YaHei", 9))
        status_label.pack(anchor="w", padx=10, pady=2)

        # 预览区
        tk.Label(dlg, text="解析预览:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=10, pady=3)
        preview_text = tk.Text(dlg, height=8, font=("Microsoft YaHei", 9),
                               bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                               bd=1, relief=tk.FLAT)
        preview_text.pack(fill=tk.X, padx=10, pady=3)
        preview_text.insert("1.0", "解析后在此显示预览...")
        preview_text.config(state=tk.DISABLED)

        # 解析结果存储
        parsed_result = {"date": "", "title": "", "resolutions": []}

        def _parse_regex():
            raw = input_text.get("1.0", tk.END).strip()
            if not raw:
                status_label.config(text="❌ 请输入内容", fg=THEME["danger"])
                return
            result = self._parse_meeting_text(raw)
            parsed_result["date"] = result.get("date", "")
            parsed_result["title"] = result.get("title", "")
            parsed_result["resolutions"] = result.get("resolutions", [])
            _update_preview(result)
            status_label.config(text=f"✅ 正则解析完成：找到 {len(result.get('resolutions', []))} 条决议", fg=THEME["success"])

        def _parse_ai():
            raw = input_text.get("1.0", tk.END).strip()
            if not raw:
                status_label.config(text="❌ 请输入内容", fg=THEME["danger"])
                return
            provider = self.data.get("ai_provider", "siliconflow")
            api_key = get_ai_api_key(provider)
            model = "deepseek-ai/DeepSeek-V4-Flash"
            if not api_key:
                status_label.config(text="❌ 未配置 AI API Key，请先在 🤖 AI起草 标签页配置", fg=THEME["danger"])
                return
            status_label.config(text="⏳ AI 解析中...", fg=THEME["warning"])
            dlg.update()

            def _run():
                try:
                    prompt = (
                        f"请从以下会议记录中提取结构化信息，返回严格JSON格式：\n"
                        f"{{\"date\":\"日期\",\"title\":\"会议标题\",\"resolutions\":["
                        f"{{\"project\":\"项目名称\",\"decision\":\"通过/拒绝/待定/关闭/完成\",\"budget\":\"预算金额\",\"note\":\"备注\"}}"
                        f"]}}\n\n"
                        f"会议记录：\n{raw[:8000]}\n\n"
                        f"请只返回JSON，不要解释。"
                    )
                    content, err = call_ai_api(provider, api_key, prompt, model)
                    if err:
                        self.root.after(0, lambda: status_label.config(text=f"❌ AI解析失败: {err[:50]}", fg=THEME["danger"]))
                        return
                    # 提取 JSON
                    import re
                    m = re.search(r'\{.*\}', content, re.DOTALL)
                    if m:
                        d = jsonlib.loads(m.group())
                        parsed_result["date"] = d.get("date", "")
                        parsed_result["title"] = d.get("title", "")
                        parsed_result["resolutions"] = d.get("resolutions", [])
                        self.root.after(0, lambda: (_update_preview(parsed_result), status_label.config(text=f"✅ AI解析完成：{len(parsed_result['resolutions'])} 条决议", fg=THEME["success"])))
                    else:
                        self.root.after(0, lambda: status_label.config(text="❌ AI返回中未找到JSON", fg=THEME["danger"]))
                except Exception as e:
                    self.root.after(0, lambda: status_label.config(text=f"❌ AI解析异常: {str(e)[:50]}", fg=THEME["danger"]))
            import threading
            threading.Thread(target=_run, daemon=True).start()

        def _update_preview(result):
            preview_text.config(state=tk.NORMAL)
            preview_text.delete("1.0", tk.END)
            preview_text.insert("1.0", f"日期: {result.get('date', '')}\n")
            preview_text.insert(tk.END, f"标题: {result.get('title', '')}\n\n")
            for i, r in enumerate(result.get("resolutions", []), 1):
                preview_text.insert(tk.END, f"{i}. {r.get('project', '')} | {r.get('decision', '')} | {r.get('budget', '')} | {r.get('note', '')}\n")
            preview_text.config(state=tk.DISABLED)

        def _do_import():
            if not parsed_result["date"] and not parsed_result["title"] and not parsed_result["resolutions"]:
                status_label.config(text="❌ 请先解析内容", fg=THEME["danger"])
                return
            meeting = {
                "id": generate_id(),
                "date": parsed_result.get("date", datetime.date.today().isoformat()),
                "title": parsed_result.get("title", "导入的会议"),
                "talk_topic_url": "",
                "talk_content": "",
                "resolutions": parsed_result.get("resolutions", []),
                "transcript": "",
                "ai_summary": "",
                "created": datetime.date.today().isoformat()
            }
            self.data.setdefault("meetings", []).append(meeting)
            save_data(self.data)
            self._refresh_meeting_list()
            dlg.destroy()
            messagebox.showinfo("导入成功", f"已导入会议纪要：{meeting['title']}\n共 {len(meeting['resolutions'])} 条决议")
            # P5 维护联动：提示将本次决议提炼进判例案例库
            if meeting["resolutions"] and messagebox.askyesno(
                    "更新判例库", "是否将本次决议同步到知识库判例案例库（knowledge/spark/precedents.md）？\n"
                    "（全量重建项目时间线，典型判例速查表不受影响）"):
                self._rebuild_precedents()

    def _rebuild_precedents(self):
        """调用 build_precedents.py 全量重建判例库（后台线程）。"""
        def _run():
            try:
                if getattr(sys, "frozen", False):
                    # 打包为 exe 时进程内执行，避免用 sys.executable 重启 GUI
                    import importlib.util as _ilu, io, contextlib
                    script = os.path.join(os.path.dirname(sys.executable), "build_precedents.py")
                    spec = _ilu.spec_from_file_location("build_precedents", script)
                    mod = _ilu.module_from_spec(spec)
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        spec.loader.exec_module(mod)
                        mod.main()
                    ok, msg = True, buf.getvalue().strip()
                else:
                    import subprocess
                    script = os.path.join(_BASE_DIR, "build_precedents.py")
                    r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=60)
                    ok = r.returncode == 0
                    msg = r.stdout.strip() if ok else (r.stderr.strip()[:200])
                self.root.after(0, lambda: messagebox.showinfo(
                    "判例库已更新" if ok else "判例库更新失败", msg or ("完成" if ok else "未知错误")))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showwarning("判例库更新失败", str(e)))
        threading.Thread(target=_run, daemon=True).start()

    def _parse_meeting_text(self, text):
        """从文本中解析会议日期、标题和决议列表。支持多种常见格式。"""
        import re
        result = {"date": "", "title": "", "resolutions": []}

        # 提取日期：YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD（月日允许不补零）
        date_match = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', text)
        if date_match:
            y, mo, d = date_match.groups()
            result["date"] = f"{y}-{int(mo):02d}-{int(d):02d}"
        else:
            result["date"] = datetime.date.today().isoformat()

        # 提取标题：紧跟日期的文本，或第一行中非日期非数字的部分
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if lines:
            first = lines[0]
            # 去掉日期部分后的剩余文本作为标题
            title_candidate = re.sub(r'\d{4}[-/.]\d{1,2}[-/.]\d{1,2}', '', first).strip()
            if title_candidate and len(title_candidate) > 2:
                result["title"] = title_candidate
            elif len(lines) > 1:
                result["title"] = lines[1][:40]
            else:
                result["title"] = "委员会评审会议"
        else:
            result["title"] = "委员会评审会议"

        # 优先尝试委员会编号格式（状态小节 + 数字编号条目，决议/预算在条目行内）
        numbered = self._parse_meeting_numbered_format(text)
        if numbered:
            result["resolutions"] = numbered
            return result

        # 提取决议：支持多种格式
        # 格式1: 1. 项目名称 | 决议 | 预算 | 备注
        # 格式2: - 项目：XXX | 决议：通过 | 预算：50000 | 备注：...
        # 格式3: 项目名称 通过 50000 备注
        # 格式4: 项目: XXX, 决议: 通过, 预算: 50000

        # 尝试匹配带序号的行
        pattern1 = re.compile(
            r'(?:^|\n)\s*(?:\d+[\.、]\s*)?'
            r'([^|\n]+?)\s*[|\t]\s*'
            r'(通过|拒绝|待定|关闭|完成|延期|补充材料|否决|不通过|approved|rejected|pending|close|completed|pass|fail|done|finish)\s*[|\t]\s*'
            r'([^|\n]*)\s*(?:[|\t]\s*(.*))?',
            re.IGNORECASE
        )

        for m in pattern1.finditer(text):
            project = m.group(1).strip()
            decision_raw = m.group(2).strip().lower()
            budget = m.group(3).strip() if m.group(3) else ""
            note = m.group(4).strip() if m.group(4) else ""

            # 标准化决议为5种：通过/待定/拒绝/关闭/完成
            if decision_raw in ("通过", "approved", "pass", "approve"):
                decision = "通过"
            elif decision_raw in ("拒绝", "rejected", "reject", "否决", "不通过", "fail"):
                decision = "拒绝"
            elif decision_raw in ("待定", "pending"):
                decision = "待定"
            elif decision_raw in ("关闭", "close", "closed"):
                decision = "关闭"
            elif decision_raw in ("完成", "completed", "done", "finish"):
                decision = "完成"
            elif decision_raw in ("延期"):
                decision = "待定"  # 延期映射为待定
            elif decision_raw in ("补充材料"):
                decision = "待定"  # 补充材料映射为待定
            else:
                decision = "待定"

            # 清理项目名称中的前缀如 "项目：" "项目:"
            project = re.sub(r'^[\s\-]*(?:项目|Project)[\s:：]+', '', project, flags=re.IGNORECASE).strip()

            result["resolutions"].append({
                "project": project,
                "decision": decision,
                "budget": budget,
                "note": note
            })

        # 如果上面的正则没匹配到，尝试更宽松的格式：Markdown 列表行
        if not result["resolutions"]:
            for line in lines:
                # 匹配 - 项目：XXX | 决议：通过 | 预算：50000
                m = re.search(
                    r'(?:项目|Project)[\s:：]+([^|]+)\s*[|\t]\s*'
                    r'(?:决议|Decision)[\s:：]+(通过|拒绝|待定|关闭|完成|延期|补充材料)\s*[|\t]\s*'
                    r'(?:预算|Budget)[\s:：]+([^|]*)\s*(?:[|\t]\s*(?:备注|Note)[\s:：]+(.*))?',
                    line, re.IGNORECASE
                )
                if m:
                    result["resolutions"].append({
                        "project": m.group(1).strip(),
                        "decision": self._standardize_decision(m.group(2).strip()),
                        "budget": m.group(3).strip(),
                        "note": m.group(4).strip() if m.group(4) else ""
                    })

        # 如果还是没匹配到，尝试最简单的格式：每行开头有项目名称和决议关键词
        if not result["resolutions"]:
            for line in lines[1:]:  # 跳过第一行（标题）
                # 查找包含决议关键词的行
                decision_keywords = {"通过": "通过", "拒绝": "拒绝", "待定": "待定",
                                     "关闭": "关闭", "完成": "完成",
                                     "延期": "待定", "补充材料": "待定",
                                     "否决": "拒绝", "不通过": "拒绝"}
                found_decision = None
                for kw, std in decision_keywords.items():
                    if kw in line:
                        found_decision = std
                        break
                if found_decision:
                    # 提取项目名称：通常是决议词前面的文本
                    parts = line.split(kw)
                    if len(parts) >= 2:
                        project_part = parts[0].strip()
                        # 去掉序号和符号
                        project_part = re.sub(r'^[\s\-\d\.\、]+', '', project_part).strip()
                        # 提取数字作为预算
                        budget_match = re.search(r'(\d[\d,\.]+)', line)
                        budget = budget_match.group(1) if budget_match else ""
                        # 备注取剩余部分
                        note = "".join(parts[1:]).strip()
                        note = re.sub(r'^[\s:：|]+', '', note).strip()
                        if project_part and len(project_part) > 1:
                            result["resolutions"].append({
                                "project": project_part,
                                "decision": found_decision,
                                "budget": budget,
                                "note": note
                            })

        return result

    def _parse_meeting_numbered_format(self, text):
        """解析委员会纪要的真实编号格式：
            2026.07.28 委员会决议纪要
            In-progress / Pending / Submitted 等状态小节
            1. 项目名：决议或预算（备注可跨行，支持 - 开头的补充行）
        返回决议列表；未识别到编号条目时返回空列表（由调用方回退到其他格式）。
        """
        import re
        SECTION_WORDS = {"in-progress", "in progress", "inprogress", "pending",
                         "submitted", "rejection", "completion", "closure",
                         "进行中", "待定", "已提交", "拒绝", "完成", "关闭"}
        SECTION_DEFAULT_DECISION = {"pending": "待定", "待定": "待定"}
        # 决议关键词（按优先级）：打款视为通过的决议；结项资助视为完成
        DECISION_PATTERNS = [
            (re.compile(r'拒绝|否决|不通过|reject', re.IGNORECASE), "拒绝"),
            (re.compile(r'中期打款|里程碑打款|打款'), "通过"),
            (re.compile(r'通过|approved|pass', re.IGNORECASE), "通过"),
            (re.compile(r'完成结项|结项|完成|complet|done|finish', re.IGNORECASE), "完成"),
            (re.compile(r'关闭|close', re.IGNORECASE), "关闭"),
            (re.compile(r'待定|pending', re.IGNORECASE), "待定"),
        ]
        # 预算：400,000 CKB / 900美金 / $1,000 / 2000 USD / 500U
        BUDGET_RE = re.compile(
            r'(\$\s*\d[\d,]*(?:\.\d+)?'
            r'|\d[\d,]*(?:\.\d+)?\s*(?:CKB|USD|USDT|美金|美元)'
            r'|\d[\d,]*(?:\.\d+)?\s*U(?![A-Za-z0-9_]))',
            re.IGNORECASE)
        # 编号条目：1. 项目名：后续内容（序号为1-2位数字，排除 2026.07.28 这类日期行）
        item_re = re.compile(r'^\s*(\d{1,2})\s*[.、．]\s*(.+?)\s*[:：]\s*(.*)$')

        lines = text.split('\n')
        items = []
        current_section = ""
        current = None

        def flush():
            nonlocal current
            if current:
                items.append(current)
                current = None

        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                continue
            # 状态小节标题（独占一行）
            sec_key = stripped.lower().rstrip(':：')
            if sec_key in SECTION_WORDS and len(stripped) < 20:
                flush()
                current_section = sec_key
                continue
            m = item_re.match(stripped)
            # 项目名中含 | 或制表符的是竖线格式（格式1），不当作编号条目，交给后续解析器
            if m and '|' not in m.group(2) and '\t' not in m.group(2):
                flush()
                current = {"project": m.group(2).strip(), "head": m.group(3).strip(),
                           "body": [], "section": current_section}
                continue
            if current is not None:
                current["body"].append(stripped)
        flush()

        resolutions = []
        for it in items:
            project = it["project"].strip()
            if not project or len(project) < 1:
                continue
            head = it["head"]
            body_lines = it["body"]
            full_text = "\n".join([head] + body_lines).strip()

            # 决议：先条目行，再备注首行（如"先完成结项资助"），最后按状态小节兜底
            decision = ""
            for pat, std in DECISION_PATTERNS:
                if pat.search(head):
                    decision = std
                    break
            if not decision and body_lines:
                for pat, std in DECISION_PATTERNS:
                    if pat.search(body_lines[0]):
                        decision = std
                        break
            if not decision:
                decision = SECTION_DEFAULT_DECISION.get(it["section"], "")

            # 预算：先条目行，再全文（如"委员会愿意给到900美金的资助"）
            budget = ""
            bm = BUDGET_RE.search(head) or BUDGET_RE.search(full_text)
            if bm:
                budget = bm.group(1).strip()

            # 备注：条目行去掉决议关键词和预算后若有剩余则保留，再接备注行
            note_parts = []
            head_rest = head
            for pat, _ in DECISION_PATTERNS:
                head_rest = pat.sub('', head_rest)
            if budget:
                head_rest = head_rest.replace(budget, '')
            head_rest = head_rest.strip(' \t-—:：,，')
            if head_rest:
                note_parts.append(head_rest)
            note_parts.extend(body_lines)

            resolutions.append({
                "project": project,
                "decision": decision,
                "budget": budget,
                "note": "\n".join(note_parts).strip(),
            })

        return resolutions

    def _delete_meeting(self):
        sel = self.meeting_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择会议")
            return
        idx = int(sel[0])
        if messagebox.askyesno("确认", "确定要删除这个会议纪要吗？"):
            meetings = self.data.get("meetings", [])
            if 0 <= idx < len(meetings):
                meetings.pop(idx)
                save_data(self.data)
                self._refresh_meeting_list()
                self._clear_meeting_detail()

    def _export_meetings(self):
        """将所有会议纪要以 Markdown 格式导出，供 AI 分析。"""
        meetings = self.data.get("meetings", [])
        if not meetings:
            messagebox.showinfo("提示", "暂无会议纪要可导出")
            return
        try:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"meetings_export_{timestamp}.md"
            filepath = os.path.join(_BASE_DIR, filename)
            lines = []
            lines.append("# Spark Program 委员会纪要汇总\n")
            lines.append(f"> 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            lines.append(f"> 会议总数: {len(meetings)}\n\n")
            for i, m in enumerate(meetings, 1):
                lines.append(f"## 会议 {i}: {m.get('title', '未命名')}\n")
                lines.append(f"- **日期**: {m.get('date', '')}\n")
                lines.append(f"- **Talk 主题**: {m.get('talk_topic_url', '')}\n")
                # 决议表格
                resolutions = m.get("resolutions", [])
                if resolutions:
                    lines.append("\n### 项目评审决议\n\n")
                    lines.append("| 项目名称 | 决议 | 轮次 | 预算 | 备注 | 主帖链接 | 关联帖链接 |\n")
                    lines.append("| --- | --- | --- | --- | --- | --- | --- |\n")
                    for r in resolutions:
                        main_url = r.get("main_post_url", "")
                        linked_url = r.get("talk_url", "")
                        main_link = f"[主帖]({main_url})" if main_url else ""
                        linked_link = f"[关联帖]({linked_url})" if linked_url else ""
                        lines.append(f"| {r.get('project', '')} | {r.get('decision', '')} | {r.get('round', '')} | {r.get('budget', '')} | {r.get('note', '')} | {main_link} | {linked_link} |\n")
                else:
                    lines.append("\n*暂无决议*\n")
                lines.append("\n---\n\n")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("".join(lines))
            messagebox.showinfo("导出成功", f"已导出到:\n{filepath}\n共 {len(meetings)} 次会议，{sum(len(m.get('resolutions', [])) for m in meetings)} 条决议")
        except Exception as e:
            messagebox.showwarning("导出失败", f"导出失败: {str(e)}")

    def _clear_meeting_detail(self):
        self._cancel_resolution_cell()
        self._current_meeting_idx = None
        self.meeting_date_var.set("")
        self.meeting_title_var.set("")
        self.meeting_url_var.set("")
        self._talk_link_proj_var.set("")
        self._talk_link_proj_combo.config(values=[])
        for item in self.resolution_tree.get_children():
            self.resolution_tree.delete(item)
        self.transcript_text.delete("1.0", tk.END)
        self.ai_summary_text.delete("1.0", tk.END)

    def _save_meeting_info(self):
        if self._current_meeting_idx is None:
            messagebox.showinfo("提示", "请先选择会议")
            return
        meetings = self.data.get("meetings", [])
        if 0 <= self._current_meeting_idx < len(meetings):
            m = meetings[self._current_meeting_idx]
            m["date"] = self.meeting_date_var.get().strip()
            m["title"] = self.meeting_title_var.get().strip()
            m["talk_topic_url"] = self.meeting_url_var.get().strip()
            m["transcript"] = self.transcript_text.get("1.0", tk.END).strip()
            m["ai_summary"] = self.ai_summary_text.get("1.0", tk.END).strip()
            save_data(self.data)
            self._refresh_meeting_list()
            messagebox.showinfo("已保存", "会议纪要已保存")

    def _open_meeting_talk(self):
        url = self.meeting_url_var.get().strip()
        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo("提示", "请先填写 Talk 帖子 URL")

    def _link_meeting_talk(self):
        """获取Talk帖子内容并保存到本地，关联到选定的项目。"""
        url = self.meeting_url_var.get().strip()
        if not url:
            messagebox.showinfo("提示", "请先填写 Talk 帖子 URL")
            return
        proj_name = self._talk_link_proj_var.get().strip()
        if not proj_name:
            messagebox.showinfo("提示", "请先从下拉菜单选择要关联的项目")
            return
        import re
        m = re.search(r"/t/([^/]+)/(\d+)", url)
        if not m:
            messagebox.showwarning("提示", "URL 格式不正确，应为 https://talk.nervos.org/t/slug/1234")
            return
        slug, post_id = m.group(1), m.group(2)
        try:
            json_url = f"https://talk.nervos.org/t/{slug}/{post_id}.json"
            req = urllib.request.Request(json_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = jsonlib.loads(resp.read().decode())
            post = data.get("post_stream", {}).get("posts", [{}])[0]
            cooked = post.get("cooked", "")
            text = re.sub(r"<[^>]+>", "", cooked).strip()
            title = data.get("title", "")
            # 保存到本地文件
            meetings = self.data.get("meetings", [])
            if self._current_meeting_idx is not None and 0 <= self._current_meeting_idx < len(meetings):
                meeting = meetings[self._current_meeting_idx]
                date = meeting.get("date", "unknown")
                safe_date = re.sub(r'[\\/:*?"<>|]', '_', date)
                dir_path = os.path.join(_BASE_DIR, "meetings", safe_date)
                os.makedirs(dir_path, exist_ok=True)
                filename = f"{slug}_{post_id}.md"
                filepath = os.path.join(dir_path, filename)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(f"# {title}\n\n")
                    f.write(f"URL: {url}\n\n")
                    f.write(f"Date: {date}\n\n")
                    f.write(text)
                # 更新关联项目
                for r in meeting.get("resolutions", []):
                    if r.get("project") == proj_name:
                        r["talk_url"] = url
                        r["talk_file"] = filepath
                        break
                save_data(self.data)
                self._on_meeting_select()
                messagebox.showinfo("完成", f"Talk 帖子内容已获取并保存到:\n{filepath}\n关联项目: {proj_name}")
        except Exception as e:
            messagebox.showwarning("失败", f"获取失败: {str(e)}")

    def _add_resolution_dialog(self):
        if self._current_meeting_idx is None:
            messagebox.showinfo("提示", "请先选择会议")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("添加评审决议")
        dlg.geometry("400x300")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=THEME["bg"])

        def lbl(parent, text, row):
            tk.Label(parent, text=text, bg=THEME["bg"], fg=THEME["text"],
                     font=("Microsoft YaHei", 10)).grid(row=row, column=0, sticky="w", padx=5, pady=3)

        lbl(dlg, "项目名称:", 0)
        proj_var = tk.StringVar()
        tk.Entry(dlg, textvariable=proj_var, width=35, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=0, column=1, padx=5, pady=3)

        lbl(dlg, "决议:", 1)
        decision_var = tk.StringVar(value="通过")
        ttk.Combobox(dlg, textvariable=decision_var,
                     values=["通过", "待定", "拒绝", "关闭", "完成"],
                     width=33, state="readonly").grid(row=1, column=1, padx=5, pady=3)

        lbl(dlg, "评审轮次:", 2)
        round_var = tk.StringVar(value="1")
        tk.Entry(dlg, textvariable=round_var, width=35, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=2, column=1, padx=5, pady=3)

        lbl(dlg, "预算/金额:", 3)
        budget_var = tk.StringVar()
        tk.Entry(dlg, textvariable=budget_var, width=35, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=3, column=1, padx=5, pady=3)

        lbl(dlg, "备注:", 4)
        note_text = tk.Text(dlg, width=33, height=2, font=("Microsoft YaHei", 9),
                            bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                            bd=1, relief=tk.FLAT)
        note_text.grid(row=4, column=1, padx=5, pady=3)

        lbl(dlg, "主帖 URL:", 5)
        main_url_var = tk.StringVar()
        tk.Entry(dlg, textvariable=main_url_var, width=35, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=5, column=1, padx=5, pady=3)

        def save():
            meetings = self.data.get("meetings", [])
            if 0 <= self._current_meeting_idx < len(meetings):
                proj_name = proj_var.get().strip()
                # 尝试从看板数据自动匹配主帖 URL
                main_url = main_url_var.get().strip()
                if not main_url:
                    matched = self._find_post_by_project_name(proj_name)
                    if matched:
                        main_url = matched
                meetings[self._current_meeting_idx].setdefault("resolutions", []).append({
                    "project": proj_name,
                    "decision": decision_var.get(),
                    "round": round_var.get().strip(),
                    "budget": budget_var.get().strip(),
                    "note": note_text.get("1.0", tk.END).strip(),
                    "main_post_url": main_url
                })
                save_data(self.data)
                self._on_meeting_select()
            dlg.destroy()

        tk.Button(dlg, text="保存", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 11, "bold"),
                  command=save).grid(row=6, column=0, columnspan=2, pady=12)

    def _standardize_decision(self, raw):
        """将各种决议表述标准化为5种：通过/待定/拒绝/关闭/完成。"""
        raw = raw.strip().lower()
        if raw in ("通过", "approved", "pass", "approve"):
            return "通过"
        elif raw in ("拒绝", "rejected", "reject", "否决", "不通过", "fail"):
            return "拒绝"
        elif raw in ("待定", "pending", "延期", "补充材料"):
            return "待定"
        elif raw in ("关闭", "close", "closed"):
            return "关闭"
        elif raw in ("完成", "completed", "done", "finish"):
            return "完成"
        return "待定"

    def _refresh_talk_link_dropdown(self):
        """刷新 Talk 关联项目下拉菜单。"""
        meetings = self.data.get("meetings", [])
        if self._current_meeting_idx is None or self._current_meeting_idx >= len(meetings):
            self._talk_link_proj_combo.config(values=[])
            self._talk_link_proj_var.set("")
            return
        m = meetings[self._current_meeting_idx]
        resolutions = m.get("resolutions", [])
        proj_names = [r.get("project", "") for r in resolutions if r.get("project", "")]
        self._talk_link_proj_combo.config(values=proj_names)
        if proj_names:
            self._talk_link_proj_var.set(proj_names[0])
        else:
            self._talk_link_proj_var.set("")

    def _on_resolution_double_click(self, event):
        """双击决议表格：数据列进入内联编辑，主帖/关联帖列打开链接。"""
        sel = self.resolution_tree.selection()
        if not sel:
            return
        region = self.resolution_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.resolution_tree.identify_column(event.x)
        idx = int(sel[0])
        # 主帖列(#6)和关联帖列(#7)保持打开链接行为
        if col in ("#6", "#7"):
            self._open_resolution_link(idx, col)
            return
        # 其他列进入内联编辑
        self._edit_resolution_cell(idx, col)

    def _open_resolution_link(self, idx, col):
        """打开决议的主帖或关联帖链接。主帖无URL时进入关联模式。"""
        meetings = self.data.get("meetings", [])
        if self._current_meeting_idx is None or self._current_meeting_idx >= len(meetings):
            return
        res = meetings[self._current_meeting_idx].get("resolutions", [])
        if 0 <= idx < len(res):
            r = res[idx]
            if col == "#6":  # 主帖
                main_url = r.get("main_post_url", "")
                if main_url:
                    webbrowser.open(main_url)
                else:
                    # 进入关联模式，跳转到看板页
                    self._start_link_to_board(idx, "main_post_url")
            elif col == "#7":  # 关联帖
                url = r.get("talk_url", "")
                if url:
                    webbrowser.open(url)
                else:
                    messagebox.showinfo("提示", "该项目尚未关联 Talk 帖子")

    def _start_link_to_board(self, resolution_idx, field):
        """启动关联模式：记录待关联状态，切换到看板页。"""
        if self._current_meeting_idx is None:
            messagebox.showinfo("提示", "请先选择会议")
            return
        meetings = self.data.get("meetings", [])
        if self._current_meeting_idx >= len(meetings):
            return
        res = meetings[self._current_meeting_idx].get("resolutions", [])
        if 0 <= resolution_idx >= len(res):
            messagebox.showinfo("提示", "无效决议")
            return
        r = res[resolution_idx]
        proj_name = r.get("project", "")
        self._pending_link = {
            "meeting_idx": self._current_meeting_idx,
            "resolution_idx": resolution_idx,
            "field": field,
            "project_name": proj_name
        }
        # 切换到看板页
        self.notebook.select(self.tab_projects)
        # 显示关联栏
        self._link_bar.pack(fill=tk.X, pady=5, before=self._stats_frame)
        self._link_status_label.config(
            text=f"🔗 关联模式：请在看板中选中 '{proj_name}' 的帖子并点击【关联】")

    def _link_selected_post(self):
        """在看板页获取选中帖子并关联到决议。"""
        if not self._pending_link:
            return
        # 查找当前选中的帖子
        selected_url = None
        selected_title = None
        for key, lb in self._project_tab_listboxes.items():
            sel = lb.curselection()
            if sel:
                idx = sel[0]
                cache = load_project_cache()
                if not cache or "classified" not in cache:
                    continue
                c = cache["classified"]
                posts = []
                if key == "official":
                    posts = c.get("spark_program", {}).get("official", [])
                elif key == "pre":
                    posts = c.get("spark_program", {}).get("pre_review", [])
                elif key == "non":
                    posts = c.get("spark_program", {}).get("non_proposal", [])
                else:
                    posts = c.get(key.lower().replace("-", "_"), [])
                if 0 <= idx < len(posts):
                    selected_url = posts[idx].get("url", "")
                    selected_title = posts[idx].get("title", "")
                    break
        if not selected_url:
            messagebox.showinfo("提示", "请先在列表中选中一个帖子")
            return
        # 写入数据
        meeting_idx = self._pending_link["meeting_idx"]
        resolution_idx = self._pending_link["resolution_idx"]
        field = self._pending_link["field"]
        meetings = self.data.get("meetings", [])
        if meeting_idx < len(meetings):
            res = meetings[meeting_idx].get("resolutions", [])
            if 0 <= resolution_idx < len(res):
                res[resolution_idx][field] = selected_url
                save_data(self.data)
                # 返回纪要页并刷新
                self._cancel_link_mode()
                self.notebook.select(self.tab_meetings)
                self._current_meeting_idx = meeting_idx
                self._on_meeting_select()
                messagebox.showinfo("关联成功", f"已关联帖子: {selected_title}")
                return
        self._cancel_link_mode()
        messagebox.showinfo("提示", "关联失败，请重试")

    def _cancel_link_mode(self):
        """取消关联模式，隐藏关联栏。"""
        self._pending_link = None
        self._link_bar.pack_forget()

    def _update_tone_knowledge(self):
        """调用 AI 分析所有会议决议纪要（含主帖、关联帖内容），生成回复措辞知识库。"""
        meetings = self.data.get("meetings", [])
        if not meetings:
            messagebox.showinfo("提示", "暂无会议纪要，无法生成知识库")
            return
        provider = self.data.get("ai_provider", "siliconflow")
        api_key = get_ai_api_key(provider)
        if not api_key:
            messagebox.showinfo("提示", "未配置 API Key，请先在 🤖 AI起草 标签页配置")
            return

        def _run():
            try:
                # 收集所有决议信息（含主帖、关联帖内容）
                all_lines = []
                for m in meetings:
                    all_lines.append(f"## 会议: {m.get('title', '')} ({m.get('date', '')})")
                    for r in m.get("resolutions", []):
                        all_lines.append(f"项目: {r.get('project', '')} | 决议: {r.get('decision', '')} | 轮次: {r.get('round', '')}")
                        all_lines.append(f"备注: {r.get('note', '')}")

                        # 获取主帖内容
                        main_url = r.get("main_post_url", "")
                        if main_url:
                            try:
                                html = self._fetch_talk_post(main_url)
                                post = self._parse_talk_post(html)
                                main_text = post.get("content", "")[:1500]
                                if main_text:
                                    all_lines.append(f"主帖内容摘要: {main_text}")
                            except Exception:
                                pass

                        # 获取关联帖内容
                        talk_url = r.get("talk_url", "")
                        talk_file = r.get("talk_file", "")
                        talk_text = ""
                        if talk_file and os.path.exists(talk_file):
                            try:
                                with open(talk_file, "r", encoding="utf-8") as f:
                                    talk_text = f.read()[:1500]
                            except Exception:
                                pass
                        elif talk_url:
                            try:
                                html = self._fetch_talk_post(talk_url)
                                post = self._parse_talk_post(html)
                                talk_text = post.get("content", "")[:1500]
                            except Exception:
                                pass
                        if talk_text:
                            all_lines.append(f"关联帖内容摘要: {talk_text}")
                        all_lines.append("")
                    all_lines.append("")
                all_content = "\n".join(all_lines)
                # 截断，避免超长
                if len(all_content) > 8000:
                    all_content = all_content[:8000] + "\n\n[内容截断，仅展示前 8000 字]"

                prompt = f"""你是一位专业的委员会联络员语气分析师。

请根据以下所有委员会评审决议纪要（包含项目主帖内容、关联帖内容、备注等），深度总结并提炼出回复申请人时的语气、立场、口吻、内容范围等关键要素。

要求输出格式为结构化的知识库，包含以下维度：
1. 语气特征（如：专业、礼貌、鼓励、严谨、委婉、直接等）
2. 立场原则（如：中立客观、支持创新、审慎评估、结果导向等）
3. 口吻风格（如：正式/非正式、主动/被动、以委员会身份发言、个人化等）
4. 内容范围（如：必须包含的要素、可选的要素、应避免的内容等）
5. 特殊规则（如：通过/拒绝/待定时的措辞差异、预算相关表述、技术方案评估措辞等）
6. 各项目差异（如：不同项目类型是否需要不同措辞风格）

请尽量精炼，控制在 300-500 字以内，便于后续作为 Prompt 上下文直接使用。

所有决议纪要（含主帖与关联帖内容）:
{all_content}"""
                content, err = call_ai_api(provider, api_key, prompt, "deepseek-ai/DeepSeek-V4-Flash")
                if err:
                    self.root.after(0, lambda: messagebox.showwarning("失败", f"AI 分析失败: {err}"))
                    return
                self.data["reply_tone_knowledge"] = content.strip()
                save_data(self.data)
                self.root.after(0, lambda: (
                    self.tone_knowledge_text.config(state=tk.NORMAL),
                    self.tone_knowledge_text.delete("1.0", tk.END),
                    self.tone_knowledge_text.insert("1.0", content.strip()),
                    self.tone_knowledge_text.config(state=tk.DISABLED),
                    messagebox.showinfo("更新成功", "回复措辞知识库已更新，已综合分析主帖与关联帖内容")
                ))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showwarning("失败", f"更新失败: {str(e)}"))
        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _edit_tone_knowledge(self):
        self.tone_knowledge_text.config(state=tk.NORMAL)
        messagebox.showinfo("编辑模式", "知识库已解锁，修改后点击【保存】")

    def _save_tone_knowledge(self):
        content = self.tone_knowledge_text.get("1.0", tk.END).strip()
        # 检测是否是委员会措辞 Skill（通过标志性标题）
        if "委员会措辞 Skill" in content:
            skill_path = os.path.join(_BASE_DIR, "committee_phrasing_skill.md")
            try:
                with open(skill_path, "w", encoding="utf-8") as f:
                    f.write(content)
                self.tone_knowledge_text.config(state=tk.DISABLED)
                messagebox.showinfo("保存成功", "委员会措辞 Skill 已更新")
            except Exception as e:
                messagebox.showwarning("保存失败", str(e))
        else:
            self.data["reply_tone_knowledge"] = content
            save_data(self.data)
            self.tone_knowledge_text.config(state=tk.DISABLED)
            messagebox.showinfo("保存成功", "回复措辞知识库已保存")

    def _edit_committee_skill(self):
        """打开委员会措辞 Skill 文件进行编辑。"""
        import subprocess
        skill_path = os.path.join(_BASE_DIR, "committee_phrasing_skill.md")
        if os.path.exists(skill_path):
            # 读取并显示在知识库编辑区
            try:
                with open(skill_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.tone_knowledge_text.config(state=tk.NORMAL)
                self.tone_knowledge_text.delete("1.0", tk.END)
                self.tone_knowledge_text.insert("1.0", content)
                messagebox.showinfo("编辑模式", "已加载委员会措辞 Skill，修改后点击【保存】可更新 Skill 文件")
            except Exception as e:
                messagebox.showwarning("读取失败", str(e))
        else:
            messagebox.showinfo("提示", "委员会措辞 Skill 文件不存在，请先运行 AI 分析生成")

    def _save_committee_skill(self):
        """保存委员会措辞 Skill 文件。"""
        content = self.tone_knowledge_text.get("1.0", tk.END).strip()
        skill_path = os.path.join(_BASE_DIR, "committee_phrasing_skill.md")
        try:
            with open(skill_path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("保存成功", "委员会措辞 Skill 已更新")
        except Exception as e:
            messagebox.showwarning("保存失败", str(e))

    def _edit_character(self, char):
        """编辑人物内容对话框。"""
        dlg = tk.Toplevel(self.root)
        dlg.title(f"编辑人物: {char.get('name', '')}")
        dlg.geometry("700x500")
        dlg.configure(bg=THEME["bg"])
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text=f"人物: {char.get('display_name', char['name'])}", bg=THEME["bg"],
                 fg=THEME["accent"], font=("Microsoft YaHei", 12, "bold")).pack(pady=5)

        text = scrolledtext.ScrolledText(dlg, font=("Microsoft YaHei", 10), wrap=tk.WORD,
                                          bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                                          relief=tk.FLAT, bd=2, height=20)
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        text.insert("1.0", char.get("content", ""))

        def _save():
            char["content"] = text.get("1.0", tk.END).strip()
            for i, c in enumerate(self.data.get("characters", [])):
                if c["id"] == char["id"]:
                    self.data["characters"][i] = char
                    break
            save_data(self.data)
            dlg.destroy()
            messagebox.showinfo("保存成功", f"人物 {char['name']} 已更新")

        tk.Button(dlg, text="💾 保存", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 10, "bold"),
                  command=_save).pack(pady=5)

    def _save_character_states(self):
        """保存人物的启用/禁用状态。"""
        for char in self.data.get("characters", []):
            char["enabled"] = self._character_vars.get(char["id"], tk.BooleanVar(value=True)).get()
        save_data(self.data)
        messagebox.showinfo("保存成功", "人物状态已保存")

    def _reset_characters(self):
        """重置人物为默认数据。"""
        if not messagebox.askyesno("确认", "确定要重置所有人物为默认数据吗？\n自定义编辑的内容将丢失。"):
            return
        self.data["characters"] = [dict(c) for c in DEFAULT_CHARACTERS]
        save_data(self.data)
        # 刷新UI - 需要重新构建人物区域
        for widget in self.tab_knowledge.winfo_children():
            widget.destroy()
        self._build_tab_knowledge()
        messagebox.showinfo("重置完成", "人物已恢复为默认数据")

    def _edit_resolution_cell(self, idx, col):
        """在 Treeview 单元格上方创建内联编辑控件。"""
        # 销毁之前的编辑控件
        self._cancel_resolution_cell()

        # 列映射
        col_map = {"#1": "project", "#2": "decision", "#3": "round",
                   "#4": "budget", "#5": "note", "#6": "main_post_url", "#7": "talk_url"}
        col_id = col_map.get(col)
        if not col_id:
            return

        # 获取当前值
        meetings = self.data.get("meetings", [])
        if self._current_meeting_idx is None or self._current_meeting_idx >= len(meetings):
            return
        res = meetings[self._current_meeting_idx].get("resolutions", [])
        if 0 <= idx >= len(res):
            return
        r = res[idx]
        current_value = r.get(col_id, "")

        # 获取单元格位置
        bbox = self.resolution_tree.bbox(str(idx), column=col)
        if not bbox:
            return
        x, y, w, h = bbox

        # 创建编辑控件
        if col_id == "decision":
            widget = ttk.Combobox(self.resolution_tree, values=["通过", "待定", "拒绝", "关闭", "完成"],
                                  state="readonly", width=max(1, w // 10))
            widget.set(current_value)
        else:
            widget = tk.Entry(self.resolution_tree, width=max(1, w // 8),
                              bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                              bd=1, relief=tk.FLAT)
            widget.insert(0, current_value)
            widget.select_range(0, tk.END)

        widget.place(x=x, y=y, width=w, height=h)
        widget.focus_set()
        self._resolution_edit_widget = widget
        self._resolution_edit_idx = idx
        self._resolution_edit_col = col_id

        # 绑定事件
        widget.bind("<Return>", lambda e: self._save_resolution_cell())
        widget.bind("<FocusOut>", lambda e: self._save_resolution_cell())
        widget.bind("<Escape>", lambda e: self._cancel_resolution_cell())
        if col_id == "decision":
            widget.bind("<<ComboboxSelected>>", lambda e: self._save_resolution_cell())

    def _save_resolution_cell(self):
        """保存单元格编辑结果并销毁编辑控件。"""
        if not hasattr(self, '_resolution_edit_widget') or not self._resolution_edit_widget or not self._resolution_edit_widget.winfo_exists():
            return
        widget = self._resolution_edit_widget
        idx = self._resolution_edit_idx
        col_id = self._resolution_edit_col
        new_value = widget.get().strip() if hasattr(widget, 'get') else widget.get()

        # 更新数据
        meetings = self.data.get("meetings", [])
        if self._current_meeting_idx is not None and 0 <= self._current_meeting_idx < len(meetings):
            res = meetings[self._current_meeting_idx].get("resolutions", [])
            if 0 <= idx < len(res):
                r = res[idx]
                r[col_id] = new_value
                save_data(self.data)
                # 刷新显示（内部会调用 _cancel_resolution_cell）
                self._on_meeting_select()
                # 如果修改了项目名称，刷新 Talk 关联下拉
                if col_id == "project":
                    self._refresh_talk_link_dropdown()
                return
        # 没有有效数据，取消编辑
        self._cancel_resolution_cell()

    def _cancel_resolution_cell(self):
        """取消单元格编辑并销毁编辑控件。"""
        if hasattr(self, '_resolution_edit_widget') and self._resolution_edit_widget and self._resolution_edit_widget.winfo_exists():
            self._resolution_edit_widget.destroy()
        self._resolution_edit_widget = None
        self._resolution_edit_idx = None
        self._resolution_edit_col = None

    def _refresh_resolution_posts(self):
        """重新从看板数据匹配所有决议的主帖 URL。"""
        if self._current_meeting_idx is None:
            messagebox.showinfo("提示", "请先选择会议")
            return
        meetings = self.data.get("meetings", [])
        if self._current_meeting_idx >= len(meetings):
            return
        m = meetings[self._current_meeting_idx]
        resolutions = m.get("resolutions", [])
        if not resolutions:
            messagebox.showinfo("提示", "该会议暂无决议项目")
            return
        matched = 0
        for r in resolutions:
            proj_name = r.get("project", "")
            if not proj_name:
                continue
            matched_url = self._find_post_by_project_name(proj_name)
            if matched_url:
                r["main_post_url"] = matched_url
                matched += 1
        save_data(self.data)
        self._on_meeting_select()
        messagebox.showinfo("刷新完成", f"已重新匹配 {matched}/{len(resolutions)} 个项目的主帖")

    def _delete_resolution(self):
        self._cancel_resolution_cell()
        sel = self.resolution_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择决议")
            return
        idx = int(sel[0])
        meetings = self.data.get("meetings", [])
        if self._current_meeting_idx is not None and 0 <= self._current_meeting_idx < len(meetings):
            res = meetings[self._current_meeting_idx].get("resolutions", [])
            if 0 <= idx < len(res):
                res.pop(idx)
                save_data(self.data)
                self._on_meeting_select()

    def _on_resolution_select(self, event=None):
        self._cancel_resolution_cell()

    def _ai_summarize_meeting(self):
        transcript = self.transcript_text.get("1.0", tk.END).strip()
        if not transcript or "通义听悟转写文本将显示在这里" in transcript or "导入转写文本后点击" in transcript:
            messagebox.showinfo("提示", "请先导入通义听悟转写文本")
            return
        provider = self.data.get("ai_provider", "siliconflow")
        api_key = get_ai_api_key(provider)
        model = self.data.get("ai_model", "deepseek-ai/DeepSeek-V3")
        if not api_key:
            messagebox.showwarning("提示", "请先配置 AI API Key")
            return
        self._audio_status_label.config(text="⏳ AI 总结中...", fg=THEME["warning"])
        self.root.update()

        def _run():
            try:
                prompt = (
                    f"以下是一段委员会会议的录音转写文字。请总结为结构化的会议纪要，"
                    f"包含：1.会议时间/背景 2.各项目讨论要点 3.最终决议（通过/拒绝/待定等）。\n\n"
                    f"转写内容：\n{transcript[:6000]}\n\n"
                    f"请输出中文会议纪要，格式清晰。"
                )
                summary, err = call_ai_api(provider, api_key, prompt, model)
                self.root.after(0, lambda: self._on_summary_done(summary, err))
            except Exception as e:
                self.root.after(0, lambda: self._on_summary_done(None, str(e)))
        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _save_meeting_link(self):
        """保存 Google Meet 会议链接到全局配置。"""
        link = self.meeting_link_var.get().strip()
        self.data["meet_link"] = link
        save_data(self.data)
        self._audio_status_label.config(text="✅ 会议链接已保存", fg=THEME["success"])

    def _open_meeting_link(self):
        """打开全局保存的 Google Meet 会议链接。"""
        link = self.meeting_link_var.get().strip()
        if not link:
            # 如果没有当前输入，尝试从全局数据读取
            link = self.data.get("meet_link", "").strip()
            if link:
                self.meeting_link_var.set(link)
            else:
                messagebox.showinfo("提示", "会议链接为空，请先粘贴链接并保存")
                return
        import webbrowser
        webbrowser.open(link)

    def _on_summary_done(self, summary, err):
        if err:
            self._audio_status_label.config(text=f"总结失败: {err[:40]}", fg=THEME["danger"])
            messagebox.showwarning("总结失败", err)
            return
        self.ai_summary_text.delete("1.0", tk.END)
        self.ai_summary_text.insert("1.0", summary)
        self._audio_status_label.config(text="✅ AI 总结完成", fg=THEME["success"])
        if self._current_meeting_idx is not None:
            meetings = self.data.get("meetings", [])
            if 0 <= self._current_meeting_idx < len(meetings):
                meetings[self._current_meeting_idx]["ai_summary"] = summary
                save_data(self.data)

    # =================== 任务操作 ===================

    def refresh_task_list(self):
        for item in self.task_tree.get_children():
            self.task_tree.delete(item)
        filt = self.task_filter.get()
        for idx, task in enumerate(self.data.get("tasks", [])):
            status = "✅" if task.get("done") else "⏳"
            if filt == "已完成" and not task.get("done"):
                continue
            if filt == "未完成" and task.get("done"):
                continue
            sel = "☑" if idx in self._selected_task_indices else "☐"
            self.task_tree.insert("", tk.END, iid=str(idx),
                values=(sel, task.get("title", ""), status, task.get("type", ""),
                        task.get("project", ""), task.get("deadline", ""),
                        task.get("created", "")))
        self._update_select_all_btn()

    def on_task_select(self, event=None):
        sel = self.task_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        tasks = self.data.get("tasks", [])
        if 0 <= idx < len(tasks):
            task = tasks[idx]
            detail = f"任务: {task.get('title', '')}\n"
            detail += f"类型: {task.get('type', '')}\n"
            detail += f"关联项目: {task.get('project', '')}\n"
            detail += f"状态: {'已完成' if task.get('done') else '未完成'}\n"
            detail += f"截止日期: {task.get('deadline', '')}\n"
            detail += f"创建日期: {task.get('created', '')}\n"
            if task.get("link"):
                detail += f"链接: {task.get('link')}\n"
            detail += f"\n备注:\n{task.get('note', '')}"
            self.task_detail.config(state=tk.NORMAL)
            self.task_detail.delete("1.0", tk.END)
            self.task_detail.insert("1.0", detail)
            self.task_detail.config(state=tk.DISABLED)

    def add_task_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("添加新任务")
        dlg.geometry("450x450")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=THEME["bg"])

        def lbl(parent, text, row):
            tk.Label(parent, text=text, bg=THEME["bg"], fg=THEME["text"],
                     font=("Microsoft YaHei", 10)).grid(row=row, column=0, sticky="w", padx=5, pady=3)

        lbl(dlg, "任务名称:", 0)
        title_var = tk.StringVar()
        tk.Entry(dlg, textvariable=title_var, width=40, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=0, column=1, padx=5, pady=3)

        lbl(dlg, "任务类型:", 1)
        type_var = tk.StringVar(value="更新 Notion Dashboard")
        ttk.Combobox(dlg, textvariable=type_var, values=[
            "更新 Notion Dashboard", "Nervos Talk 回帖", "Nervos Talk 发帖",
            "周报跟进", "中期审核", "结项处理", "其他"
        ], width=38, state="readonly").grid(row=1, column=1, padx=5, pady=3)

        lbl(dlg, "关联项目:", 2)
        proj_var = tk.StringVar()
        projects = [""] + [p["name"] for p in ONGOING_PROJECTS] + [p["name"] for p in SUBMITTED_PROJECTS] + ["通用/全体"]
        ttk.Combobox(dlg, textvariable=proj_var, values=projects, width=38, state="readonly").grid(row=2, column=1, padx=5, pady=3)

        lbl(dlg, "截止日期:", 3)
        deadline_var = tk.StringVar(value=datetime.date.today().strftime("%Y-%m-%d"))
        tk.Entry(dlg, textvariable=deadline_var, width=40, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=3, column=1, padx=5, pady=3)

        lbl(dlg, "链接:", 4)
        link_var = tk.StringVar()
        tk.Entry(dlg, textvariable=link_var, width=40, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=4, column=1, padx=5, pady=3)

        lbl(dlg, "备注:", 5)
        note_text = tk.Text(dlg, width=38, height=6, font=("Microsoft YaHei", 10),
                            bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                            bd=1, relief=tk.FLAT)
        note_text.grid(row=5, column=1, padx=5, pady=3)

        def save():
            title = title_var.get().strip()
            if not title:
                messagebox.showwarning("提示", "请输入任务名称", parent=dlg)
                return
            task = {
                "title": title, "type": type_var.get(), "project": proj_var.get(),
                "deadline": deadline_var.get().strip(),
                "note": note_text.get("1.0", tk.END).strip(),
                "link": link_var.get().strip(),
                "created": datetime.date.today().strftime("%Y-%m-%d"), "done": False
            }
            self.data.setdefault("tasks", []).append(task)
            save_data(self.data)
            self._selected_task_indices.clear()
            self.refresh_task_list()
            dlg.destroy()

        tk.Button(dlg, text="保存", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 11, "bold"),
                  command=save).grid(row=6, column=0, columnspan=2, pady=12)

    def quick_add_task(self, title, project=""):
        task_type = "其他"
        if "Notion" in title: task_type = "更新 Notion Dashboard"
        elif "回帖" in title: task_type = "Nervos Talk 回帖"
        elif "发帖" in title: task_type = "Nervos Talk 发帖"
        elif "周报" in title: task_type = "周报跟进"
        elif "审核" in title or "中期" in title: task_type = "中期审核"
        elif "结项" in title or "报告" in title: task_type = "结项处理"
        task = {
            "title": title, "type": task_type, "project": project,
            "deadline": "", "note": "",
            "created": datetime.date.today().strftime("%Y-%m-%d"), "done": False
        }
        self.data.setdefault("tasks", []).append(task)
        save_data(self.data)
        self._selected_task_indices.clear()
        self.refresh_task_list()
        self.notebook.select(self.tab_tasks)

    def _on_task_click(self, event):
        """点击任务列表复选框切换选择状态。"""
        region = self.task_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.task_tree.identify_column(event.x)
        if col != "#1":
            return
        row = self.task_tree.identify_row(event.y)
        if not row:
            return
        idx = int(row)
        if idx in self._selected_task_indices:
            self._selected_task_indices.remove(idx)
            self.task_tree.set(row, "sel", "☐")
        else:
            self._selected_task_indices.add(idx)
            self.task_tree.set(row, "sel", "☑")
        self._update_select_all_btn()

    def _update_select_all_btn(self):
        """更新全选按钮文字和已选计数。"""
        all_selected = True
        has_rows = False
        for row in self.task_tree.get_children():
            has_rows = True
            idx = int(row)
            if idx not in self._selected_task_indices:
                all_selected = False
                break
        if not has_rows:
            self._select_all_btn.config(text="☐ 全选")
        elif all_selected:
            self._select_all_btn.config(text="☑ 取消全选")
        else:
            self._select_all_btn.config(text="☐ 全选")
        self._batch_count_label.config(text=f"已选: {len(self._selected_task_indices)}")

    def _select_all_tasks(self):
        """全选/取消全选当前可见任务。"""
        all_selected = True
        has_rows = False
        for row in self.task_tree.get_children():
            has_rows = True
            idx = int(row)
            if idx not in self._selected_task_indices:
                all_selected = False
                break
        if all_selected and has_rows:
            for row in self.task_tree.get_children():
                idx = int(row)
                self._selected_task_indices.discard(idx)
                self.task_tree.set(row, "sel", "☐")
        else:
            for row in self.task_tree.get_children():
                idx = int(row)
                self._selected_task_indices.add(idx)
                self.task_tree.set(row, "sel", "☑")
        self._update_select_all_btn()

    def _batch_complete_tasks(self):
        """一键完成所有选中的任务。"""
        if not self._selected_task_indices:
            messagebox.showinfo("提示", "请先选择任务")
            return
        tasks = self.data.get("tasks", [])
        count = 0
        blocked = 0
        for idx in self._selected_task_indices:
            if 0 <= idx < len(tasks):
                if not tasks[idx].get("done"):
                    task = tasks[idx]
                    if task.get("notion_step"):
                        blocked += 1
                    else:
                        task["done"] = True
                        count += 1
        if count > 0:
            save_data(self.data)
            self._selected_task_indices.clear()
            self.refresh_task_list()
            suffix = f"；{blocked} 个 Notion 任务须写入并回读核验后自动完成" if blocked else ""
            messagebox.showinfo("完成", f"已完成 {count} 个任务{suffix}")
        elif blocked:
            messagebox.showinfo("提示", "Notion 任务不能手动完成；写入并回读核验成功后会自动勾选")
        else:
            messagebox.showinfo("提示", "选中的任务已经是完成状态")

    def _batch_delete_tasks(self):
        """一键删除所有选中的任务。"""
        if not self._selected_task_indices:
            messagebox.showinfo("提示", "请先选择任务")
            return
        if messagebox.askyesno("确认", f"确定要删除选中的 {len(self._selected_task_indices)} 个任务吗？"):
            tasks = self.data.get("tasks", [])
            for idx in sorted(self._selected_task_indices, reverse=True):
                if 0 <= idx < len(tasks):
                    tasks.pop(idx)
            save_data(self.data)
            self._selected_task_indices.clear()
            self.refresh_task_list()
            self.task_detail.config(state=tk.NORMAL)
            self.task_detail.delete("1.0", tk.END)
            self.task_detail.insert("1.0", "点击任务查看详情...")
            self.task_detail.config(state=tk.DISABLED)
            messagebox.showinfo("已删除", "选中任务已删除")

    def toggle_task_status(self):
        sel = self.task_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个任务")
            return
        idx = int(sel[0])
        tasks = self.data.get("tasks", [])
        if 0 <= idx < len(tasks):
            if tasks[idx].get("notion_step") and not tasks[idx].get("done"):
                messagebox.showinfo("提示", "Notion 任务不能手动完成；写入并回读核验成功后会自动勾选")
                return
            tasks[idx]["done"] = not tasks[idx].get("done", False)
            save_data(self.data)
            self._selected_task_indices.clear()
            self.refresh_task_list()
            self.on_task_select()

    def delete_task(self):
        sel = self.task_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个任务")
            return
        idx = int(sel[0])
        if messagebox.askyesno("确认", "确定要删除这个任务吗？"):
            tasks = self.data.get("tasks", [])
            if 0 <= idx < len(tasks):
                tasks.pop(idx)
                save_data(self.data)
                self._selected_task_indices.clear()
                self.refresh_task_list()
                self.task_detail.config(state=tk.NORMAL)
                self.task_detail.delete("1.0", tk.END)
                self.task_detail.insert("1.0", "点击任务查看详情...")
                self.task_detail.config(state=tk.DISABLED)

    def open_task_link(self):
        """打开选中任务附带的链接（如 Notion 更新任务的对应页面）。"""
        sel = self.task_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一条任务")
            return
        idx = int(sel[0])
        tasks = self.data.get("tasks", [])
        if 0 <= idx < len(tasks):
            link = (tasks[idx].get("link") or "").strip()
            if link:
                webbrowser.open(link)
            else:
                messagebox.showinfo("提示", "该任务没有关联链接")

    def copy_task(self):
        sel = self.task_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        tasks = self.data.get("tasks", [])
        if 0 <= idx < len(tasks):
            text = f"{tasks[idx].get('title', '')}\n{tasks[idx].get('note', '')}"
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            messagebox.showinfo("已复制", "任务内容已复制到剪贴板")

    def _on_task_double_click(self, event):
        """双击任务名称打开帖子链接。删除标签任务自动打开无痕窗口。"""
        sel = self.task_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        tasks = self.data.get("tasks", [])
        if 0 <= idx < len(tasks):
            task = tasks[idx]
            note = task.get("note", "")
            import re
            urls = re.findall(r"https?://[^\s\n]+", note)
            if not urls:
                messagebox.showinfo("提示", "该任务没有包含可打开的链接")
                return
            url = urls[0]
            # 删除标签任务直接打开无痕窗口
            if task.get("type") == "删除标签":
                self._open_incognito_browser(url)
            else:
                webbrowser.open(url)

    def _find_browser_exe(self):
        """查找系统中可用的浏览器路径。优先 Chrome，其次 Edge。"""
        import os
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def _open_incognito_browser(self, url):
        """启动无痕浏览器打开指定 URL。"""
        import subprocess, os
        browser = self._find_browser_exe()
        if not browser:
            messagebox.showwarning("提示", "未找到 Chrome 或 Edge 浏览器。请手动打开浏览器。")
            return
        args = [browser]
        if "chrome" in browser.lower():
            args.extend(["--incognito", url])
        elif "edge" in browser.lower() or "msedge" in browser.lower():
            args.extend(["--inprivate", url])
        else:
            args.append(url)
        try:
            subprocess.Popen(args, shell=False)
        except Exception as e:
            messagebox.showwarning("错误", f"启动浏览器失败: {str(e)}")

    def on_template_select(self, event=None):
        sel = self.template_listbox.curselection()
        if not sel:
            return
        name = self.template_listbox.get(sel[0])
        info = TEMPLATES.get(name, {})
        self.template_desc.config(text=info.get("desc", ""))
        # 如果正在编辑同一个模板，保持当前文本框内容
        if self._template_editing_name == name:
            return
        # 如果正在编辑不同模板，提示是否保存
        if self._template_editing_name is not None:
            if messagebox.askyesno("保存提示", f"正在编辑【{self._template_editing_name}】，是否先保存？"):
                self.save_template()
            self._template_editing_name = None
        self.template_text.config(state=tk.NORMAL)
        self.template_text.delete("1.0", tk.END)
        self.template_text.insert("1.0", info.get("template", ""))
        self.template_text.config(state=tk.DISABLED)

    def copy_template(self):
        content = self.template_text.get("1.0", tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        messagebox.showinfo("已复制", "模板内容已复制到剪贴板")

    def edit_template(self):
        sel = self.template_listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在左侧选择一个模板")
            return
        name = self.template_listbox.get(sel[0])
        self._template_editing_name = name
        self.template_text.config(state=tk.NORMAL)
        messagebox.showinfo("编辑模式", f"正在编辑【{name}】，修改后请点击保存")

    def save_template(self):
        sel = self.template_listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在左侧选择一个模板")
            return
        name = self.template_listbox.get(sel[0])
        content = self.template_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("提示", "模板内容为空，无法保存")
            return
        TEMPLATES[name]["template"] = content
        # 持久化到数据文件
        if "templates" not in self.data:
            self.data["templates"] = {}
        self.data["templates"][name] = {"template": content}
        save_data(self.data)
        self._template_editing_name = None
        self.template_text.config(state=tk.DISABLED)
        messagebox.showinfo("保存成功", f"模板【{name}】已保存并持久化到数据文件")

    def create_task_from_template(self):
        sel = self.template_listbox.curselection()
        if not sel:
            return
        name = self.template_listbox.get(sel[0])
        self.quick_add_task(f"使用【{name}】模板回帖/发帖", "")

    def _ai_draft(self):
        """AI 起草：选择项目+模板，直接调用 API 生成最终草稿。支持 Kimi / Silicon Flow。"""
        sel = self.template_listbox.curselection()
        if not sel:
            messagebox.showwarning("提示", "请先从左侧选择一个回复模板")
            return
        template_name = self.template_listbox.get(sel[0])
        template_info = TEMPLATES.get(template_name, {})
        template_text = template_info.get("template", "")
        template_desc = template_info.get("desc", "")

        self._current_prompt = ""

        dlg = tk.Toplevel(self.root)
        dlg.title("🤖 AI 起草回复")
        dlg.geometry("700x650")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=THEME["bg"])

        # ===== Provider 选择 =====
        provider_frame = tk.LabelFrame(dlg, text="  AI 服务商  ", bg=THEME["bg"], fg=THEME["accent"],
                                        font=("Microsoft YaHei", 10, "bold"), bd=1, relief=tk.FLAT,
                                        highlightbackground=THEME["border"], highlightthickness=1)
        provider_frame.pack(fill=tk.X, padx=10, pady=5)

        provider_var = tk.StringVar(value=self.data.get("ai_provider", "siliconflow"))
        model_var = tk.StringVar(value=self.data.get("ai_model", ""))

        pf = tk.Frame(provider_frame, bg=THEME["bg"])
        pf.pack(fill=tk.X, padx=5, pady=5)

        tk.Radiobutton(pf, text="Kimi (Moonshot)", variable=provider_var, value="kimi",
                       bg=THEME["bg"], fg=THEME["text"], selectcolor=THEME["card"],
                       font=("Microsoft YaHei", 10), command=lambda: update_model_list()).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(pf, text="Silicon Flow", variable=provider_var, value="siliconflow",
                       bg=THEME["bg"], fg=THEME["text"], selectcolor=THEME["card"],
                       font=("Microsoft YaHei", 10), command=lambda: update_model_list()).pack(side=tk.LEFT, padx=5)

        model_frame = tk.Frame(provider_frame, bg=THEME["bg"])
        model_frame.pack(fill=tk.X, padx=5, pady=5)
        tk.Label(model_frame, text="模型:", bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        model_combo = ttk.Combobox(model_frame, textvariable=model_var, width=40, state="readonly")
        model_combo.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        MODELS = {
            "kimi": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "moonshot-v1-auto"],
            "siliconflow": [
                "deepseek-ai/DeepSeek-V4-Flash",
                "deepseek-ai/DeepSeek-V3",
                "deepseek-ai/DeepSeek-R1",
                "Qwen/Qwen3.6-27B",
                "Qwen/Qwen3-235B-A22B",
                "Qwen/QwQ-32B",
                "Pro/moonshotai/Kimi-K2.6",
                "Pro/Qwen/Qwen3-235B-A22B",
                "zai-org/GLM-5.2",
                "Pro/MiniMaxAI/MiniMax-M2.5"
            ]
        }

        def update_model_list():
            p = provider_var.get()
            models = MODELS.get(p, [])
            model_combo['values'] = models
            current = model_var.get()
            if current not in models:
                model_var.set(models[0] if models else "")

        update_model_list()

        # ===== API Key =====
        api_frame = tk.LabelFrame(dlg, text="  API Key  ", bg=THEME["bg"], fg=THEME["accent"],
                                   font=("Microsoft YaHei", 10, "bold"), bd=1, relief=tk.FLAT,
                                   highlightbackground=THEME["border"], highlightthickness=1)
        api_frame.pack(fill=tk.X, padx=10, pady=5)

        api_key_var = tk.StringVar(value=get_ai_api_key(provider_var.get()))
        has_key = bool(api_key_var.get().strip())

        api_status = tk.Label(api_frame, text="✅ 已设置" if has_key else "❌ 未设置",
                              bg=THEME["bg"], fg=THEME["success"] if has_key else THEME["danger"],
                              font=("Microsoft YaHei", 10, "bold"))
        api_status.pack(side=tk.LEFT, padx=8, pady=5)

        api_entry = tk.Entry(api_frame, textvariable=api_key_var, width=50,
                             bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                             bd=1, relief=tk.FLAT, show="●")
        api_entry.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.X, expand=True)

        def toggle_api_key():
            api_entry.config(show="" if api_entry.cget("show") == "●" else "●")

        tk.Button(api_frame, text="👁", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 10),
                  command=toggle_api_key).pack(side=tk.LEFT, padx=5, pady=5)

        def save_settings():
            provider = provider_var.get()
            self.data["ai_provider"] = provider
            self.data["ai_model"] = model_var.get()
            set_ai_api_key(api_key_var.get().strip(), provider)
            save_data(self.data)
            key = api_key_var.get().strip()
            api_status.config(text="✅ 已设置" if key else "❌ 未设置",
                              fg=THEME["success"] if key else THEME["danger"])

        tk.Button(api_frame, text="💾 保存", bg=THEME["card"], fg=THEME["text"],
                  activebackground=THEME["accent"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 10),
                  command=save_settings).pack(side=tk.LEFT, padx=5, pady=5)

        # ===== 项目选择 =====
        tk.Label(dlg, text="选择项目帖子:", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", padx=10, pady=5)

        proj_frame = tk.Frame(dlg, bg=THEME["bg"])
        proj_frame.pack(fill=tk.X, padx=10, pady=5)

        proj_var = tk.StringVar()
        proj_urls = {}
        proj_list = ["-- 手动输入 URL --"]
        # 从看板缓存加载 Pre-review / Submitted / Pending / In-Progress 帖子
        cache = load_project_cache()
        if cache and "classified" in cache:
            c = cache["classified"]
            for label, key in [("[Pre-review]", "pre_review"), ("[Submitted]", "submitted"),
                               ("[Pending]", "pending"), ("[In-Progress]", "in_progress")]:
                for post in c.get("spark_program", {}).get(key, []) if key == "pre_review" else c.get(key, []):
                    title = post.get("title", "")
                    if title:
                        display = f"{label} {title}"
                        proj_list.append(display)
                        proj_urls[display] = post.get("url", "")
        proj_combo = ttk.Combobox(proj_frame, textvariable=proj_var, values=proj_list, width=55, state="readonly")
        proj_combo.pack(side=tk.LEFT, padx=3)
        proj_combo.set(proj_list[0])

        url_var = tk.StringVar()
        url_entry = tk.Entry(proj_frame, textvariable=url_var, width=45, bg=THEME["card"], fg=THEME["text"],
                             insertbackground=THEME["text"], bd=1, relief=tk.FLAT)
        url_entry.pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)

        def on_proj_change(*args):
            p = proj_var.get()
            if p in proj_urls:
                url = proj_urls[p]
                url_var.set(url)
            elif p == "-- 手动输入 URL --":
                url_var.set("")
        proj_var.trace("w", on_proj_change)

        # ===== 会议纪要手动选取 =====
        meeting_frame = tk.LabelFrame(dlg, text=" 会议纪要选取 ", bg=THEME["bg"], fg=THEME["accent2"],
                                       font=("Microsoft YaHei", 10, "bold"), bd=1, relief=tk.FLAT,
                                       highlightbackground=THEME["border"], highlightthickness=1)
        meeting_frame.pack(fill=tk.X, padx=10, pady=5)
        
        meeting_top = tk.Frame(meeting_frame, bg=THEME["bg"])
        meeting_top.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(meeting_top, text="选择会议:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=2)
        meeting_var = tk.StringVar(value="-- 选择会议 --")
        meeting_combo = ttk.Combobox(meeting_top, textvariable=meeting_var, width=45, 
                                      state="readonly", font=("Microsoft YaHei", 9))
        meeting_combo.pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)
        
        # 填充会议下拉框
        meetings = self.data.get("meetings", [])
        meeting_items = ["-- 选择会议 --"]
        meeting_map = {}
        for m in sorted(meetings, key=lambda x: x.get("date", ""), reverse=True):
            date = m.get("date", "")
            title = m.get("title", "")
            display = f"{date} {title}"
            meeting_items.append(display)
            resolutions = m.get("resolutions", [])
            if resolutions:
                info_lines = []
                for r in resolutions:
                    info_lines.append(f"项目: {r.get('project', '')}")
                    info_lines.append(f"决议: {r.get('decision', '')}")
                    info_lines.append(f"轮次: {r.get('round', '')}")
                    info_lines.append(f"预算: {r.get('budget', '')}")
                    info_lines.append(f"备注: {r.get('note', '')}")
                    info_lines.append("---")
                meeting_map[display] = "\n".join(info_lines)
            else:
                meeting_map[display] = "该会议暂无决议记录。"
        meeting_combo['values'] = meeting_items
        
        load_meeting_btn = tk.Button(meeting_top, text="📋 加载决议", bg=THEME["accent2"], fg=THEME["bg"],
                                      activebackground=THEME["accent"], activeforeground=THEME["bg"],
                                      bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 9, "bold"))
        load_meeting_btn.pack(side=tk.LEFT, padx=2)
        
        meeting_info_text = tk.Text(meeting_frame, font=("Microsoft YaHei", 9), wrap=tk.WORD,
                                     bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                                     relief=tk.FLAT, bd=1, height=5)
        meeting_info_text.pack(fill=tk.X, padx=5, pady=3)
        meeting_info_text.insert("1.0", "从上方下拉菜单选择会议，点击【加载决议】查看该会议的决议信息。")
        meeting_info_text.config(state=tk.DISABLED)
        meeting_status = tk.Label(meeting_frame, text="", bg=THEME["bg"], fg=THEME["text_dim"],
                                   font=("Microsoft YaHei", 9))
        meeting_status.pack(anchor="w", padx=5, pady=(0, 3))

        def load_meeting_info():
            display = meeting_var.get()
            if not display or display == "-- 选择会议 --":
                meeting_info_text.config(state=tk.NORMAL)
                meeting_info_text.delete("1.0", tk.END)
                meeting_info_text.insert("1.0", "请先从下拉菜单选择一个会议。")
                meeting_info_text.config(state=tk.DISABLED)
                meeting_status.config(text="⚠️ 未选择会议", fg=THEME["warning"])
                return
            info = meeting_map.get(display, "")
            meeting_info_text.config(state=tk.NORMAL)
            meeting_info_text.delete("1.0", tk.END)
            meeting_info_text.insert("1.0", info)
            meeting_info_text.config(state=tk.DISABLED)
            meeting_status.config(text=f"✅ 已加载 {display.split()[0]} 的决议", fg=THEME["success"])

        load_meeting_btn.config(command=load_meeting_info)

        # 状态
        status_label = tk.Label(dlg, text="✅ 配置 API 后选择项目，点击 ⚡ 直接生成草稿", bg=THEME["bg"],
                                fg=THEME["success"], font=("Microsoft YaHei", 10))
        status_label.pack(anchor="w", padx=10, pady=5)

        # ===== 结果区域 =====
        tk.Label(dlg, text="最终回复草稿:", bg=THEME["bg"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=10, pady=5)

        result_text = scrolledtext.ScrolledText(dlg, font=("Microsoft YaHei", 10), wrap=tk.WORD,
                                                bg=THEME["card"], fg=THEME["text"], insertbackground=THEME["text"],
                                                relief=tk.FLAT, bd=2, height=16)
        result_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        result_text.insert("1.0", "配置 API 后，点击【⚡ 生成草稿】直接生成最终回复...")
        result_text.config(state=tk.DISABLED)

        # ===== 按钮 =====
        btn_row = tk.Frame(dlg, bg=THEME["bg"])
        btn_row.pack(fill=tk.X, padx=10, pady=8)

        gen_btn = tk.Button(btn_row, text="⚡ 生成草稿", bg=THEME["accent"], fg=THEME["bg"],
                            activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                            bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 11, "bold"))
        gen_btn.pack(side=tk.LEFT, padx=3)

        copy_btn = tk.Button(btn_row, text="📋 复制", bg=THEME["card"], fg=THEME["text"],
                             activebackground=THEME["accent"], activeforeground=THEME["bg"],
                             bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 10))
        copy_btn.pack(side=tk.LEFT, padx=3)

        auto_btn = tk.Button(btn_row, text="🤖 发送到 Kimi", bg="#89b4fa", fg=THEME["bg"],
                             activebackground="#74c7ec", activeforeground=THEME["bg"],
                             bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 10))
        auto_btn.pack(side=tk.LEFT, padx=3)

        def generate():
            # 检查配置
            api_key = api_entry.get().strip()
            if not api_key:
                api_key = get_ai_api_key(provider_var.get())
                if api_key:
                    api_key_var.set(api_key)
            if not api_key:
                status_label.config(text="❌ API Key 为空。请在上方输入并点击 💾 保存")
                return

            provider = provider_var.get()
            model = model_var.get()
            if not model:
                status_label.config(text="❌ 请选择模型")
                return

            # 保存设置
            self.data["ai_provider"] = provider
            self.data["ai_model"] = model
            set_ai_api_key(api_key, provider)
            save_data(self.data)
            api_status.config(text="✅ 已设置", fg=THEME["success"])

            url = url_var.get().strip()
            if not url or not url.startswith("http"):
                status_label.config(text="❌ 请输入有效的 Talk 帖子 URL")
                return

            status_label.config(text="⏳ 正在获取帖子内容...")
            gen_btn.config(state=tk.DISABLED, text="⏳ 获取中...")
            dlg.update()

            html = self._fetch_talk_post(url)
            if not html:
                status_label.config(text="❌ 无法获取帖子内容")
                gen_btn.config(state=tk.NORMAL, text="⚡ 生成草稿")
                return

            post = self._parse_talk_post(html)
            if not post.get("title"):
                status_label.config(text="❌ 无法解析帖子内容")
                gen_btn.config(state=tk.NORMAL, text="⚡ 生成草稿")
                return

            # 检查模板
            if not template_name or not template_text:
                status_label.config(text="❌ 未选择模板，请切换到 📝 模板 标签页选择模板")
                gen_btn.config(state=tk.NORMAL, text="⚡ 生成草稿")
                return

            # 查找最新决议和知识库（从手动选取的会议纪要中读取）
            resolution_info = meeting_info_text.get("1.0", tk.END).strip()
            meeting_date = meeting_var.get().split()[0] if meeting_var.get() else ""
            if not resolution_info or resolution_info.startswith("从上方") or resolution_info.startswith("请先从"):
                resolution_info = ""
                meeting_date = ""
            tone_knowledge = self.data.get("reply_tone_knowledge", "")

            status_label.config(text=f"⏳ 正在调用 {provider.upper()} AI 生成草稿...")
            if resolution_info:
                status_label.config(text=f"⏳ 已关联 {meeting_date} 决议，正在调用 {provider.upper()} AI 生成草稿...")
            gen_btn.config(text="⏳ AI 生成中...")
            dlg.update()

            prompt = self._build_kimi_prompt(template_name, template_desc, template_text, post,
                                               resolution_info=resolution_info, tone_knowledge=tone_knowledge)
            self._current_prompt = prompt

            # 调用 API（zz 模板注入大段措辞 Skill，需要更长超时）
            api_timeout = 300 if "[zz]" in template_name else 180
            draft, err = call_ai_api(provider, api_key, prompt, model, timeout=api_timeout)
            if err:
                status_label.config(text=f"❌ {err}")
                gen_btn.config(state=tk.NORMAL, text="⚡ 重试")
                return

            # 显示结果
            result_text.config(state=tk.NORMAL)
            result_text.delete("1.0", tk.END)
            result_text.insert("1.0", draft)
            result_text.config(state=tk.NORMAL)

            status_label.config(text=f"✅ 草稿已生成！共 {len(draft)} 字")
            status_label.config(fg=THEME["success"])
            gen_btn.config(state=tk.NORMAL, text="🔄 重新生成")

            # 自动复制
            self.root.clipboard_clear()
            self.root.clipboard_append(draft)
            status_label.config(text=f"✅ 草稿已生成并复制到剪贴板！共 {len(draft)} 字")

        def copy_draft():
            content = result_text.get("1.0", tk.END).strip()
            if not content or content.startswith("配置"):
                status_label.config(text="❌ 还没有内容")
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            status_label.config(text="✅ 已复制")

        def auto_send():
            if not self._current_prompt:
                status_label.config(text="❌ 先生成草稿")
                return
            self._auto_send_to_kimi(self._current_prompt, status_label, auto_btn, result_text, dlg)

        gen_btn.config(command=generate)
        copy_btn.config(command=copy_draft)
        auto_btn.config(command=auto_send)

        if has_key:
            status_label.config(text="✅ API 已配置，选择项目后点击 ⚡ 直接生成草稿")
            status_label.config(fg=THEME["success"])


    def _find_kimi_window(self):
        """查找所有可见窗口标题，返回含 'Kimi' 的窗口列表。"""
        import ctypes
        results = []
        
        def callback(hwnd, _):
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
                    title = buffer.value
                    if title and len(title) > 2:
                        results.append((hwnd, title))
            return True
        
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        ctypes.windll.user32.EnumWindows(EnumWindowsProc(callback), 0)
        
        # 筛选含 Kimi 的窗口
        kimi_windows = [(hwnd, title) for hwnd, title in results if "Kimi" in title]
        return kimi_windows, results

    def _auto_send_to_kimi(self, prompt, status_label, auto_btn, result_text, dlg):
        """自动发送 Prompt 给 Kimi 桌面端。"""
        if not prompt:
            status_label.config(text="❌ 先生成 Prompt")
            return
        
        import ctypes, time
        
        # 1. 复制到剪贴板
        self.root.clipboard_clear()
        self.root.clipboard_append(prompt)
        status_label.config(text="⏳ 正在查找 Kimi 窗口...")
        auto_btn.config(state=tk.DISABLED, text="⏳ 发送中...")
        if dlg:
            dlg.update()
        else:
            self.root.update()
        
        # 2. 查找 Kimi 窗口
        kimi_windows, all_windows = self._find_kimi_window()
        
        if not kimi_windows:
            # 列出所有窗口让用户确认
            window_list = [f"{title}" for _, title in all_windows if title]
            status_label.config(text=f"❌ 未找到 Kimi 窗口。发现 {len(window_list)} 个窗口")
            auto_btn.config(state=tk.NORMAL, text="🤖 自动发送给 Kimi")
            # 弹出窗口列表
            self._show_window_selector(window_list, prompt, status_label, auto_btn)
            return
        
        # 如果只有一个 Kimi 窗口，直接使用
        if len(kimi_windows) == 1:
            hwnd = kimi_windows[0][0]
            title = kimi_windows[0][1]
        else:
            # 多个 Kimi 窗口，弹出选择
            titles = [title for _, title in kimi_windows]
            self._show_kimi_window_selector(kimi_windows, prompt, status_label, auto_btn)
            return
        
        self._send_to_window(hwnd, title, prompt, status_label, auto_btn, result_text, dlg)
    
    def _show_window_selector(self, window_list, prompt, status_label, auto_btn):
        """显示所有窗口列表，让用户手动选择 Kimi 窗口。"""
        selector = tk.Toplevel(self.root)
        selector.title("选择 Kimi 窗口")
        selector.geometry("400x300")
        selector.configure(bg=THEME["bg"])
        selector.transient(self.root)
        selector.grab_set()
        
        tk.Label(selector, text="未找到标题含 'Kimi' 的窗口。\n请选择 Kimi 桌面端窗口：",
                 bg=THEME["bg"], fg=THEME["text"], font=("Microsoft YaHei", 10), wraplength=350).pack(padx=10, pady=5)
        
        listbox = tk.Listbox(selector, bg=THEME["card"], fg=THEME["text"], 
                             selectbackground=THEME["accent"], font=("Microsoft YaHei", 10), height=10)
        listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        for title in window_list[:50]:
            listbox.insert(tk.END, title)
        
        def on_select():
            sel = listbox.curselection()
            if not sel:
                return
            selected_title = listbox.get(sel[0])
            # 重新查找这个窗口的 hwnd
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, selected_title)
            if hwnd:
                self._send_to_window(hwnd, selected_title, prompt, status_label, auto_btn, None, None)
            selector.destroy()
        
        tk.Button(selector, text="确认选择", bg=THEME["accent"], fg=THEME["bg"],
                  font=("Microsoft YaHei", 10, "bold"), command=on_select).pack(pady=5)
    
    def _show_kimi_window_selector(self, kimi_windows, prompt, status_label, auto_btn):
        """显示多个 Kimi 窗口供选择。"""
        selector = tk.Toplevel(self.root)
        selector.title("选择 Kimi 窗口")
        selector.geometry("400x200")
        selector.configure(bg=THEME["bg"])
        selector.transient(self.root)
        selector.grab_set()
        
        tk.Label(selector, text="发现多个 Kimi 窗口，请选择：",
                 bg=THEME["bg"], fg=THEME["text"], font=("Microsoft YaHei", 10)).pack(padx=10, pady=5)
        
        listbox = tk.Listbox(selector, bg=THEME["card"], fg=THEME["text"],
                             selectbackground=THEME["accent"], font=("Microsoft YaHei", 10), height=6)
        listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        for hwnd, title in kimi_windows:
            listbox.insert(tk.END, f"{title} (hwnd:{hwnd})")
        
        def on_select():
            sel = listbox.curselection()
            if not sel:
                return
            hwnd = kimi_windows[sel[0]][0]
            title = kimi_windows[sel[0]][1]
            self._send_to_window(hwnd, title, prompt, status_label, auto_btn, None, None)
            selector.destroy()
        
        tk.Button(selector, text="确认选择", bg=THEME["accent"], fg=THEME["bg"],
                  font=("Microsoft YaHei", 10, "bold"), command=on_select).pack(pady=5)
    
    def _send_to_window(self, hwnd, title, prompt, status_label, auto_btn, result_text, dlg):
        """向指定窗口发送粘贴和 Enter 按键。"""
        import ctypes, time
        
        # 激活窗口
        SW_RESTORE = 9
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.8)
        
        # 模拟粘贴 Ctrl+V
        VK_CONTROL = 0x11
        VK_V = 0x56
        VK_RETURN = 0x0D
        KEYEVENTF_KEYUP = 0x0002
        
        ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.15)
        ctypes.windll.user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.4)
        
        # 模拟 Enter 发送
        ctypes.windll.user32.keybd_event(VK_RETURN, 0, 0, 0)
        time.sleep(0.15)
        ctypes.windll.user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
        
        auto_btn.config(state=tk.NORMAL, text="🤖 自动发送给 Kimi")
        status_label.config(text=f"✅ 已发送给窗口: {title[:30]}... 请等待 Kimi 生成回复，然后 Ctrl+C 复制回复")
        status_label.config(fg=THEME["success"])
        
        # 启动剪贴板监控
        if dlg and result_text:
            self._start_clipboard_monitor(dlg, status_label, result_text)

    def _start_clipboard_monitor(self, dlg, status_label, result_text):
        """后台监控剪贴板，自动捕获用户复制的 Kimi 回复。"""
        self._monitoring_clipboard = True
        # 使用 _ai_current_prompt 作为基准，如果不可用则回退到 _current_prompt
        prompt = getattr(self, '_ai_current_prompt', '') or getattr(self, '_current_prompt', '')
        self._last_clipboard = prompt
        import threading, time

        def get_clipboard():
            """使用 Windows API 读取剪贴板文本。"""
            try:
                CF_UNICODETEXT = 13
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                if not user32.OpenClipboard(None):
                    return ""
                try:
                    handle = user32.GetClipboardData(CF_UNICODETEXT)
                    if not handle:
                        return ""
                    pointer = kernel32.GlobalLock(handle)
                    if not pointer:
                        return ""
                    text = ctypes.wstring_at(pointer)
                    kernel32.GlobalUnlock(handle)
                    return text
                finally:
                    user32.CloseClipboard()
            except Exception:
                return ""

        def monitor():
            while self._monitoring_clipboard:
                time.sleep(1.5)
                current = get_clipboard()
                if current and current != self._last_clipboard and len(current) > 100:
                    # 可能是 Kimi 的回复
                    self._last_clipboard = current
                    # 使用 after 在主线程更新 UI
                    self.root.after(0, lambda: self._on_clipboard_received(current, status_label, result_text))
                    break

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        # 30 秒后自动停止监控
        self.root.after(30000, lambda: self._stop_clipboard_monitor())

    def _on_clipboard_received(self, content, status_label, result_text):
        """剪贴板监控回调：在主线程更新 UI。"""
        if not self._monitoring_clipboard:
            return
        result_text.config(state=tk.NORMAL)
        result_text.delete("1.0", tk.END)
        result_text.insert("1.0", content)
        result_text.config(state=tk.NORMAL)
        status_label.config(text=f"🎉 自动检测到 Kimi 回复！共 {len(content)} 字。点击 📋 复制即可使用")
        status_label.config(fg=THEME["success"])
        self._monitoring_clipboard = False

    def _stop_clipboard_monitor(self):
        """停止剪贴板监控。"""
        self._monitoring_clipboard = False

    def _fetch_talk_post(self, url):
        """获取 Nervos Talk 帖子 HTML 内容。"""
        try:
            if not url.endswith(".json"):
                url = url.rstrip("/") + ".json"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8")
        except Exception:
            return None

    def _parse_talk_post(self, html):
        """解析 Nervos Talk 帖子 JSON，提取关键信息。包括标题、作者、内容、预算金额等。"""
        import re
        try:
            data = jsonlib.loads(html)
            post = data.get("post_stream", {}).get("posts", [{}])[0]
            topic = data.get("title", "")
            author = post.get("username", "")
            cooked = post.get("cooked", "")
            # 去掉 HTML 标签，提取纯文本
            text = re.sub(r"<[^>]+>", "", cooked)
            text = re.sub(r"\n\s*\n", "\n\n", text).strip()
            # 提取预算/资助金额（常见格式：$5,000, $2000, 5000 USD, 5000$）
            budget_amount = None
            budget_patterns = [
                r'(?:budget|grant|total|funding|amount)\s*[:\-]?\s*\$?\s*([\d,\.]+)\s*(?:USD|CKB|\$)?',
                r'(?:budget|grant|total|funding|amount)\s*[:\-]?\s*([\d,\.]+)\s*\$',
                r'\$\s*([\d,\.]+)\s*(?:USD|CKB)?',
                r'\$\s*([\d,\.]+)',
                r'([\d,\.]+)\s*(?:USD|CKB|usd|ckb)',
                r'(?:Budget|Total Grant|Grant|Funding)\s*[:\-]?\s*([\d,\.]+)',
                r'(?:budget\s*:\s*)\$?\s*([\d,\.]+)',
                r'(?:total\s*\$\s*([\d,\.]+))',
            ]
            for pattern in budget_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    raw = match.group(1).replace(',', '').replace(' ', '')
                    try:
                        val = float(raw)
                        if val > 100 and val < 1000000:  # 合理范围
                            budget_amount = raw
                            break
                    except ValueError:
                        continue
            # 提取小标题（h1-h6），用于预审的格式完整性比对
            import html as _html
            headings = []
            for hm in re.finditer(r'<h[1-6][^>]*>(.*?)</h[1-6]>', cooked, re.IGNORECASE | re.DOTALL):
                h = _html.unescape(re.sub(r'<[^>]+>', '', hm.group(1))).strip()
                if h and len(h) < 200:
                    headings.append(h)
            # 提取提案关键信息
            info = {
                "title": topic,
                "author": author,
                "content": text[:3000] if len(text) > 3000 else text,
                "content_full": text,
                "headings": headings,
                "full_url": data.get("url", ""),
                "budget_amount": budget_amount
            }
            return info
        except Exception:
            # 如果 JSON 解析失败，尝试从 HTML 中提取
            try:
                title_match = re.search(r'<title>([^<]+)</title>', html)
                title = title_match.group(1) if title_match else "未知"
                text = re.sub(r"<[^>]+>", "", html)
                text = re.sub(r"\s+", " ", text).strip()
                return {"title": title, "author": "", "content": text[:3000], "full_url": ""}
            except Exception:
                return {}

    def _get_ckb_usd_rate(self):
        """获取当前 CKB/USD 实时汇率。返回 (ckb_price_usd, rate) 元组。
        ckb_price_usd: 1 CKB = 多少 USD
        rate: 1 USD = 多少 CKB
        结果缓存 30 分钟，避免每次起草都联网阻塞。
        """
        now = time.time()
        cached = getattr(self, "_ckb_rate_cache", None)
        if cached and now - cached[0] < 1800:
            return cached[1], cached[2]
        try:
            import urllib.request
            url = "https://api.coingecko.com/api/v3/simple/price?ids=nervos-network&vs_currencies=usd"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = jsonlib.loads(resp.read().decode())
                ckb_price_usd = data.get("nervos-network", {}).get("usd", 0)
                if ckb_price_usd and ckb_price_usd > 0:
                    rate = 1.0 / ckb_price_usd
                    self._ckb_rate_cache = (now, ckb_price_usd, rate)
                    return ckb_price_usd, rate
        except Exception:
            pass
        return None, None

    def _build_kimi_prompt(self, template_name, template_desc, template_text, post, resolution_info="", tone_knowledge="", supplement_content="", style=""):
        """构建完整的 Kimi AI Prompt。style: 风格人物 id（空=默认委员会口吻）。"""
        # 预审反馈模板：走真实的三步分析法（格式比对 → 资金标准比对 → 逻辑分析）
        if "预审" in template_name:
            return self._build_pre_review_prompt(template_name, template_desc, template_text, post,
                                                 tone_knowledge=tone_knowledge,
                                                 supplement_content=supplement_content)
        extra_context = ""
        if resolution_info:
            extra_context += f"\n## 委员会最新决议\n{resolution_info}\n"
        if supplement_content:
            extra_context += f"\n## 补充内容（必须参考）\n{supplement_content}\n"
        # ---- 事实层：知识库按需召回（言之有物的依据） ----
        knowledge_text = retrieve_knowledge_context(template_name, post)
        if knowledge_text:
            extra_context += (f"\n## 知识库参考（事实依据：规则、技术事实与历史判例；"
                              f"回复中的规则引用、技术判断、先例参照必须与此相符）\n{knowledge_text}\n")
        # ---- 风格层（可选，不强制） ----
        if tone_knowledge:
            extra_context += f"\n## 回复措辞知识库（必须遵循）\n{tone_knowledge}\n"
        # 人物档案：仅当起草页显式选择风格人物时注入
        if style:
            for char in self.data.get("characters", []):
                if char.get("id") == style and char.get("content"):
                    extra_context += (f"\n## 人物风格（本次回复以 {char.get('display_name', style)} 的身份与风格撰写，"
                                      f"措辞、签名与该人物真实发帖风格一致）\n{char['content']}\n")
                    break
        # 委员会措辞 Skill：按决议类型抽取对应章节（zz 模板或 zz 风格时用 zz 版本）
        use_zz = ("[zz]" in template_name) or (style == "zz_tovarishch")
        if use_zz:
            zz_text = route_phrasing_sections(template_name, os.path.join("style", "zz_tovarishch.md"),
                                              ZZ_SECTION_MAP, ZZ_DEFAULT_SECTIONS)
            if zz_text:
                extra_context += (f"\n## 委员会措辞 Skill — zz_tovarishch 版本"
                                  f"（本次起草的简洁度、结构与签名格式以此为准；"
                                  f"与通用版冲突时优先本版本）\n{zz_text}\n")
        else:
            skill_text = route_phrasing_sections(template_name, os.path.join("style", "committee_phrasing_skill.md"),
                                                 PHRASING_SECTION_MAP, PHRASING_DEFAULT_SECTIONS, max_chars=4500)
            if skill_text:
                extra_context += f"\n## 委员会措辞 Skill（必须遵循）\n{skill_text}\n"
        # 如果是 Approved/通过 模板，注入 CKB/USD 实时汇率
        if "Approved" in template_name or "通过" in template_name:
            ckb_price_usd, rate = self._get_ckb_usd_rate()
            if ckb_price_usd and rate:
                extra_context += f"\n## CKB/USD 实时汇率（Approved 模板必须使用）\n"
                extra_context += f"- 当前 CKB 价格: 1 CKB = {ckb_price_usd:.6f} USD\n"
                extra_context += f"- 当前汇率: 1 USD = {rate:.2f} CKB\n\n"
                extra_context += f"计算要求：如果决议中包含预算金额（如 $50000），请使用该金额除以 CKB 价格（或乘以汇率）计算 CKB 总额。\n"
                extra_context += f"计算格式参考: $金额 * 百分比 @CKB价格 = CKB总额\n"
                extra_context += f"示例: $50000 * 100% @{ckb_price_usd:.6f} = {50000 * rate:.2f} CKB\n"
        prompt = f"""# Spark Program 回复起草任务

## 背景
你是 Spark Program 委员会联络员（行天春彦）。
你需要根据申请人的提案帖子内容，使用指定的回复模板，起草一份专业、礼貌、有针对性的回复。
{extra_context}
## 模板信息
- **模板名称**: {template_name}
- **模板用途**: {template_desc}

## 模板原文
```
{template_text}
```

## 申请人帖子内容
- **帖子标题**: {post.get('title', '')}
- **申请人**: {post.get('author', '')}
- **帖子链接**: {post.get('full_url', '')}
- **预算金额**: {post.get('budget_amount', '未检测到，请手动确认')} USD

### 帖子正文（摘要）:
```
{post.get('content', '')}
```

## 任务要求
请根据以上帖子内容，使用【{template_name}】模板，生成一份完整的回复。

要求:
1. 保留模板的整体结构和语气
2. 将模板中的占位符（如[项目名称]、[申请人]等）替换为帖子中的实际信息
3. 针对帖子内容，补充具体的评审意见或反馈
4. 保持英文为主（委员会对外沟通语言），必要时可夹杂中文
5. 回复应当专业、礼貌、有建设性
6. 如果帖子内容不足以填充某些占位符，请保留占位符并用【】标注，方便手动补充
7. 严格遵循【回复措辞知识库】中的语气、立场和口吻要求
8. 如果存在【委员会最新决议】，请确保回复与决议结论一致
9. 如果存在【补充内容】，请确保回复充分参考并体现补充内容中的要求
10. 如果存在【委员会措辞 Skill】，请根据决议类型（通过/待定/拒绝/关闭/完成）选择对应的措辞模板，并复用措辞片段库中的具体措辞
11. 如果存在【CKB/USD 实时汇率】且模板为 Approved 类型，必须在回复中计算并填入 CKB 总额（格式: $金额 * 百分比 @CKB价格 = CKB总额）

请直接输出回复正文，不需要额外解释。"""
        return prompt

    def _build_pre_review_prompt(self, template_name, template_desc, template_text, post, tone_knowledge="", supplement_content=""):
        """预审【阶段一】Prompt：深度三步分析（输出中文分析报告，不输出回复草稿）。
        第一步：对照官方提案模板帖(8752 下"提案模板"回复)做格式完整性比对；
        第二步：对照主帖(8752)与委员会最新档位标准做资金合规比对；
        第三步：对提案内容做逻辑真实性分析。
        """
        ref = fetch_pre_review_reference()
        ref_note = ""
        if ref.get("error"):
            ref_note = f"\n> ⚠️ 参考内容获取提示：{ref['error']}\n"

        headings = post.get("headings", [])
        headings_text = "\n".join(f"- {h}" for h in headings) if headings else "（未检测到任何小标题，提案可能完全没有按模板格式化）"
        content = post.get("content_full") or post.get("content", "")
        if len(content) > 16000:
            content = content[:16000] + "\n...（内容过长已截断）"

        extra = ""
        if supplement_content:
            extra += f"\n## 补充内容（分析时必须考虑）\n{supplement_content}\n"

        # 知识库：资助档位（rules.md 优先）+ 技术评审手册/生态清单/判例（按帖子关键词召回）
        funding_rules = load_funding_rules_text()
        review_knowledge = retrieve_knowledge_context("预审", post, max_chars=7000)
        knowledge_section = ""
        if review_knowledge:
            knowledge_section = f"""
## 参考四：技术评审手册、生态已有项目与历史判例（知识库，分析时必须对照）
```
{review_knowledge}
```
"""

        prompt = f"""# Spark Program 提案预审分析任务（阶段一：深度分析）

## 背景
你是 Spark Program 联络员（行天春彦 / xingtianchunyan），正在对一份新提案帖子做预审。
本阶段只输出**内部分析报告**（中文），不需要起草给申请人的回复——回复将在下一阶段基于你的分析生成。
分析必须具体、有证据、可落地，禁止笼统表述（"建议完善提案""内容不够详细"这类空话一律禁止）。
{extra}
## 参考一：官方提案模板帖（格式与内容要求基准）
> 来源: https://talk.nervos.org/t/spark-program-mini-grant-initiative/8752 主帖下的"提案模板：各章节内容详细说明"回复
{ref_note}
```
{ref.get('template_post', '')}
```

## 参考二：Spark Program 主帖（资助标准与规则基准）
> 来源: https://talk.nervos.org/t/spark-program-mini-grant-initiative/8752
```
{ref.get('main_post', '')}
```

## 参考三：委员会最新资助档位执行标准（知识库 2026-06，主帖标准的细化，必须遵循）
```
{funding_rules}
```
{knowledge_section}
## 目标提案帖子
- **帖子标题**: {post.get('title', '')}
- **申请人**: {post.get('author', '')}
- **帖子链接**: {post.get('full_url', '')}
- **检测到的申请金额**: {post.get('budget_amount') or '未检测到，请从正文中确认'} USD

### 帖子中的小标题结构:
{headings_text}

### 帖子正文:
```
{content}
```

## 分析步骤（必须按顺序完整执行，每步都要引用帖子原文作为证据）

### 第一步：格式完整性比对
以【参考一】为基准，对模板的**每一个章节**逐一核对（不要遗漏任何一节），输出一张核对清单，每行包含：
- 章节编号与名称（按模板原文，如 "§6 执行计划 / To-Do List"）
- 状态：✅ 已包含且充实 / ⚠️ 已包含但敷衍或不满足内容要求 / ❌ 缺失
- 说明：缺失或敷衍的具体原因，引用帖子中的相关内容（或指出完全没有对应内容）
- 标题与标签格式（§0）也要核对：标题是否符合 "Spark Program | The Project Title" 格式

### 第二步：资金标准比对
以【参考二】和【参考三】为基准：
1. 判断项目类型（纯技术 / 纯社区 / 技术+社区综合 / 特殊高难度），说明判断理由
2. 找出帖子中的申请金额（若正文与检测值不一致，以正文为准并说明），与对应档位的常规标准比对
3. 明确结论：合规 / 超标。若超标，给出应调整到的合理档位、金额与理由（例如：纯技术类项目申请超过 $1,000 即不符合常规标准）
4. 评估预算明细质量：是否有资金分配明细？各项支出是否合理、与工作量是否匹配？

### 第三步：内容逻辑分析
对提案内容进行逻辑与真实性分析，逐项给出判断和证据（涉及技术可行性、协议能力、重复造轮子、生态已有同类时，必须对照【参考四】给出具体依据；存在同类历史判例时引用判例）：
1. 问题真实性：提案描述的问题是否真实存在？是否夸大了痛点？
2. 方案匹配度：技术方案能否真正解决该问题？有没有更简单的替代方案被忽略？
3. 可行性：技术方案、时间线、里程碑是否合理？团队能力与交付计划是否匹配？
4. 团队可信度：团队宣称的经验/背景是否有可验证的证据（GitHub、历史项目、社区贡献）？
5. 验证方式：验收方式是否满足"非代码审查、低成本、可复现"的要求？是否具体到可执行的步骤？
6. 矛盾与风险：是否存在自相矛盾、与公开事实不符、或明显不可信的表述？
7. 重复性检查：与生态已有项目/工具（【参考四】清单）是否重复？与 Spark 历史资助项目是否撞车？若有差异化，差异是否成立？

### 最后：亮点清单
列出提案中做得好的 3-5 个方面（用于回复中先给予肯定），每条都要具体。

## 输出格式
用中文 Markdown 输出分析报告，结构为：
## 一、格式完整性比对
（章节核对清单）
## 二、资金标准比对
（类型判断 / 金额比对结论 / 预算明细评估）
## 三、内容逻辑分析
（按 7 个维度逐项分析）
## 四、亮点清单
（3-5 条）
## 五、核心结论
（3-5 句话总结：这份提案最主要的问题是什么、最优先需要申请人补充什么）

请直接输出分析报告，不要输出给申请人的回复，不要额外寒暄。"""
        return prompt

    def _build_pre_review_format_prompt(self, analysis, template_name, template_text, post, tone_knowledge="", supplement_content=""):
        """预审【阶段二】Prompt：把分析报告严格按预审反馈模板格式化为 Markdown 回复。"""
        extra = ""
        if tone_knowledge:
            extra += f"\n## 回复措辞知识库（必须遵循）\n{tone_knowledge}\n"
        if supplement_content:
            extra += f"\n## 补充内容（必须参考并体现）\n{supplement_content}\n"

        prompt = f"""# Spark Program 预审反馈格式化输出任务（阶段二：按模板输出最终回复）

## 背景
你是 Spark Program 联络员（行天春彦 / xingtianchunyan）。
你已完成对一份提案帖子的预审分析（见下方分析报告）。现在需要将分析结果转化为一份**发给申请人的预审反馈回复**。
回复以**个人名义**发出，仅提供提案优化意见，不涉及任何提案状态判定，不使用 Pending / Rejection / Approved 等委员会结论性措辞。
{extra}
## 输出模板（必须严格遵循其章节结构、标题层级与顺序，Markdown 格式，不得增删章节、不得改变小标题文字）
```markdown
{template_text}
```

## 预审分析报告（回复中的所有意见必须来自此报告，不得凭空新增）
```markdown
{analysis}
```

## 帖子基本信息
- **帖子标题**: {post.get('title', '')}
- **申请人**: {post.get('author', '')}
- **帖子链接**: {post.get('full_url', '')}
- **检测到的申请金额**: {post.get('budget_amount') or '未检测到'} USD

## 输出要求
1. 严格按模板的章节与顺序输出：**1. Format & Completeness → 2. Funding Standards → 3. Content Logic → Suggestions**，保持模板中的小标题文字不变
2. 使用标准 Markdown：**加粗**章节标题、`-` 列表逐条列意见、编号列表列建议（Nervos Talk 支持 Markdown 渲染）
3. Format & Completeness：每条意见注明模板章节号（如 §6 To-Do List），并引用分析报告中对应的具体缺失/敷衍说明；已包含且充实的章节不要逐条罗列，可用一句话带过
4. Funding Standards：给出项目类型判断、金额与档位的比对结论；如超标给出建议档位与金额；如金额合规但预算明细缺失，指出需要补充明细
5. Content Logic：先用 1-2 条亮点肯定提案，再逐条列出逻辑疑虑（每条带证据）
6. Suggestions：编号列表，每条都是可立即执行的动作，与分析报告的发现一一对应，按优先级排序
7. 将模板占位符（[申请人]、[项目名称] 等）替换为实际信息；分析报告未覆盖、无法确认的信息用【】标注，方便手动补充
8. 以联络员个人口吻撰写（如 "Before we move to committee review..." / "as the program liaison, not a committee decision"），先肯定价值再指出问题，专业、礼貌、有建设性
9. 全英文输出（专有名词、章节名保留模板原文），不要使用中文
10. 每条意见 1-2 句话，整篇不超过 600 英文单词，保持申请人可读

请直接输出最终回复正文（Markdown），不要输出分析过程，不要任何额外解释。"""
        return prompt

    def _load_project_cache_display(self):
        """启动时加载缓存的看板数据，仅显示，不自动刷新。"""
        cache = load_project_cache()
        if cache and "classified" in cache:
            self._update_project_tab_display(cache)
            self._update_stats_display()
            if self._last_refresh_label:
                last = cache.get("last_update", "")
                if last:
                    try:
                        dt = datetime.datetime.fromisoformat(last)
                        self._last_refresh_label.config(text=f"Last refresh: {dt.strftime('%m-%d %H:%M')}")
                    except Exception:
                        pass

    def _check_project_reminders(self):
        today = datetime.date.today()
        existing_titles = {t.get("title", "") for t in self.data.get("tasks", [])}
        added = []
        for proj in ONGOING_PROJECTS:
            try:
                expected = datetime.date.fromisoformat(proj["expected"])
                delta = (expected - today).days
                if delta <= 14 and delta >= -7:
                    title = f"【提醒】{proj['name']} 项目预计完成日期 {proj['expected']}"
                    if title not in existing_titles:
                        task = {
                            "title": title, "type": "结项处理", "project": proj["name"],
                            "deadline": proj["expected"],
                            "note": f"团队: {proj['team']}  |  已发放: {proj['disbursed']}  |  Talk帖子: {proj['talk_url']}",
                            "created": today.isoformat(), "done": False
                        }
                        self.data.setdefault("tasks", []).append(task)
                        added.append(proj["name"])
            except Exception:
                continue
        if added:
            save_data(self.data)
            self.refresh_task_list()
            messagebox.showinfo("项目提醒",
                f"以下进行中项目即将到达或已逾预计完成日期，已自动添加提醒任务：\n\n" +
                "\n".join(f"• {p}" for p in added))

    def _refresh_project_data(self):
        """Refresh from Nervos Talk."""
        self._do_refresh(silent=False)

    def _do_refresh(self, silent=False):
        """后台线程拉取 Nervos Talk 数据，完成后回主线程应用。
        网络请求不再阻塞 UI，且任何异常都不会让按钮卡在 Refreshing。"""
        if getattr(self, "_refresh_running", False):
            return  # 防止重复点击/并发刷新
        self._refresh_running = True
        self._refresh_btn.config(text="Refreshing...", state=tk.DISABLED)
        self._last_refresh_label.config(text="Fetching from Nervos Talk...")

        def _worker():
            try:
                result = fetch_nervos_talk_projects()
                self.root.after(0, lambda: self._refresh_apply(result, silent, None))
            except Exception as e:
                err = str(e)
                self.root.after(0, lambda: self._refresh_apply(None, silent, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_apply(self, result, silent, error):
        """主线程：应用刷新结果（由后台线程通过 root.after 调回）。"""
        self._refresh_running = False
        if error or not result:
            self._refresh_btn.config(text="Refresh", state=tk.NORMAL)
            self._last_refresh_label.config(text="Last refresh: failed")
            if not silent:
                detail = f"\n{error}" if error else ""
                messagebox.showwarning("Refresh failed", f"Unable to fetch data from Nervos Talk.{detail}")
            return

        try:
            errors = result.get("errors", [])
            classified = result.get("classified", {})

            # 应用白名单
            classified = self._apply_board_whitelist(classified)
            # 检测新进入 In-Progress 的项目，自动创建 Notion 更新任务
            auto_tasks = self._seed_and_sync_inprogress_tasks(classified)

            # 立即保存并显示基础数据（不等待 AI）
            result["classified"] = classified
            save_project_cache(result)
            self._update_project_tab_display(result)
            self._update_stats_display()
            # 时间线页读取同一份缓存，同步刷新
            if hasattr(self, "timeline_listbox"):
                self._refresh_timeline()

            self._refresh_btn.config(text="Refresh", state=tk.NORMAL)
            self._last_refresh_label.config(text=f"Last refresh: {datetime.datetime.now().strftime('%H:%M')}")
            if not silent:
                msg = "Sync complete!"
                if errors:
                    msg += f"\n\nSome tags failed:\n" + "\n".join(errors[:3])
                if auto_tasks:
                    msg += f"\n\n检测到新项目进入 In-Progress，已自动创建 {auto_tasks} 项 Notion 更新任务（任务页查看）。"
                messagebox.showinfo("Refresh success", msg)

            # AI 分类在后台线程执行，避免 UI 阻塞
            provider = self.data.get("ai_provider", "siliconflow")
            api_key = get_ai_api_key(provider)
            model = "deepseek-ai/DeepSeek-V3"
            pre_review = classified.get("spark_program", {}).get("pre_review", [])

            if api_key and pre_review:
                def _run_ai():
                    try:
                        new_classified = classify_spark_posts_with_ai(classified, api_key, provider, model)
                        self.root.after(0, lambda: self._apply_ai_result(new_classified, silent))
                    except Exception as e:
                        if not silent:
                            self.root.after(0, lambda err=str(e): messagebox.showwarning("AI classification failed", f"AI error: {err}"))
                threading.Thread(target=_run_ai, daemon=True).start()
        except Exception as e:
            # 兜底：任何处理异常都不能让按钮卡死
            self._refresh_btn.config(text="Refresh", state=tk.NORMAL)
            self._last_refresh_label.config(text="Last refresh: error")
            if not silent:
                messagebox.showwarning("Refresh failed", f"刷新处理出错: {e}")

    def _apply_ai_result(self, classified, silent):
        """AI 后台线程完成后，在主线程应用结果。"""
        # 应用白名单
        classified = self._apply_board_whitelist(classified)

        # 自动创建任务
        added = auto_create_tasks_from_classified(classified, self.data)
        if added:
            self.refresh_task_list()
            if not silent:
                messagebox.showinfo("Auto tasks created", f"Created {len(added)} tasks")

        # 更新缓存
        cache = load_project_cache()
        if cache and "classified" in cache:
            cache["classified"] = classified
            cache["last_update"] = datetime.datetime.now().isoformat()
            save_project_cache(cache)

        # 更新 UI 显示
        self._update_project_tab_display(cache)
        self._update_stats_display()


    def _add_manual_project(self):
        """手动添加一个项目到已提交列表。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("手动添加项目")
        dlg.geometry("400x200")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=THEME["bg"])

        tk.Label(dlg, text="项目名称:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 10)).grid(row=0, column=0, sticky="w", padx=5, pady=5)
        name_var = tk.StringVar()
        tk.Entry(dlg, textvariable=name_var, width=40, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=0, column=1, padx=5, pady=5)

        tk.Label(dlg, text="Talk 帖子 URL:", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei", 10)).grid(row=1, column=0, sticky="w", padx=5, pady=5)
        url_var = tk.StringVar()
        tk.Entry(dlg, textvariable=url_var, width=40, bg=THEME["card"], fg=THEME["text"],
                 insertbackground=THEME["text"], bd=1, relief=tk.FLAT).grid(row=1, column=1, padx=5, pady=5)

        def save():
            name = name_var.get().strip()
            url = url_var.get().strip()
            if not name or not url:
                messagebox.showwarning("提示", "请输入项目名称和 URL", parent=dlg)
                return
            # Add to the old list for link tab compatibility
            SUBMITTED_PROJECTS.append({
                "name": name, "author": "未知", "url": url, "status": "已提交"
            })
            # Preserve new cache format if exists, or skip cache save
            cache = load_project_cache()
            if cache and "classified" in cache:
                # Add to new format as well so it appears in Submitted tab
                cache.setdefault("classified", {}).setdefault("submitted", []).append({
                    "id": None, "title": name, "slug": "", "tags": ["Submitted"],
                    "author": "手动添加", "url": url, "created_at": "", "posts_count": 0, "views": 0
                })
                save_project_cache(cache)
            else:
                # Fallback to old format only if no new cache yet
                save_project_cache({
                    "ongoing": ONGOING_PROJECTS,
                    "submitted": SUBMITTED_PROJECTS,
                    "last_update": datetime.datetime.now().isoformat()
                })
            dlg.destroy()
            messagebox.showinfo("已添加", f"项目 '{name}' 已添加")

        tk.Button(dlg, text="保存", bg=THEME["accent"], fg=THEME["bg"],
                  activebackground=THEME["accent2"], activeforeground=THEME["bg"],
                  bd=0, relief=tk.FLAT, cursor="hand2", font=("Microsoft YaHei", 11, "bold"),
                  command=save).grid(row=2, column=0, columnspan=2, pady=15)


# ============================================================
# Nervos Talk 数据同步
# ============================================================

def get_spark_program_category_info():
    """尝试从 Nervos Talk 获取 Spark Program 分区的 slug 和 id。
    注意：Spark Program 是 DAOs & Funding 的子分类，需 include_subcategories=true。
    """
    try:
        url = "https://talk.nervos.org/categories.json?include_subcategories=true"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = jsonlib.loads(resp.read().decode())
        for cat in data.get("category_list", {}).get("categories", []):
            # 检查主分类
            name = cat.get("name", "").lower()
            if "spark program" in name:
                return cat.get("slug", ""), cat.get("id")
            # 检查子分类
            for sub in cat.get("subcategory_list", []):
                sub_name = sub.get("name", "").lower()
                if "spark program" in sub_name:
                    return sub.get("slug", ""), sub.get("id")
    except Exception:
        pass
    return None, None
    """尝试从 Nervos Talk 获取 Spark Program 分区的 slug 和 id。"""
    try:
        url = "https://talk.nervos.org/categories.json"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = jsonlib.loads(resp.read().decode())
        for cat in data.get("category_list", {}).get("categories", []):
            name = cat.get("name", "").lower()
            if "spark program" in name:
                return cat.get("slug", ""), cat.get("id")
    except Exception:
        pass
    return None, None


def _normalize_tag_names(raw_tags):
    """Discourse 的 tags 字段有时返回字符串列表，有时返回 dict 列表（{id,name,slug}），统一提取标签名。"""
    names = []
    for t in raw_tags or []:
        if isinstance(t, str):
            names.append(t)
        elif isinstance(t, dict):
            name = t.get("name")
            if name:
                names.append(name)
    return names


def fetch_nervos_talk_projects():
    """从 Nervos Talk 拉取全部 Spark 相关帖子并按看板规则分类。"""
    result = {
        "all_posts": {},
        "errors": [],
        "classified": None,
        "last_update": datetime.datetime.now().isoformat(),
    }
    all_posts = {}
    classified = {
        "spark_program": {"official": [], "pre_review": [], "non_proposal": []},
        "submitted": [], "pending": [], "in_progress": [],
        "rejection": [], "completion": [], "closure": [],
    }
    for tag in TALK_TAGS:
        try:
            # 获取多页数据，确保不漏掉老帖子
            tag_posts = []
            for page in range(0, 3):  # 最多获取3页
                per_page = 30  # Discourse tag.json 默认每页30个
                url = f"https://talk.nervos.org/tag/{tag}.json?per_page={per_page}&page={page}"
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Accept": "application/json, text/html, */*"
                })
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read().decode()
                if raw.strip().startswith("<"):
                    result["errors"].append(f"{tag}: HTML")
                    break
                data = jsonlib.loads(raw)
                topics = data.get("topic_list", {}).get("topics", [])
                if not topics:
                    break  # 没有更多帖子
                for topic in topics:
                    post_id = topic.get("id")
                    if post_id not in all_posts:
                        # 获取主帖发帖者（Original Poster）的 user_id
                        original_poster_id = None
                        for poster in topic.get("posters", []):
                            desc = poster.get("description", "")
                            if isinstance(desc, list):
                                desc = " ".join(desc)
                            desc_str = str(desc).lower()
                            if "original poster" in desc_str or "original" in desc_str:
                                original_poster_id = poster.get("user_id")
                                break
                        # Fallback: 如果 posters 为空，使用 topic 的 user_id
                        if original_poster_id is None:
                            original_poster_id = topic.get("user_id")
                        all_posts[post_id] = {
                            "id": post_id, "title": topic.get("title", ""),
                            "slug": topic.get("slug", ""), "tags": _normalize_tag_names(topic.get("tags", [])),
                            "author": topic.get("last_poster_username", ""),
                            "original_poster_id": original_poster_id,
                            "url": f"https://talk.nervos.org/t/{topic.get('slug', '')}/{topic.get('id', '')}",
                            "created_at": topic.get("created_at", ""),
                            "posts_count": topic.get("posts_count", 0),
                            "views": topic.get("views", 0),
                        }
                    else:
                        # 合并标签（同一个帖子可能出现在多个标签下）
                        existing_tags = set(all_posts[post_id]["tags"])
                        new_tags = set(_normalize_tag_names(topic.get("tags", [])))
                        all_posts[post_id]["tags"] = list(existing_tags | new_tags)
                    tag_posts.append(all_posts[post_id])
                if len(topics) < per_page:
                    break  # 最后一页
            result["all_posts"][tag] = tag_posts
        except Exception as e:
            result["errors"].append(f"{tag}: {str(e)}")

    # 新增：从 Spark Program 分区获取帖子（兼容新分区机制）
    cat_slug, cat_id = get_spark_program_category_info()
    if cat_slug and cat_id:
        try:
            for page in range(0, 3):
                per_page = 30
                url = f"https://talk.nervos.org/c/{cat_slug}/{cat_id}.json?per_page={per_page}&page={page}"
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Accept": "application/json, text/html, */*"
                })
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read().decode()
                if raw.strip().startswith("<"):
                    result["errors"].append("category: HTML")
                    break
                data = jsonlib.loads(raw)
                topics = data.get("topic_list", {}).get("topics", [])
                if not topics:
                    break
                for topic in topics:
                    post_id = topic.get("id")
                    if post_id not in all_posts:
                        original_poster_id = None
                        for poster in topic.get("posters", []):
                            desc = poster.get("description", "")
                            if isinstance(desc, list):
                                desc = " ".join(desc)
                            desc_str = str(desc).lower()
                            if "original poster" in desc_str or "original" in desc_str:
                                original_poster_id = poster.get("user_id")
                                break
                        if original_poster_id is None:
                            original_poster_id = topic.get("user_id")
                        all_posts[post_id] = {
                            "id": post_id, "title": topic.get("title", ""),
                            "slug": topic.get("slug", ""), "tags": _normalize_tag_names(topic.get("tags", [])),
                            "author": topic.get("last_poster_username", ""),
                            "original_poster_id": original_poster_id,
                            "url": f"https://talk.nervos.org/t/{topic.get('slug', '')}/{topic.get('id', '')}",
                            "created_at": topic.get("created_at", ""),
                            "posts_count": topic.get("posts_count", 0),
                            "views": topic.get("views", 0),
                            "from_spark_category": True,
                        }
                    else:
                        all_posts[post_id]["from_spark_category"] = True
                        existing_tags = set(all_posts[post_id]["tags"])
                        new_tags = set(_normalize_tag_names(topic.get("tags", [])))
                        all_posts[post_id]["tags"] = list(existing_tags | new_tags)
                if len(topics) < per_page:
                    break
        except Exception as e:
            result["errors"].append(f"category: {str(e)}")

    # 统一分类
    OFFICIAL_USER_IDS = {3792, 3420}  # xingtianchunyan, zz_tovarishch
    USER_ID_MAP = {3792: "xingtianchunyan", 3420: "zz_tovarishch"}

    for post in all_posts.values():
        tags = set(_normalize_tag_names(post.get("tags", [])))
        original_poster_id = post.get("original_poster_id")
        has_status = bool(tags & STATUS_TAGS)
        from_spark_category = post.get("from_spark_category", False)

        # 标题匹配：包含 "Spark Program" 的帖子（包括 "Spark Program |" 和 "Spark Program 2025 Annual Report" 等）
        title = post.get("title", "")
        is_spark_title = "spark program" in title.lower()

        # 官方帖子：委员会成员发布的主帖，带 Spark-Program 标签/标题匹配/来自Spark分区，且无状态标签
        if original_poster_id in OFFICIAL_USER_IDS and ("Spark-Program" in tags or is_spark_title or from_spark_category) and not has_status:
            post["original_poster_username"] = USER_ID_MAP.get(original_poster_id, "")
            classified["spark_program"]["official"].append(post)
            continue

        # Spark-Program 预审区：带 Spark-Program 标签/标题匹配/来自Spark分区，且不带任何状态标签
        is_spark_program = "Spark-Program" in tags or is_spark_title or from_spark_category
        if is_spark_program and not has_status:
            classified["spark_program"]["pre_review"].append(post)

        # 状态标签页：要求同时带有对应状态标签和 Spark-Program 标签（或来自Spark分区）
        if "Submitted" in tags and ("Spark-Program" in tags or from_spark_category):
            classified["submitted"].append(post)
        if "Pending" in tags and ("Spark-Program" in tags or from_spark_category):
            classified["pending"].append(post)
        if "In-Progress" in tags and ("Spark-Program" in tags or from_spark_category):
            classified["in_progress"].append(post)
        if "Rejection" in tags and ("Spark-Program" in tags or from_spark_category):
            classified["rejection"].append(post)
        if "Completion" in tags and ("Spark-Program" in tags or from_spark_category):
            classified["completion"].append(post)
        if "Closure" in tags and ("Spark-Program" in tags or from_spark_category):
            classified["closure"].append(post)

    result["classified"] = classified
    return result


def classify_spark_posts_with_ai(classified, api_key, provider="siliconflow", model="deepseek-ai/DeepSeek-V3"):
    """对仅带Spark-Program标签的帖子进行基于规则的提案分类。
    规则：1. 小标题与提案模板重合率达到40%；2. 包含提案关键词（Deliverables等）。
    返回修改后的classified字典。
    """
    import re

    # 提案模板URL（从该帖子提取标准小标题）
    TEMPLATE_URL = "https://talk.nervos.org/t/spark-program-mini-grant-initiative/8752.json"
    PROPOSAL_KEYWORDS = ["deliverables", "how to verify", "to-do list", "todo list", "budget", "milestones", "team", "wallet address"]
    MIN_OVERLAP_RATE = 0.40  # 40% 重合率

    def extract_headings(html_text):
        """从HTML内容中提取小标题（h1-h6）文本"""
        headings = []
        if not html_text:
            return headings
        for match in re.finditer(r'<h[1-6][^>]*>(.*?)</h[1-6]>', html_text, re.IGNORECASE | re.DOTALL):
            text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
            if text and len(text) < 200:
                headings.append(text.lower())
        return headings

    def calculate_overlap(template_headings, post_headings):
        """计算两个标题列表的重合率"""
        if not template_headings or not post_headings:
            return 0.0
        template_set = set(h.strip() for h in template_headings if h.strip())
        post_set = set(h.strip() for h in post_headings if h.strip())
        if not template_set:
            return 0.0
        overlap = len(template_set & post_set)
        return overlap / len(template_set)

    def has_proposal_keywords(text):
        """检查文本是否包含提案关键词"""
        text_lower = text.lower()
        for kw in PROPOSAL_KEYWORDS:
            if kw in text_lower:
                return True, kw
        return False, None

    # 获取提案模板内容
    template_headings = []
    try:
        req = urllib.request.Request(TEMPLATE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = jsonlib.loads(resp.read().decode())
        # 提取所有帖子回复的小标题
        for post in d.get("post_stream", {}).get("posts", []):
            cooked = post.get("cooked", "")
            headings = extract_headings(cooked)
            template_headings.extend(headings)
    except Exception:
        pass

    # 如果无法获取模板，使用默认的提案标准小标题
    if not template_headings:
        template_headings = [
            "proposal title", "project description", "team introduction",
            "project goals and milestones", "technical approach",
            "budget breakdown", "deliverables and acceptance criteria",
            "ckb wallet address"
        ]

    pre_review = classified["spark_program"].get("pre_review", [])
    if not pre_review:
        return classified

    for post in pre_review:
        title = post.get("title", "")
        try:
            url = f"https://talk.nervos.org/t/{post.get('slug', '')}/{post.get('id', '')}.json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = jsonlib.loads(resp.read().decode())
            # 获取主帖内容（第一个回复）
            posts = d.get("post_stream", {}).get("posts", [])
            if not posts:
                post["ai_classified"] = "proposal"
                continue
            cooked = posts[0].get("cooked", "")
            text = re.sub(r"<[^>]+>", "", cooked)

            # 提取帖子小标题
            post_headings = extract_headings(cooked)

            # 规则1：计算小标题重合率
            overlap_rate = calculate_overlap(template_headings, post_headings)

            # 规则2：检查提案关键词
            has_keywords, matched_kw = has_proposal_keywords(text)

            # 判定：重合率>=40% 或 包含关键词 → 提案
            if overlap_rate >= MIN_OVERLAP_RATE or has_keywords:
                post["ai_classified"] = "proposal"
                post["ai_classify_reason"] = f"overlap={overlap_rate:.0%}, keyword={matched_kw or 'N/A'}"
            else:
                post["ai_classified"] = "non_proposal"
                post["ai_classify_reason"] = f"overlap={overlap_rate:.0%}, no keywords"
        except Exception:
            # 获取失败时，默认标记为proposal（需要人工审查，避免误删）
            post["ai_classified"] = "proposal"
            post["ai_classify_reason"] = "fetch_failed"

    proposal_list = [p for p in pre_review if p.get("ai_classified") != "non_proposal"]
    non_list = [p for p in pre_review if p.get("ai_classified") == "non_proposal"]
    classified["spark_program"]["pre_review"] = proposal_list
    classified["spark_program"]["non_proposal"] = non_list
    return classified


def auto_create_tasks_from_classified(classified, data):
    """根据分类结果自动创建任务。"""
    existing = {t.get("title", "") for t in data.get("tasks", [])}
    added = []
    for post in classified["spark_program"]["pre_review"]:
        title = f"【预审】{post.get('title', '未知帖子')}"
        if title not in existing:
            data.setdefault("tasks", []).append({
                "title": title, "type": "预审", "project": post.get("title", ""),
                "deadline": "", "note": f"帖子: {post.get('url', '')}\n作者: {post.get('author', '')}\n仅带Spark-Program标签，需要预审反馈",
                "created": datetime.date.today().isoformat(), "done": False
            })
            added.append(title)
    for post in classified["spark_program"]["non_proposal"]:
        title = f"【删除标签】{post.get('title', '未知帖子')}"
        if title not in existing:
            data.setdefault("tasks", []).append({
                "title": title, "type": "删除标签", "project": post.get("title", ""),
                "deadline": "", "note": f"帖子: {post.get('url', '')}\n作者: {post.get('author', '')}\nAI判断为非提案，需删除Spark-Program标签",
                "created": datetime.date.today().isoformat(), "done": False
            })
            added.append(title)
    if added:
        save_data(data)
    return added


def load_project_cache():
    """加载缓存的项目数据。"""
    if os.path.exists(PROJECT_CACHE_FILE):
        try:
            with open(PROJECT_CACHE_FILE, "r", encoding="utf-8") as f:
                return jsonlib.load(f)
        except Exception:
            pass
    return None


def save_project_cache(data):
    with open(PROJECT_CACHE_FILE, "w", encoding="utf-8") as f:
        jsonlib.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# 单实例检测（Windows CreateMutex + FindWindow）
# ============================================================

def ensure_single_instance(root):
    """Windows CreateMutex 互斥对象 + FindWindow 查找已有窗口。"""
    already_running = False
    try:
        import ctypes
        # 1. 先查找已有窗口并激活（最可靠的方式）
        hwnd = ctypes.windll.user32.FindWindowW(None, "Spark Program")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            already_running = True
        else:
            # 2. 创建 Windows 内核互斥对象作为后备
            ctypes.windll.kernel32.SetLastError(0)
            ctypes.windll.kernel32.CreateMutexW(None, 0, "SparkProgramTaskTracker_v3")
            if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                already_running = True
    except Exception:
        pass

    if already_running:
        try:
            messagebox.showwarning("已在运行",
                "Spark Program 桌面精灵已经在运行中。\n\n" +
                "请使用已打开的窗口，或先关闭后再重新打开。")
        except Exception:
            pass
        return False
    return True


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    if not ensure_single_instance(root):
        root.destroy()
        sys.exit(0)
    root.deiconify()
    app = SparkTaskTracker(root)
    root.mainloop()
