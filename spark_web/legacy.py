from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from .config import CACHE_PATH, DATA_PATH, ENV_PATH, ROOT, load_env
from .forum import clean_html, fetch_topic, fetch_topic_posts


_LOCK = threading.RLock()
_SECRET_FIELDS = {"kimi_api_key", "ai_api_key", "nervos_username", "nervos_password"}
_EDITABLE_SECTIONS = {"templates", "meetings", "characters", "board_whitelist"}


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def load_data() -> dict:
    with _LOCK:
        try:
            data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for field in _SECRET_FIELDS:
            data.pop(field, None)
        data.setdefault("templates", {})
        data.setdefault("meetings", [])
        data.setdefault("characters", [])
        data.setdefault("board_whitelist", {})
        data.setdefault("reply_tone_knowledge", "")
        return data


def save_data(data: dict) -> None:
    clean = dict(data)
    for field in _SECRET_FIELDS:
        clean.pop(field, None)
    with _LOCK:
        _atomic_json(DATA_PATH, clean)


def replace_section(name: str, value: Any) -> dict:
    if name not in _EDITABLE_SECTIONS:
        raise ValueError("不允许修改该数据区")
    data = load_data()
    data[name] = value
    save_data(data)
    return {"ok": True, "section": name}


def save_tone(value: str) -> dict:
    data = load_data()
    data["reply_tone_knowledge"] = str(value)
    save_data(data)
    return {"ok": True}


def save_ai_preferences(provider: str, model: str) -> dict:
    if provider not in {"kimi", "siliconflow"}:
        raise ValueError("不支持的 AI 服务商")
    data = load_data()
    data["ai_provider"] = provider
    data["ai_model"] = str(model)
    save_data(data)
    return {"ok": True}


def load_cache() -> dict:
    with _LOCK:
        try:
            value = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}


def save_cache(value: dict) -> None:
    with _LOCK:
        _atomic_json(CACHE_PATH, value)


def apply_whitelist(board: dict, whitelist: dict) -> dict:
    """应用白名单：帖子从所有原分组移除，加入目标分组。

    目标可以是 7 个状态桶，也可以是 spark_program 子分组
    （pre_review / non_proposal / official），与桌面版一致。
    """
    classified = board.get("classified") or {}
    destinations = {"submitted", "pending", "in_progress", "rejection", "completion", "closure"}
    spark_targets = {"pre_review", "non_proposal", "official"}
    all_posts: dict[str, dict] = {}
    for posts in board.get("all_posts", {}).values():
        for post in posts:
            all_posts[str(post.get("id"))] = post
    spark = classified.setdefault("spark_program", {})
    for posts in spark.values():
        if isinstance(posts, list):
            for post in posts:
                all_posts[str(post.get("id"))] = post
    for bucket in destinations:
        for post in classified.get(bucket, []):
            all_posts[str(post.get("id"))] = post
    for raw_id, bucket in (whitelist or {}).items():
        topic_id = str(raw_id)
        if bucket not in destinations | spark_targets or topic_id not in all_posts:
            continue
        post = all_posts[topic_id]
        for existing_bucket in destinations:
            classified[existing_bucket] = [p for p in classified.get(existing_bucket, [])
                                           if str(p.get("id")) != topic_id]
        for key in spark_targets:
            spark[key] = [p for p in spark.get(key, []) if str(p.get("id")) != topic_id]
        if bucket in spark_targets:
            spark.setdefault(bucket, [])
            if not any(str(p.get("id")) == topic_id for p in spark[bucket]):
                spark[bucket].append(post)
        else:
            classified.setdefault(bucket, [])
            if not any(str(p.get("id")) == topic_id for p in classified[bucket]):
                classified[bucket].append(post)
    board["classified"] = classified
    return board


def knowledge_root() -> Path:
    return ROOT / "knowledge"


def knowledge_files() -> list[dict]:
    root = knowledge_root()
    values: list[dict] = []
    if root.exists():
        for path in sorted(root.rglob("*.md")):
            values.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size})
    skill = ROOT / "committee_phrasing_skill.md"
    if skill.exists():
        values.append({"path": "@committee_phrasing_skill.md", "size": skill.stat().st_size})
    return values


def _knowledge_path(relative: str) -> Path:
    if relative == "@committee_phrasing_skill.md":
        return (ROOT / "committee_phrasing_skill.md").resolve()
    root = knowledge_root().resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError("知识库路径无效")
    if target.suffix.lower() != ".md":
        raise ValueError("只能读取或保存 Markdown 文件")
    return target


def read_knowledge(relative: str) -> dict:
    target = _knowledge_path(relative)
    if not target.is_file():
        raise FileNotFoundError(relative)
    return {"path": relative, "content": target.read_text(encoding="utf-8")}


def write_knowledge(relative: str, content: str) -> dict:
    target = _knowledge_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(str(content), encoding="utf-8")
    os.replace(temp, target)
    return {"ok": True, "path": relative}


def ai_settings() -> dict:
    data = load_data()
    env = load_env()
    provider = data.get("ai_provider") or "siliconflow"
    model = data.get("ai_model") or ("moonshot-v1-8k" if provider == "kimi" else "deepseek-ai/DeepSeek-V3")
    return {
        "provider": provider,
        "model": model,
        "has_key": bool(env.get("SPARK_KIMI_API_KEY") if provider == "kimi" else env.get("SPARK_AI_API_KEY")),
    }


def public_state(quick_links: dict[str, str]) -> dict:
    data = load_data()
    cache = load_cache()
    return {
        "templates": merged_templates(),
        "meetings": data.get("meetings", []),
        "characters": data.get("characters", []),
        "tone": data.get("reply_tone_knowledge", ""),
        "board_whitelist": data.get("board_whitelist", {}),
        "board": cache,
        "knowledge": knowledge_files(),
        "links": quick_links,
        "ai": ai_settings(),
    }


_TRACKER_MODULE = None


def tracker_module():
    """无头加载桌面版 spark_task_tracker.py，复用其知识库/分类/Prompt 函数。"""
    global _TRACKER_MODULE
    if _TRACKER_MODULE is None:
        import importlib.util
        path = ROOT / "spark_task_tracker.py"
        spec = importlib.util.spec_from_file_location("spark_legacy_tracker", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        _TRACKER_MODULE = module
    return _TRACKER_MODULE


def merged_templates() -> dict:
    """内置 20 个模板（桌面版 TEMPLATES）+ 用户保存的覆盖，与桌面版行为一致。"""
    data = load_data()
    merged: dict[str, dict] = {}
    try:
        for name, info in tracker_module().TEMPLATES.items():
            merged[name] = {"desc": info.get("desc", ""), "template": info.get("template", "")}
    except Exception:
        pass
    for name, custom in (data.get("templates") or {}).items():
        text = custom.get("template", "") if isinstance(custom, dict) else str(custom)
        if name in merged:
            merged[name]["template"] = text
        else:
            merged[name] = {"desc": (custom.get("desc", "") if isinstance(custom, dict) else ""),
                            "template": text}
    return merged


def parse_post_from_topic(topic: dict) -> dict:
    """把 Talk 主题 JSON 解析为起草用的 post 结构（对齐桌面版 _parse_talk_post）。"""
    import html as _html
    posts = topic.get("post_stream", {}).get("posts", [])
    first = posts[0] if posts else {}
    cooked = first.get("cooked", "")
    text = re.sub(r"<[^>]+>", "", cooked)
    text = re.sub(r"\n\s*\n", "\n\n", text).strip()
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
            raw = match.group(1).replace(",", "").replace(" ", "")
            try:
                val = float(raw)
                if 100 < val < 1000000:
                    budget_amount = raw
                    break
            except ValueError:
                continue
    headings = []
    for hm in re.finditer(r"<h[1-6][^>]*>(.*?)</h[1-6]>", cooked, re.IGNORECASE | re.DOTALL):
        h = _html.unescape(re.sub(r"<[^>]+>", "", hm.group(1))).strip()
        if h and len(h) < 200:
            headings.append(h)
    return {
        "title": topic.get("title", ""),
        "author": first.get("username", ""),
        "content": text[:3000] if len(text) > 3000 else text,
        "content_full": text,
        "headings": headings,
        "full_url": topic.get("url", ""),
        "budget_amount": budget_amount,
    }


_CKBRATE_CACHE: tuple[float, float | None, float | None] = (0.0, None, None)


def get_ckb_usd_rate() -> tuple[float | None, float | None]:
    """CKB/USD 实时汇率（CoinGecko），结果缓存 30 分钟。返回 (ckb_price_usd, rate)。"""
    import time
    import urllib.request
    now = time.time()
    cached_at, price, rate = _CKBRATE_CACHE
    if price and now - cached_at < 1800:
        return price, rate
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=nervos-network&vs_currencies=usd"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        price = data.get("nervos-network", {}).get("usd", 0)
        if price and price > 0:
            rate = 1.0 / price
            _CKBRATE_CACHE = (now, price, rate)
            return price, rate
    except Exception:
        pass
    return None, None


def format_resolution_info(meeting: dict | None) -> tuple[str, str]:
    """把会议决议格式化为桌面版同款文本（项目/决议/轮次/预算/备注），返回 (info, date)。"""
    if not meeting:
        return "", ""
    resolutions = meeting.get("resolutions") or []
    if not resolutions:
        return "", str(meeting.get("date", ""))
    lines = []
    for r in resolutions:
        lines.append(f"项目: {r.get('project', '')}")
        lines.append(f"决议: {r.get('decision', '')}")
        lines.append(f"轮次: {r.get('round', '')}")
        lines.append(f"预算: {r.get('budget', '')}")
        lines.append(f"备注: {r.get('note', '')}")
        lines.append("---")
    return "\n".join(lines), str(meeting.get("date", ""))


def build_ai_prompt(payload: dict) -> tuple[str, str, str]:
    """兼容旧接口：返回 (provider, model, prompt)。新代码请用 ai_draft_plan()。"""
    plan = ai_draft_plan(payload)
    return plan["provider"], plan["model"], plan["stages"][0]["prompt"]


def ai_draft_plan(payload: dict) -> dict:
    """构建对齐桌面版 _build_kimi_prompt 的完整 Prompt 计划。

    预审模板返回两阶段（分析 → 格式化），其余单阶段。
    """
    data = load_data()
    settings = ai_settings()
    tracker = tracker_module()
    provider = str(payload.get("provider") or settings["provider"])
    model = str(payload.get("model") or settings["model"])
    template_name = str(payload.get("template") or "")
    templates = merged_templates()
    template = templates.get(template_name, {})
    template_text = template.get("template", "")
    template_desc = template.get("desc", "")
    if not template_text:
        raise ValueError("请选择有效模板")

    topic = topic_text(str(payload.get("topic_url") or payload.get("topic_id") or ""))
    topic_full = fetch_topic(topic["id"])
    post = parse_post_from_topic(topic_full)

    character_id = str(payload.get("character") or "")
    character = next((x for x in data.get("characters", []) if str(x.get("id")) == character_id and x.get("enabled")), None)
    meeting = next((x for x in data.get("meetings", []) if str(x.get("id")) == str(payload.get("meeting_id") or "")), None)
    resolution_info, meeting_date = format_resolution_info(meeting)
    tone_knowledge = data.get("reply_tone_knowledge", "")
    supplement_content = str(payload.get("supplement") or "").strip()

    stages = []
    if "预审" in template_name:
        stages.append({"name": "分析", "prompt": _pre_review_analysis_prompt(
            template_name, template_desc, template_text, post,
            tone_knowledge=tone_knowledge, supplement_content=supplement_content)})
        stages.append({"name": "格式化", "prompt": None})  # 第二阶段依赖第一阶段产出
    else:
        stages.append({"name": "生成", "prompt": _standard_prompt(
            tracker, template_name, template_desc, template_text, post,
            resolution_info=resolution_info, tone_knowledge=tone_knowledge,
            supplement_content=supplement_content, character=character)})

    return {
        "provider": provider, "model": model, "stages": stages,
        "template_name": template_name, "template_text": template_text,
        "post": {"title": post.get("title", ""), "author": post.get("author", ""),
                 "full_url": post.get("full_url", "")},
        "meeting_date": meeting_date,
        "is_pre_review": "预审" in template_name,
        "topic_id": topic["id"],
    }


def pre_review_format_prompt(template_name: str, template_text: str, analysis: str,
                             post: dict, tone_knowledge: str = "", supplement_content: str = "") -> str:
    """预审阶段二：把分析报告严格按预审反馈模板格式化为 Markdown 回复。"""
    extra = ""
    if tone_knowledge:
        extra += f"\n## 回复措辞知识库（必须遵循）\n{tone_knowledge}\n"
    if supplement_content:
        extra += f"\n## 补充内容（必须参考并体现）\n{supplement_content}\n"
    return f"""# Spark Program 预审反馈格式化输出任务（阶段二：按模板输出最终回复）

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


def _pre_review_analysis_prompt(template_name: str, template_desc: str, template_text: str,
                                post: dict, tone_knowledge: str = "", supplement_content: str = "") -> str:
    tracker = tracker_module()
    ref = tracker.fetch_pre_review_reference()
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
    funding_rules = tracker.load_funding_rules_text()
    review_knowledge = tracker.retrieve_knowledge_context("预审", post, max_chars=7000)
    knowledge_section = ""
    if review_knowledge:
        knowledge_section = f"""
## 参考四：技术评审手册、生态已有项目与历史判例（知识库，分析时必须对照）
```
{review_knowledge}
```
"""
    return f"""# Spark Program 提案预审分析任务（阶段一：深度分析）

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


def _standard_prompt(tracker, template_name: str, template_desc: str, template_text: str,
                     post: dict, resolution_info: str = "", tone_knowledge: str = "",
                     supplement_content: str = "", character: dict | None = None) -> str:
    extra_context = ""
    if resolution_info:
        extra_context += f"\n## 委员会最新决议\n{resolution_info}\n"
    if supplement_content:
        extra_context += f"\n## 补充内容（必须参考）\n{supplement_content}\n"
    knowledge_text = tracker.retrieve_knowledge_context(template_name, post)
    if knowledge_text:
        extra_context += (f"\n## 知识库参考（事实依据：规则、技术事实与历史判例；"
                          f"回复中的规则引用、技术判断、先例参照必须与此相符）\n{knowledge_text}\n")
    if tone_knowledge:
        extra_context += f"\n## 回复措辞知识库（必须遵循）\n{tone_knowledge}\n"
    style = (character or {}).get("id", "")
    if character and character.get("content"):
        extra_context += (f"\n## 人物风格（本次回复以 {character.get('display_name', style)} 的身份与风格撰写，"
                          f"措辞、签名与该人物真实发帖风格一致）\n{character['content']}\n")
    use_zz = ("[zz]" in template_name) or (style == "zz_tovarishch")
    if use_zz:
        zz_text = tracker.route_phrasing_sections(
            template_name, os.path.join("style", "zz_tovarishch.md"),
            tracker.ZZ_SECTION_MAP, tracker.ZZ_DEFAULT_SECTIONS)
        if zz_text:
            extra_context += (f"\n## 委员会措辞 Skill — zz_tovarishch 版本"
                              f"（本次起草的简洁度、结构与签名格式以此为准；"
                              f"与通用版冲突时优先本版本）\n{zz_text}\n")
    else:
        skill_text = tracker.route_phrasing_sections(
            template_name, os.path.join("style", "committee_phrasing_skill.md"),
            tracker.PHRASING_SECTION_MAP, tracker.PHRASING_DEFAULT_SECTIONS,
            max_chars=4500)
        if skill_text:
            extra_context += f"\n## 委员会措辞 Skill（必须遵循）\n{skill_text}\n"
    if "Approved" in template_name or "通过" in template_name:
        ckb_price_usd, rate = get_ckb_usd_rate()
        if ckb_price_usd and rate:
            extra_context += f"\n## CKB/USD 实时汇率（Approved 模板必须使用）\n"
            extra_context += f"- 当前 CKB 价格: 1 CKB = {ckb_price_usd:.6f} USD\n"
            extra_context += f"- 当前汇率: 1 USD = {rate:.2f} CKB\n\n"
            extra_context += f"计算要求：如果决议中包含预算金额（如 $50000），请使用该金额除以 CKB 价格（或乘以汇率）计算 CKB 总额。\n"
            extra_context += f"计算格式参考: $金额 * 百分比 @CKB价格 = CKB总额\n"
            extra_context += f"示例: $50000 * 100% @{ckb_price_usd:.6f} = {50000 * rate:.2f} CKB\n"
    return f"""# Spark Program 回复起草任务

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


def topic_id_from_url(value: str) -> str:
    match = re.search(r"/t/(?:[^/]+/)?(\d+)", value or "")
    if not match and str(value).isdigit():
        return str(value)
    if not match:
        raise ValueError("请输入有效的 Nervos Talk 帖子 URL 或 topic ID")
    return match.group(1)


def topic_text(value: str) -> dict:
    topic_id = topic_id_from_url(value)
    topic = fetch_topic(topic_id)
    posts = topic.get("post_stream", {}).get("posts", [])
    normalized = [{
        "post_number": p.get("post_number"),
        "username": p.get("username", ""),
        "created_at": p.get("created_at", ""),
        "text": clean_html(p.get("cooked", "")),
    } for p in posts]
    return {
        "id": topic_id,
        "title": topic.get("title", ""),
        "url": f"https://talk.nervos.org/t/{topic.get('slug','')}/{topic_id}",
        "posts": normalized,
    }


def add_manual_project(value: str, bucket: str) -> dict:
    allowed = {"submitted", "pending", "in_progress", "rejection", "completion", "closure"}
    if bucket not in allowed:
        raise ValueError("无效的项目分组")
    topic = topic_text(value)
    first = (topic.get("posts") or [{}])[0]
    post = {
        "id": int(topic["id"]), "title": topic["title"], "url": topic["url"],
        "author": first.get("username", ""), "created_at": first.get("created_at", ""),
        "posts_count": len(topic.get("posts") or []), "tags": [],
    }
    data = load_data()
    whitelist = dict(data.get("board_whitelist") or {})
    whitelist[str(topic["id"])] = bucket
    data["board_whitelist"] = whitelist
    save_data(data)
    board = load_cache()
    classified = board.setdefault("classified", {})
    for name in allowed:
        classified[name] = [p for p in classified.get(name, []) if str(p.get("id")) != topic["id"]]
    classified.setdefault(bucket, []).append(post)
    board.setdefault("all_posts", {}).setdefault("manual", []).append(post)
    board["last_update"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_cache(board)
    return post


def build_ai_prompt(payload: dict) -> tuple[str, str, str]:
    data = load_data()
    settings = ai_settings()
    provider = str(payload.get("provider") or settings["provider"])
    model = str(payload.get("model") or settings["model"])
    template_name = str(payload.get("template") or "")
    templates = data.get("templates", {})
    template = templates.get(template_name, {})
    template_text = template.get("template", "") if isinstance(template, dict) else str(template)
    if not template_text:
        raise ValueError("请选择有效模板")
    topic = topic_text(str(payload.get("topic_url") or payload.get("topic_id") or ""))
    character_id = str(payload.get("character") or "")
    character = next((x for x in data.get("characters", []) if str(x.get("id")) == character_id and x.get("enabled")), None)
    meeting = next((x for x in data.get("meetings", []) if str(x.get("id")) == str(payload.get("meeting_id") or "")), None)
    knowledge_parts = []
    for item in knowledge_files()[:20]:
        try:
            knowledge_parts.append(read_knowledge(item["path"])["content"][:5000])
        except Exception:
            continue
    prompt = f"""你是 CKB Spark Program 联络员。请依据帖子事实和模板起草可直接发布的回复，不得编造事实。

【模板：{template_name}】
{template_text}

【人物风格】
{(character or {}).get('content', '使用专业、清晰、友善的委员会联络员语气。')}

【统一措辞知识】
{data.get('reply_tone_knowledge', '')}

【会议纪要或决议】
{json.dumps(meeting or {}, ensure_ascii=False)[:16000]}

【补充要求】
{payload.get('supplement', '')}

【项目主题】
{json.dumps(topic, ensure_ascii=False)[:50000]}

【相关知识库】
{chr(10).join(knowledge_parts)[:24000]}

仅输出最终草稿。"""
    return provider, model, prompt


def timeline(board: dict) -> list[dict]:
    posts: dict[str, dict] = {}
    for items in board.get("all_posts", {}).values():
        for post in items:
            posts[str(post.get("id"))] = post
    return sorted(posts.values(), key=lambda x: x.get("created_at", ""), reverse=True)


def new_meeting(payload: dict) -> dict:
    data = load_data()
    meeting = {
        "id": os.urandom(4).hex(),
        "date": str(payload.get("date") or dt.date.today().isoformat()),
        "title": str(payload.get("title") or "委员会会议"),
        "talk_topic_url": str(payload.get("talk_topic_url") or ""),
        "resolutions": payload.get("resolutions") or [],
        "transcript": str(payload.get("transcript") or ""),
        "ai_summary": str(payload.get("ai_summary") or ""),
        "meet_link": str(payload.get("meet_link") or ""),
        "created": dt.date.today().isoformat(),
    }
    data.setdefault("meetings", []).append(meeting)
    save_data(data)
    return meeting


def update_meeting(meeting_id: str, payload: dict) -> dict:
    data = load_data()
    for meeting in data.get("meetings", []):
        if str(meeting.get("id")) == str(meeting_id):
            for key in ("date", "title", "talk_topic_url", "resolutions", "transcript", "ai_summary", "meet_link"):
                if key in payload:
                    meeting[key] = payload[key]
            save_data(data)
            return meeting
    raise KeyError("会议不存在")


def delete_meeting(meeting_id: str) -> dict:
    data = load_data()
    before = len(data.get("meetings", []))
    data["meetings"] = [x for x in data.get("meetings", []) if str(x.get("id")) != str(meeting_id)]
    if len(data["meetings"]) == before:
        raise KeyError("会议不存在")
    save_data(data)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 会议纪要文本解析（移植自桌面版 _parse_meeting_text / _parse_meeting_numbered_format）
# ---------------------------------------------------------------------------

def standardize_decision(raw: str) -> str:
    """标准化决议为5种：通过/待定/拒绝/关闭/完成。"""
    mapping = {
        "通过": "通过", "approved": "通过", "pass": "通过", "approve": "通过",
        "拒绝": "拒绝", "rejected": "拒绝", "reject": "拒绝", "否决": "拒绝",
        "不通过": "拒绝", "fail": "拒绝",
        "待定": "待定", "pending": "待定", "延期": "待定", "补充材料": "待定",
        "关闭": "关闭", "close": "关闭", "closed": "关闭",
        "完成": "完成", "completed": "完成", "done": "完成", "finish": "完成",
    }
    return mapping.get((raw or "").strip().lower(), "待定")


def parse_meeting_numbered_format(text: str) -> list[dict]:
    """解析委员会纪要的真实编号格式：
        2026.07.28 委员会决议纪要
        In-progress / Pending / Submitted 等状态小节
        1. 项目名：决议或预算（备注可跨行，支持 - 开头的补充行）
    返回决议列表；未识别到编号条目时返回空列表（由调用方回退到其他格式）。
    """
    SECTION_WORDS = {"in-progress", "in progress", "inprogress", "pending",
                     "submitted", "rejection", "completion", "closure",
                     "进行中", "待定", "已提交", "拒绝", "完成", "关闭"}
    SECTION_DEFAULT_DECISION = {"pending": "待定", "待定": "待定"}
    DECISION_PATTERNS = [
        (re.compile(r'拒绝|否决|不通过|reject', re.IGNORECASE), "拒绝"),
        (re.compile(r'中期打款|里程碑打款|打款'), "通过"),
        (re.compile(r'通过|approved|pass', re.IGNORECASE), "通过"),
        (re.compile(r'完成结项|结项|完成|complet|done|finish', re.IGNORECASE), "完成"),
        (re.compile(r'关闭|close', re.IGNORECASE), "关闭"),
        (re.compile(r'待定|pending', re.IGNORECASE), "待定"),
    ]
    BUDGET_RE = re.compile(
        r'(\$\s*\d[\d,]*(?:\.\d+)?'
        r'|\d[\d,]*(?:\.\d+)?\s*(?:CKB|USD|USDT|美金|美元)'
        r'|\d[\d,]*(?:\.\d+)?\s*U(?![A-Za-z0-9_]))',
        re.IGNORECASE)
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
        sec_key = stripped.lower().rstrip(':：')
        if sec_key in SECTION_WORDS and len(stripped) < 20:
            flush()
            current_section = sec_key
            continue
        m = item_re.match(stripped)
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

        budget = ""
        bm = BUDGET_RE.search(head) or BUDGET_RE.search(full_text)
        if bm:
            budget = bm.group(1).strip()

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


def parse_meeting_text(text: str) -> dict:
    """从文本中解析会议日期、标题和决议列表。支持多种常见格式。"""
    result = {"date": "", "title": "", "resolutions": []}

    date_match = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', text)
    if date_match:
        y, mo, d = date_match.groups()
        result["date"] = f"{y}-{int(mo):02d}-{int(d):02d}"
    else:
        result["date"] = dt.date.today().isoformat()

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if lines:
        first = lines[0]
        title_candidate = re.sub(r'\d{4}[-/.]\d{1,2}[-/.]\d{1,2}', '', first).strip()
        if title_candidate and len(title_candidate) > 2:
            result["title"] = title_candidate
        elif len(lines) > 1:
            result["title"] = lines[1][:40]
        else:
            result["title"] = "委员会评审会议"
    else:
        result["title"] = "委员会评审会议"

    numbered = parse_meeting_numbered_format(text)
    if numbered:
        result["resolutions"] = numbered
        return result

    pattern1 = re.compile(
        r'(?:^|\n)\s*(?:\d+[\.、]\s*)?'
        r'([^|\n]+?)\s*[|\t]\s*'
        r'(通过|拒绝|待定|关闭|完成|延期|补充材料|否决|不通过|approved|rejected|pending|close|completed|pass|fail|done|finish)\s*[|\t]\s*'
        r'([^|\n]*)\s*(?:[|\t]\s*(.*))?',
        re.IGNORECASE
    )

    for m in pattern1.finditer(text):
        project = m.group(1).strip()
        budget = m.group(3).strip() if m.group(3) else ""
        note = m.group(4).strip() if m.group(4) else ""
        decision = standardize_decision(m.group(2))
        project = re.sub(r'^[\s\-]*(?:项目|Project)[\s:：]+', '', project, flags=re.IGNORECASE).strip()
        result["resolutions"].append({
            "project": project,
            "decision": decision,
            "budget": budget,
            "note": note
        })

    if not result["resolutions"]:
        for line in lines:
            m = re.search(
                r'(?:项目|Project)[\s:：]+([^|]+)\s*[|\t]\s*'
                r'(?:决议|Decision)[\s:：]+(通过|拒绝|待定|关闭|完成|延期|补充材料)\s*[|\t]\s*'
                r'(?:预算|Budget)[\s:：]+([^|]*)\s*(?:[|\t]\s*(?:备注|Note)[\s:：]+(.*))?',
                line, re.IGNORECASE
            )
            if m:
                result["resolutions"].append({
                    "project": m.group(1).strip(),
                    "decision": standardize_decision(m.group(2).strip()),
                    "budget": m.group(3).strip(),
                    "note": m.group(4).strip() if m.group(4) else ""
                })

    if not result["resolutions"]:
        for line in lines[1:]:
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
                parts = line.split(kw)
                if len(parts) >= 2:
                    project_part = parts[0].strip()
                    project_part = re.sub(r'^[\s\-\d\.\、]+', '', project_part).strip()
                    budget_match = re.search(r'(\d[\d,\.]+)', line)
                    budget = budget_match.group(1) if budget_match else ""
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


# ---------------------------------------------------------------------------
# 决议主帖匹配 / Talk 关联（移植自桌面版）
# ---------------------------------------------------------------------------

def find_post_by_project_name(project_name: str) -> str:
    """从看板缓存中查找与项目名称匹配的帖子 URL。"""
    if not project_name:
        return ""
    cache = load_cache()
    if not cache:
        return ""
    all_posts = cache.get("all_posts", {})
    proj_lower = project_name.lower().strip()
    for tag_posts in all_posts.values():
        for post in tag_posts:
            title = (post.get("title") or "").lower()
            if proj_lower in title or title in proj_lower:
                return post.get("url", "")
    return ""


def refresh_resolution_posts(meeting_id: str) -> dict:
    """重新从看板数据匹配所有决议的主帖 URL。"""
    data = load_data()
    for m in data.get("meetings", []):
        if str(m.get("id")) == str(meeting_id):
            resolutions = m.get("resolutions", [])
            matched = 0
            for r in resolutions:
                proj_name = r.get("project", "")
                if not proj_name:
                    continue
                matched_url = find_post_by_project_name(proj_name)
                if matched_url:
                    r["main_post_url"] = matched_url
                    matched += 1
            save_data(data)
            return {"matched": matched, "total": len(resolutions)}
    raise KeyError("会议不存在")


def _talk_content_from_url(url: str, limit: int = 1500) -> str:
    """抓取 Talk 帖子（主帖）正文纯文本。"""
    tid = topic_id_from_url(url)
    if not tid:
        return ""
    try:
        topic = fetch_topic(tid)
    except Exception:
        return ""
    posts = topic.get("post_stream", {}).get("posts", [])
    if not posts:
        return ""
    return clean_html(posts[0].get("cooked", ""))[:limit]


def link_talk(meeting_id: str, index: int, url: str) -> dict:
    """获取 Talk 帖子内容并保存到本地，关联到指定决议项。"""
    url = (url or "").strip()
    m = re.search(r"/t/([^/]+)/(\d+)", url)
    if not m:
        raise ValueError("URL 格式不正确，应为 https://talk.nervos.org/t/slug/1234")
    slug, post_id = m.group(1), m.group(2)
    json_topic = fetch_topic(post_id)
    posts = json_topic.get("post_stream", {}).get("posts", [])
    if not posts:
        raise ValueError("未获取到帖子内容")
    text = clean_html(posts[0].get("cooked", ""))
    title = json_topic.get("title", "")

    data = load_data()
    for meeting in data.get("meetings", []):
        if str(meeting.get("id")) == str(meeting_id):
            resolutions = meeting.get("resolutions", [])
            if not (0 <= int(index) < len(resolutions)):
                raise KeyError("决议项不存在")
            date = meeting.get("date", "unknown")
            safe_date = re.sub(r'[\\/:*?"<>|]', '_', date)
            dir_path = ROOT / "meetings" / safe_date
            dir_path.mkdir(parents=True, exist_ok=True)
            filepath = dir_path / f"{slug}_{post_id}.md"
            filepath.write_text(
                f"# {title}\n\nURL: {url}\n\nDate: {date}\n\n{text}",
                encoding="utf-8")
            resolutions[int(index)]["talk_url"] = url
            resolutions[int(index)]["talk_file"] = str(filepath)
            save_data(data)
            return {"file": str(filepath), "title": title}
    raise KeyError("会议不存在")


# ---------------------------------------------------------------------------
# 回复措辞知识库（移植自桌面版 _update_tone_knowledge）
# ---------------------------------------------------------------------------

def ai_credentials() -> tuple[str, str, str]:
    """返回 (provider, api_key, model)，key 取自 .env。"""
    data = load_data()
    provider = (data.get("ai_provider") or "siliconflow").strip()
    env = load_env()
    if provider == "kimi":
        key = (env.get("SPARK_KIMI_API_KEY") or "").strip()
        model = (data.get("ai_model") or env.get("SPARK_KIMI_MODEL") or "moonshot-v1-8k").strip()
    else:
        provider = "siliconflow"
        key = (env.get("SPARK_AI_API_KEY") or "").strip()
        model = (data.get("ai_model") or env.get("SPARK_AI_MODEL") or "deepseek-ai/DeepSeek-V3").strip()
    return provider, key, model


def generate_tone_knowledge() -> str:
    """调用 AI 分析所有会议决议纪要（含主帖、关联帖内容），生成回复措辞知识库。"""
    meetings = load_data().get("meetings", [])
    if not meetings:
        raise ValueError("暂无会议纪要，无法生成知识库")
    provider, key, _model = ai_credentials()
    if not key:
        raise ValueError("未配置 API Key，请先在设置中配置 SPARK_AI_API_KEY / SPARK_KIMI_API_KEY")
    tracker = tracker_module()
    call_ai = getattr(tracker, "call_ai_api", None) if tracker else None
    if not call_ai:
        raise RuntimeError("无法加载本地工具模块的 AI 接口")

    all_lines = []
    for m in meetings:
        all_lines.append(f"## 会议: {m.get('title', '')} ({m.get('date', '')})")
        for r in m.get("resolutions", []):
            all_lines.append(f"项目: {r.get('project', '')} | 决议: {r.get('decision', '')} | 轮次: {r.get('round', '')}")
            all_lines.append(f"备注: {r.get('note', '')}")
            main_url = r.get("main_post_url", "")
            if main_url:
                main_text = _talk_content_from_url(main_url)
                if main_text:
                    all_lines.append(f"主帖内容摘要: {main_text}")
            talk_url = r.get("talk_url", "")
            talk_file = r.get("talk_file", "")
            talk_text = ""
            if talk_file and os.path.exists(talk_file):
                try:
                    talk_text = Path(talk_file).read_text(encoding="utf-8")[:1500]
                except Exception:
                    talk_text = ""
            elif talk_url:
                talk_text = _talk_content_from_url(talk_url)
            if talk_text:
                all_lines.append(f"关联帖内容摘要: {talk_text}")
            all_lines.append("")
        all_lines.append("")
    all_content = "\n".join(all_lines)
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
    content, err = call_ai(provider, key, prompt, "deepseek-ai/DeepSeek-V4-Flash")
    if err or not content:
        raise RuntimeError(f"AI 分析失败: {err or '空返回'}")
    save_tone(content.strip())
    return content.strip()


# ---------------------------------------------------------------------------
# 上会项目（移植自桌面版 _refresh_meeting_items / _ai_summarize_meeting_items）
# ---------------------------------------------------------------------------

COMMITTEE_MEMBERS = {"zz_tovarishch", "xingtianchunyan", "yixiu.ckbfans.bit", "hanssen"}

_MEETING_ITEMS: dict = {"items": {"in_progress": [], "pending": [], "submitted": []}, "updated_at": ""}


def _parse_talk_time(value: str) -> dt.datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def meeting_items_refresh() -> dict:
    """刷新上会项目列表：获取帖子详情、判断是否需要上会。结果带时间戳缓存。"""
    cache = load_cache()
    classified = (cache or {}).get("classified") or {}
    all_posts = []
    for key in ["in_progress", "pending", "submitted"]:
        for post in classified.get(key, []):
            all_posts.append((key, post))

    meeting_items: dict[str, list] = {"in_progress": [], "pending": [], "submitted": []}
    if not all_posts:
        _MEETING_ITEMS["items"] = meeting_items
        _MEETING_ITEMS["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        return dict(_MEETING_ITEMS)

    for cat_key, post in all_posts:
        post_id = post.get("id")
        url = post.get("url", "")
        if not post_id or not url:
            continue
        try:
            posts_list = fetch_topic_posts(str(post_id))
        except Exception:
            continue
        if not posts_list:
            continue

        op_username = (posts_list[0].get("username") or "").lower()
        if not op_username:
            continue
        op_posts = [p for p in posts_list if (p.get("username") or "").lower() == op_username]
        if not op_posts:
            continue

        last_op_post = max(op_posts, key=lambda p: p.get("post_number", 0))
        last_op_post_number = last_op_post.get("post_number", 0)
        last_op_time = last_op_post.get("created_at", "")

        committee_after_op = False
        committee_last_reply_time = ""
        for p in posts_list:
            if p.get("post_number", 0) > last_op_post_number:
                author = (p.get("username") or "").lower()
                if author in COMMITTEE_MEMBERS:
                    committee_after_op = True
                    if not committee_last_reply_time or p.get("created_at", "") > committee_last_reply_time:
                        committee_last_reply_time = p.get("created_at", "")

        needs_meeting = False
        overdue_7d = False
        # 判定规则（按行天要求）：帖子最近一条回复在 7 天内、且作者是
        # zz_tovarishch / xingtianchunyan 之一 → 委员会已回复，不上会；
        # 否则保持原本「OP 最后一条之后没有委员会回复」的判断。
        now = dt.datetime.now()
        committee_replied_recent = False
        last_topic_post = max(posts_list, key=lambda p: p.get("post_number", 0))
        last_topic_time = _parse_talk_time(last_topic_post.get("created_at", ""))
        if last_topic_time and (now - last_topic_time).days < 7:
            if (last_topic_post.get("username") or "").lower() in {"zz_tovarishch", "xingtianchunyan"}:
                committee_replied_recent = True
        if not committee_replied_recent:
            if not committee_after_op:
                needs_meeting = True
            elif cat_key == "in_progress" and committee_last_reply_time:
                committee_time = _parse_talk_time(committee_last_reply_time)
                if committee_time and (now - committee_time).days > 7:
                    needs_meeting = True
                    overdue_7d = True

        if needs_meeting:
            latest_op_content = clean_html(last_op_post.get("cooked", ""))
            irrelevant_mark = ""
            if len(op_posts) > 1:
                op_post_numbers = sorted([p.get("post_number", 0) for p in op_posts])
                for i in range(len(op_post_numbers) - 1):
                    for p in posts_list:
                        pn = p.get("post_number", 0)
                        if op_post_numbers[i] < pn < op_post_numbers[i + 1]:
                            author = (p.get("username") or "").lower()
                            if author not in COMMITTEE_MEMBERS and author != op_username:
                                irrelevant_mark = "[无关] "
                                break
                    if irrelevant_mark:
                        break

            meeting_items[cat_key].append({
                "post_id": post_id,
                "title": post.get("title", ""),
                "author": post.get("original_poster_username") or post.get("author", ""),
                "url": url,
                "latest_op_content": latest_op_content[:300] if latest_op_content else "",
                "irrelevant_mark": irrelevant_mark,
                "last_op_time": last_op_time,
                "overdue_7d": overdue_7d,
                "ai_summary": "",
            })

    for key in meeting_items:
        meeting_items[key].sort(key=lambda x: x.get("last_op_time", ""), reverse=False)

    _MEETING_ITEMS["items"] = meeting_items
    _MEETING_ITEMS["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return dict(_MEETING_ITEMS)


def meeting_items_get() -> dict:
    """读取缓存的上会项目列表（无缓存时触发一次刷新）。"""
    if not _MEETING_ITEMS.get("updated_at"):
        return meeting_items_refresh()
    return dict(_MEETING_ITEMS)


def meeting_items_summarize() -> dict:
    """对缓存中尚无 AI 总结的上会项目逐条生成一句话总结（≤30字）。"""
    items = _MEETING_ITEMS.get("items") or {}
    provider, key, model = ai_credentials()
    if not key:
        return {"summarized": 0, "error": "未配置 API Key"}
    tracker = tracker_module()
    call_ai = getattr(tracker, "call_ai_api", None) if tracker else None
    if not call_ai:
        return {"summarized": 0, "error": "无法加载本地工具模块的 AI 接口"}

    summarized = 0
    for cat_key in ["in_progress", "pending", "submitted"]:
        for item in items.get(cat_key, []):
            if item.get("ai_summary"):
                continue
            content = item.get("latest_op_content", "")
            if not content:
                continue
            prompt = f"""请用一句话（不超过30个字）简要总结以下Spark Program项目帖子的最新回复内容：

{content[:500]}

只输出总结，不要其他内容。"""
            try:
                summary, err = call_ai(provider, key, prompt, model)
                if summary and not err:
                    item["ai_summary"] = summary.strip().strip('"').strip("'").strip()
                    summarized += 1
            except Exception:
                continue
    _MEETING_ITEMS["items"] = items
    return {"summarized": summarized, "items": dict(_MEETING_ITEMS["items"]), "updated_at": _MEETING_ITEMS["updated_at"]}


# ---------------------------------------------------------------------------
# 知识库浏览 / 索引重建
# ---------------------------------------------------------------------------

def knowledge_chunks(tag: str | None = None) -> list[dict]:
    """列出知识块（可按标签过滤），供前端浏览。"""
    tracker = tracker_module()
    if not tracker:
        return []
    chunks = tracker.load_knowledge_chunks() or []
    out = []
    for ch in chunks:
        tags = ch.get("tags") or []
        if tag and tag not in tags:
            continue
        content = ch.get("content") or ""
        out.append({
            "id": ch.get("id", ""),
            "file": ch.get("file", ""),
            "heading": ch.get("heading", ""),
            "tags": tags,
            "chars": ch.get("chars", len(content)),
            "preview": content[:300],
        })
    return out


def knowledge_chunk_content(chunk_id: str) -> dict:
    """按 id 读取单个知识块全文。"""
    tracker = tracker_module()
    if not tracker:
        raise RuntimeError("无法加载本地工具模块")
    for ch in tracker.load_knowledge_chunks() or []:
        if ch.get("id") == chunk_id:
            return {"id": ch.get("id"), "file": ch.get("file"), "heading": ch.get("heading"),
                    "tags": ch.get("tags") or [], "content": ch.get("content") or ""}
    raise KeyError("知识块不存在")


def knowledge_index() -> list[dict]:
    """标签 → 知识块数量 统计。"""
    counts: dict[str, int] = {}
    for ch in knowledge_chunks():
        for t in ch["tags"]:
            counts[t] = counts.get(t, 0) + 1
    return [{"tag": t, "count": n} for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


# ---------------------------------------------------------------------------
# 白名单批量操作 / AI 分类后的自动建任务
# ---------------------------------------------------------------------------

def add_whitelist(ids: list, target: str) -> dict:
    """批量把帖子加入白名单目标分组，并重放白名单到看板缓存。"""
    destinations = {"submitted", "pending", "in_progress", "rejection", "completion", "closure"}
    spark_targets = {"pre_review", "non_proposal", "official"}
    if target not in destinations | spark_targets:
        raise ValueError("不支持的白名单目标分组")
    data = load_data()
    whitelist = data.setdefault("board_whitelist", {})
    added = []
    for raw_id in ids:
        topic_id = str(raw_id)
        if whitelist.get(topic_id) != target:
            whitelist[topic_id] = target
            added.append(topic_id)
    save_data(data)
    board = load_cache()
    if board:
        apply_whitelist(board, whitelist)
        save_cache(board)
    return {"ok": True, "added": added, "whitelist": whitelist}


def auto_create_review_tasks(store) -> list[str]:
    """根据看板缓存中的 spark_program 分类结果自动创建【预审】/【删除标签】任务。"""
    cache = load_cache()
    classified = (cache or {}).get("classified") or {}
    spark = classified.get("spark_program") or {}
    specs = []
    for post in spark.get("pre_review", []):
        specs.append((f"【预审】{post.get('title', '未知帖子')}", "预审", post,
                      f"帖子: {post.get('url', '')}\n作者: {post.get('author', '')}\n仅带Spark-Program标签，需要预审反馈"))
    for post in spark.get("non_proposal", []):
        specs.append((f"【删除标签】{post.get('title', '未知帖子')}", "删除标签", post,
                      f"帖子: {post.get('url', '')}\n作者: {post.get('author', '')}\nAI判断为非提案，需删除Spark-Program标签"))
    added = []
    for title, task_type, post, note in specs:
        if store.query("SELECT 1 FROM tasks WHERE title = ?", (title,)):
            continue
        store.create_task({
            "title": title,
            "type": task_type,
            "project": post.get("title", ""),
            "deadline": "",
            "note": note,
            "link": post.get("url", ""),
            "done": False,
        })
        added.append(title)
    return added


def reset_characters() -> dict:
    """重置人物角色为桌面版默认（DEFAULT_CHARACTERS）。"""
    tracker = tracker_module()
    defaults = getattr(tracker, "DEFAULT_CHARACTERS", None)
    if not defaults:
        raise RuntimeError("无法加载默认人物角色")
    data = load_data()
    data["characters"] = [dict(c) for c in defaults]
    save_data(data)
    return {"ok": True, "characters": data["characters"]}
