# -*- coding: utf-8 -*-
"""从 spark_task_data.json 的会议纪要提炼 Spark Program 判例库。
输出: knowledge/spark/precedents.md
可重复运行：每次根据最新纪要全量重建。"""
import json, os, re, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, "spark_task_data.json")
OUT_FILE = os.path.join(BASE, "knowledge", "spark", "precedents.md")

# 项目名归一化（同一项目的不同写法）
ALIAS = {
    "fiber checkout": "Fiber Checkout", "fiber-checkout": "Fiber Checkout",
    "ckb probe": "CKB Probe", "ckb-probe": "CKB Probe",
    "ckb ugmp": "CKB UGMP", "ugmp": "CKB UGMP",
    "discourse ... for dao v1.1": "Discourse for DAO", "discourse": "Discourse for DAO",
    "ckb developer onboarding guide": "CKB Developer Onboarding",
    "ckb developer onboarding": "CKB Developer Onboarding",
    "ckb builder lab": "CKB Builder Lab", "builder lab": "CKB Builder Lab",
    "ckb bulider lab": "CKB Builder Lab",
    "cell sandbox": "Cell Sandbox", "cellsandbox": "Cell Sandbox",
    "wallet behaviour": "Wallet Behaviour", "tiko": "TiKo",
    "tiko creator commerce validation sprint": "TiKo",
    "fiber rgb++ swap": "Fiber RGB++ Swap", "rgb++ swap": "Fiber RGB++ Swap",
    "cellmint": "CellMint", "cellmint ": "CellMint",
    "nervos brain": "Nervos Brain", "dular": "Dular",
    "corven": "Corven", "cellar": "Cellar", "zk-lock": "zk-Lock",
    "spore metadate": "Spore Metadata", "sporemetadata": "Spore Metadata",
    "fiber pay studio": "Fiber Pay Studio",
}

def norm(name):
    n = (name or "").strip()
    return ALIAS.get(n.lower(), n)

HEADER = """# Spark Program 判例案例库

> 由 build_precedents.py 自动生成（数据源：工具内会议纪要），请勿手工编辑本文件的项目时间线部分。
> 生成时间：{now}
> 用途：AI 起草/预审时召回同类案例，让回复"言之有物"（参照此前 XX 项目的处理方式）。

## 典型判例速查（人工维护）

| 情形 | 判例 | 结论 |
|------|------|------|
| 纯技术项目超 $1,000 | Fiber Checkout 申请 $1,200 | 调档至 $1,000 通过 |
| 高难度论证失败 | CKB Probe 申请 $1,500（eBPF 门槛） | 维持 $1,000（纯技术档） |
| 首个技术+社区综合 $2,000 | Nervos Brain | 通过，含 AI 引擎+社区推广+种子测试 |
| 预算设计不符合惯例 | zk-Lock | 委员会主动给出 $900 方案 |
| 超范围应转 DAO | Corven（经济模型过大） | 拒绝并引导 Community Fund DAO |
| 范围太大 | TiKo / NNCBN | 缩减范围与预算后再审（NNCBN 砍至 $1,080） |
| 冒名顶替风险 | Fiber Submarine（GitHub 与推特账号不匹配） | 直接拒绝 |
| 教育类项目标准 | CKB Developer Onboarding / DevLaunch Kit | 须熟悉生态+事实准确，否则中期拒付/关停 |
| 重复造轮子 | CellKit Actions（切割 CCC 已有流程） | 拒绝，对生态无价值 |
| 生态已有同类 | CKB NFT Market（已有 Omiga）/ Fiber Pay Studio（已有 Fiber Checkout） | 拒绝 |
| 基础不可用 | Hash This（交易构造不成功） | 关闭，"修炼内功再来" |
| 与 Fiber 无关却挂名 | FiberTap | 拒绝 |
| 关停后争论 | Cell Sandbox | 原则上不继续支付，Spark 资金用于 MVP 而非购买工时 |

## 项目判例时间线（按项目分组，自动生成）

"""

def main():
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    meetings = sorted(data.get("meetings", []), key=lambda m: m.get("date", ""))
    projects = {}
    for m in meetings:
        date = m.get("date", "?")
        for r in m.get("resolutions", []):
            name = norm(r.get("project"))
            if not name:
                continue
            projects.setdefault(name, []).append({
                "date": date,
                "decision": (r.get("decision") or "讨论").strip() or "讨论",
                "budget": (r.get("budget") or "").strip(),
                "note": (r.get("note") or "").strip().replace("\n", " "),
                "url": r.get("main_post_url", ""),
            })
    lines = [HEADER.format(now=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))]
    for name in sorted(projects, key=lambda x: x.lower()):
        items = projects[name]
        final = items[-1]["decision"]
        lines.append(f"### {name}（最近状态：{final}）\n")
        lines.append("| 日期 | 决议 | 预算 | 要点 |")
        lines.append("|------|------|------|------|")
        for it in items:
            note = it["note"][:150] + ("…" if len(it["note"]) > 150 else "")
            note = note.replace("|", "\\|")
            lines.append(f"| {it['date']} | {it['decision']} | {it['budget']} | {note} |")
        url = next((it["url"] for it in reversed(items) if it["url"]), "")
        if url:
            lines.append(f"\n帖子: {url}")
        lines.append("")
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"OK: {OUT_FILE} ({len(projects)} 个项目, {sum(len(v) for v in projects.values())} 条判例)")

if __name__ == "__main__":
    main()
