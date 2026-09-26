# -*- coding: utf-8 -*-
"""扫描 knowledge/**/*.md，按标题切块并生成 knowledge/index.json。
工具运行时起草本文件产物即可；知识库文件变更后重新运行本脚本更新索引。"""
import os, re, json, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
KDIR = os.path.join(BASE, "knowledge")
OUT = os.path.join(KDIR, "index.json")

# 关键词 → 标签（命中标题或正文即打标）
TAG_RULES = {
    "预算": ["预算", "档位", "funding", "budget", "$", "usd", "ckb 总额", "金额"],
    "预审": ["预审", "pre-review", "格式", "清单", "章节", "模板要求"],
    "验证": ["验证", "verify", "verification", "docker", "npm", "复现", "录屏"],
    "结项": ["结项", "completion", "关闭", "终止", "3.3", "关停"],
    "打款": ["打款", "拨款", "disbursement", "分期", "里程碑"],
    "拒绝": ["拒绝", "rejection", "decline"],
    "通过": ["通过", "approved", "批准"],
    "催更": ["催", "提醒", "通牒", "进展更新", "沉默"],
    "fiber": ["fiber", "纤维"],
    "rgb++": ["rgb++", "rgbpp"],
    "spore": ["spore", "dob"],
    "cell模型": ["cell 模型", "cell model", "utxo", "capacity", "lock script", "type script"],
    "开发工具": ["lumos", "ccc", "ckb-cli", "offckb", "sdk", "cli", "npm", "开发工具"],
    "钱包": ["钱包", "wallet", "joyid", "ckbull", "neuron", "imtoken"],
    "defi": ["dex", "swap", "amm", "defi", "支付", "payment"],
    "教育": ["教育", "教程", "指南", "guide", "onboarding", "文档"],
    "案例": ["判例", "案例", "precedent"],
    "dao": ["dao", "community fund"],
    "节点": ["节点", "node", "全节点", "indexer", "rpc"],
    "ai": ["ai", "神经网络", "模型", "llm"],
}

def slugify(s):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", s.lower()).strip("-")
    return s[:60]

def chunk_file(path, rel):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    chunks = []
    # 按 ## / ### 标题切块（保留标题行）
    parts = re.split(r"(?m)^(#{2,3} .+)$", text)
    # parts[0] 是文件头（可能为空），之后 (title, body) 交替
    preamble = parts[0].strip()
    pairs = []
    if preamble and len(preamble) > 50:
        first_title = "# 文件说明"
        pairs.append((first_title, preamble))
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        pairs.append((title, body.strip()))
    for title, body in pairs:
        if not body and not title:
            continue
        content = (title + "\n" + body).strip()
        if len(content) < 30:
            continue
        hay = (title + " " + body).lower()
        tags = sorted({tag for tag, kws in TAG_RULES.items() if any(k in hay for k in kws)})
        chunks.append({
            "id": f"{rel}:{slugify(title)}",
            "file": rel,
            "heading": title.lstrip("# ").strip(),
            "level": 2 if title.startswith("## ") else 3,
            "tags": tags,
            "chars": len(content),
            "preview": content[:80].replace("\n", " "),
        })
    return chunks

def load_chunks_with_content(kdir=None):
    """读取所有 md 并按标题切块，返回含完整 content 的块列表（供工具运行时召回）。"""
    kdir = kdir or KDIR
    result = []
    for root, _, files in os.walk(kdir):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, kdir).replace("\\", "/")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            parts = re.split(r"(?m)^(#{2,3} .+)$", text)
            pairs = []
            preamble = parts[0].strip()
            if preamble and len(preamble) > 50:
                pairs.append(("# 文件说明", preamble))
            for i in range(1, len(parts), 2):
                title = parts[i].strip()
                body = parts[i + 1] if i + 1 < len(parts) else ""
                pairs.append((title, body.strip()))
            for title, body in pairs:
                content = (title + "\n" + body).strip()
                if len(content) < 30:
                    continue
                hay = (title + " " + body).lower()
                tags = sorted({tag for tag, kws in TAG_RULES.items() if any(k in hay for k in kws)})
                result.append({
                    "id": f"{rel}:{slugify(title)}",
                    "file": rel,
                    "heading": title.lstrip("# ").strip(),
                    "tags": tags,
                    "chars": len(content),
                    "content": content,
                })
    return result


def main():
    all_chunks = []
    for root, _, files in os.walk(KDIR):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, KDIR).replace("\\", "/")
            all_chunks.extend(chunk_file(path, rel))
    idx = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "chunk_count": len(all_chunks),
        "chunks": all_chunks,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    print(f"OK: {OUT} ({len(all_chunks)} chunks)")

if __name__ == "__main__":
    main()
