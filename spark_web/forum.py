from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .config import setting


TALK_ORIGIN = "https://talk.nervos.org"


def _request_json(url: str, *, method: str = "GET", payload: dict | None = None,
                  headers: dict | None = None, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    base_headers = {"User-Agent": "Spark-Program-Workbench/1.0", "Accept": "application/json"}
    if payload is not None:
        base_headers["Content-Type"] = "application/json"
    base_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, method=method, headers=base_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Nervos Talk HTTP {exc.code}: {message[:500]}") from exc


def fetch_topic(topic_id: str) -> dict:
    """获取主题 JSON；帖子数超过首窗口时自动合并后续窗口，保证 posts 全量。"""
    topic = _request_json(f"{TALK_ORIGIN}/t/{topic_id}.json")
    posts = list(topic.get("post_stream", {}).get("posts", []))
    by_id = {p.get("id"): p for p in posts}
    try:
        total = int(topic.get("posts_count") or 0)
    except (TypeError, ValueError):
        total = 0
    if total > len(by_id):
        step = 15  # 窗口约 20 楼，步长 15 保证相邻窗口有重叠
        stale_rounds = 0
        number = total
        while number >= 1 and len(by_id) < total and stale_rounds < 2:
            try:
                window = _request_json(f"{TALK_ORIGIN}/t/{topic_id}/{number}.json")
            except Exception:
                break
            added = 0
            for post in window.get("post_stream", {}).get("posts", []):
                key = post.get("id")
                if key not in by_id:
                    by_id[key] = post
                    added += 1
            if added == 0:
                stale_rounds += 1
            else:
                stale_rounds = 0
            number -= step
        topic.setdefault("post_stream", {})["posts"] = sorted(
            by_id.values(), key=lambda p: p.get("post_number", 0))
    return topic


def fetch_topic_posts(topic_id: str) -> list[dict]:
    """获取主题的全部帖子（含所有回复），按楼层号排序。"""
    return fetch_topic(topic_id).get("post_stream", {}).get("posts", [])


def clean_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"</(?:p|li|h[1-6])>", "\n", value, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def normalized_posts(topic: dict, after_post_number: int = 0) -> list[dict]:
    result = []
    for post in topic.get("post_stream", {}).get("posts", []):
        number = int(post.get("post_number") or 0)
        if number <= after_post_number:
            continue
        result.append({
            "id": post.get("id"), "post_number": number, "username": post.get("username", ""),
            "created_at": post.get("created_at", ""), "updated_at": post.get("updated_at", ""),
            "text": clean_html(post.get("cooked", "")),
            "url": f"{TALK_ORIGIN}/t/{topic.get('slug','')}/{topic.get('id')}?u={post.get('username','')}#{number}",
        })
    return result


def _ai_endpoint() -> tuple[str, str, str]:
    if setting("SPARK_AI_API_KEY"):
        return ("https://api.siliconflow.cn/v1/chat/completions", setting("SPARK_AI_API_KEY"),
                setting("SPARK_AI_MODEL", "deepseek-ai/DeepSeek-V3"))
    if setting("SPARK_KIMI_API_KEY"):
        return ("https://api.moonshot.cn/v1/chat/completions", setting("SPARK_KIMI_API_KEY"),
                setting("SPARK_KIMI_MODEL", "moonshot-v1-8k"))
    raise RuntimeError("尚未在 .env 中配置 SPARK_AI_API_KEY 或 SPARK_KIMI_API_KEY")


def ai_json(prompt: str) -> dict:
    endpoint, key, model = _ai_endpoint()
    response = _request_json(endpoint, method="POST", payload={
        "model": model,
        "messages": [{"role": "system", "content": "Return one strict JSON object only."},
                     {"role": "user", "content": prompt}],
        "temperature": 0.1,
    }, headers={"Authorization": f"Bearer {key}"}, timeout=120)
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.S)
    if match:
        content = match.group(1)
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("AI 未返回可解析的 JSON")
    value = json.loads(content[start:end + 1])
    if not isinstance(value, dict):
        raise RuntimeError("AI 返回结果不是 JSON 对象")
    return value


def extract_new_project(topic: dict) -> dict:
    posts = normalized_posts(topic)
    prompt = f"""从下面 Nervos Talk 项目主题中提取 Notion 入库数据。不得猜测；无法确认的字段使用空字符串。
返回 JSON，字段必须为：topic_id, project_name, team, proposal_url, github_url, approval_date,
approval_rationale, acceptance_criteria, wallet, start_date, expected_completion, total_funding,
allocated_amount, distributed, initial_adjustments, transactions,
disbursements, refunds, adjustments。
transactions 是数组，每项字段 hash,type,date,amount,purpose；type 只能是“提款 / Withdrawal”或“存款 / Deposit”。
disbursements 是数组，只记录委员会金库拨给该项目的每笔款项（首笔拨款、里程碑款等），
每项字段 date,amount,percent,tx_hash,post_url,note：date=该笔款项公布楼层的发布日期（YYYY-MM-DD）；
amount 带币种原文（千分位）；percent 为该笔占总资助的百分比（如 "20%"，未知用空字符串）；
tx_hash 以 0x 开头；post_url 精确到楼层（含 ?u= 与 #楼层号）；note 简述款项用途。
refunds 与 disbursements 结构相同，只记录项目退回委员会金库的款项；没有则为空数组。
adjustments 是数组，记录项目延期获批、重大转向、范围/预算变更等重大调整事件，
每项字段 date,summary,post_url：date=获批/公布楼层日期（YYYY-MM-DD）；summary 一句话概述；
post_url 精确到楼层；没有则为空数组。

填写规则（必须严格遵守）：
1. start_date：申请人若在回复中明确说明了开始时间，用该日期；找不到明确说法时，用委员会发布
   第一笔拨款通知的回复日期；都没有则用空字符串。
2. expected_completion：取申请人在帖子中给出的预计完成日期，找不到用空字符串。
3. total_funding / allocated_amount：金额必须包含币种（如 "1,204,820 CKB"、"$10,000 USD"），
   数字用千分位；allocated_amount 取委员会批准分配的金额。
4. distributed：只填百分比（如 "20%"），不得填具体数额。
5. wallet：项目多签钱包地址（ckb1.../ckt1...），不得填委员会金库地址，转小写。
6. transactions 只记录与委员会主金库直接相关的链上交易：提款 = 从委员会主金库向外拨出；
   存款 = 其它钱包向委员会主金库存入。项目多签钱包与申请人之间的转账不要记录。
   date 用该交易在 Talk 上公布帖子的发布时间（帖子 JSON 里有 created_at）；
   amount 含币种（千分位）；purpose 简述资金去向（如 "To <项目名> Multisig Wallet"）。
日期格式 YYYY-MM-DD。项目名应去掉 Spark Program 前缀，只保留正式项目名。
topic_id={topic.get('id')}
proposal_url={TALK_ORIGIN}/t/{topic.get('slug','')}/{topic.get('id')}
标题：{topic.get('title','')}
帖子：{json.dumps(posts, ensure_ascii=False)[:50000]}"""
    value = ai_json(prompt)
    value["topic_id"] = str(topic.get("id"))
    value.setdefault("proposal_url", f"{TALK_ORIGIN}/t/{topic.get('slug','')}/{topic.get('id')}")
    if value.get("wallet"):
        value["wallet"] = value["wallet"].lower()
    value.setdefault("transactions", [])
    value.setdefault("disbursements", [])
    value.setdefault("refunds", [])
    value.setdefault("adjustments", [])
    return value


def extract_weekly_update(topic: dict, after_post_number: int) -> dict | None:
    posts = normalized_posts(topic, after_post_number)
    if not posts:
        return None
    last = max(int(post["post_number"]) for post in posts)
    prompt = f"""将以下项目新回复整理为一次 Notion 周报。忽略纯寒暄和委员会催更；不得猜测。
返回 JSON：date, fund_usage, progress, adjustments。progress 必须有内容；没有资金或调整信息时用空字符串。
保留关键链接、交易哈希、数字和明确日期。输出可直接阅读的中英原文摘要，不要输出 Markdown 标题。
帖子：{json.dumps(posts, ensure_ascii=False)[:45000]}"""
    value = ai_json(prompt)
    value.update({"update_key": f"posts-{after_post_number + 1}-{last}", "post_number": last,
                  "source_url": posts[-1]["url"]})
    return value


def extract_completion(topic: dict, base: dict, status: str) -> dict:
    posts = normalized_posts(topic)
    prompt = f"""从以下项目主题中提取结项或关停数据，不得猜测。
返回 JSON：completion_date, funding_amount, deliverables（URL 数组，最多2项）, final_evaluation,
completion_post_url。
funding_amount 必须包含币种（如 "1,204,820 CKB"、"$10,000 USD"），数字用千分位；
它是委员会实际拨给该项目的总额，与 total_funding 一致。
final_evaluation 应包括结果摘要、主要成果、未完成项或关停原因。日期 YYYY-MM-DD。
completion_post_url 必须是委员会发布的结项/关停公告所在楼层的精确链接
（含 ?u= 与 #楼层号），不要给主题首页链接；找不到用空字符串。
帖子：{json.dumps(posts, ensure_ascii=False)[:50000]}"""
    value = ai_json(prompt)
    if not value.get("completion_date"):
        # 兜底：结项/公告的最后一条回复日期即结项日期
        last_date = (posts[-1].get("created_at") or "")[:10] if posts else ""
        if last_date:
            value["completion_date"] = last_date
    # 结项公告正文必须忠实原文：按 URL 匹配回原始楼层，直接取该楼正文，不采用 AI 改写。
    value.setdefault("completion_post_url", "")
    by_url = {p["url"]: p for p in posts}
    post = by_url.get(value["completion_post_url"])
    if post is not None:
        value["completion_post_text"] = post.get("text", "")
    else:
        value.setdefault("completion_post_text", "")
        if not value["completion_post_url"]:
            # 兜底：找不到公告楼层时，以最后一楼作为结项公告来源
            if posts:
                value["completion_post_url"] = posts[-1]["url"]
                value["completion_post_text"] = posts[-1].get("text", "")
    value.update({k: base.get(k, "") for k in (
        "topic_id", "project_name", "team", "start_date", "wallet", "total_funding", "expected_completion")})
    if not value.get("funding_amount") and base.get("total_funding"):
        value["funding_amount"] = base["total_funding"]
    value["status"] = status
    return value


def post_as_user(topic_id: str, raw: str, forum_username: str) -> dict:
    """Post with a per-user Discourse User API key; never accepts or stores a forum password."""
    env_name = "SPARK_DISCOURSE_USER_API_KEY_" + re.sub(r"[^A-Z0-9]", "_", forum_username.upper())
    api_key = os.environ.get(env_name) or setting(env_name)
    if not api_key:
        raise RuntimeError(f"未配置 {env_name}")
    return _request_json(f"{TALK_ORIGIN}/posts.json", method="POST",
                         payload={"topic_id": int(topic_id), "raw": raw},
                         headers={"User-Api-Key": api_key})
