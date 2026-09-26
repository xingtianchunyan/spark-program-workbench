"""链上交易检索：CKB 地址（bech32m）编解码、JSON-RPC 访问与交易分类。

重要交易记录表只记 Spark 主钱包作为一方的交易：
- 主钱包划出 = 提款；主钱包收到 = 存款；
- 从项目多签发给提案团队的钱不进此表（项目多签的支出只进各项目页的
  「资金使用情况」内联库）。

项目多签钱包判定（两个条件缺一不可）：
1. 主钱包历史上有交易发送到过该地址（主钱包流出交易的对手方）；
2. Notion 项目列表（进行中/已完成两表）的行「钱包」属性或项目页内容中
   能检索到该地址。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any


MAIN_WALLET_ADDRESS = (
    "ckb1qrg6n7rh4mfltcruh8zjkcdtjmgx7fg2u6yre3ls5fprmvyhdlyzzqg7kpzlam02pxu0uvnaxymnj8g5cfmuadc5a4wf5"
)
DEFAULT_RPC_URL = "https://mainnet.ckb.dev/rpc"
RPC_RETRIES = 6
RPC_TIMEOUT = 30
INDEXER_PAGE_SIZE = 50  # get_transactions 单次分页条数（保守取值）

# 「资金使用情况」内联库列名（与 workflows.FUND_DB_SCHEMA 对齐）
FUND_DB_TITLE = "资金使用情况 / Fund Usage"
FUND_COL_DATE = "日期 / Date"
FUND_COL_TYPE = "类型 / Type"
FUND_COL_AMOUNT = "金额 / Amount"
FUND_COL_HASH = "交易哈希 / Transaction Hash"
FUND_COL_NOTES = "备注 / Notes"
FUND_TYPE_WITHDRAWAL = "提款 Withdrawal"
FUND_TYPE_DEPOSIT = "存款 Deposit"

ADDRESS_RE = re.compile(r"\b(?:ckb1|ckt1)[02-9ac-hj-np-z02-9ac-hj-np-z1l]{20,}\b")
BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))


class ChainError(RuntimeError):
    """链上数据访问/解析失败。"""


# ---------------------------------------------------------------------------
# bech32m 编解码（BIP-350，constant 0x2bc830a3）
# ---------------------------------------------------------------------------

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32M_CONST = 0x2BC830A3
_BECH32_GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)


def _polymod(values: list[int]) -> int:
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i in range(5):
            if (top >> i) & 1:
                chk ^= _BECH32_GEN[i]
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(ch) >> 5 for ch in hrp] + [0] + [ord(ch) & 31 for ch in hrp]


def _create_checksum(hrp: str, data: list[int], const: int = _BECH32M_CONST) -> list[int]:
    values = _hrp_expand(hrp) + data
    polymod = _polymod(values + [0, 0, 0, 0, 0, 0]) ^ const
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _verify_checksum(hrp: str, data: list[int]) -> int | None:
    """同时接受 bech32m（0x2bc830a3）与旧版 bech32（1）校验和，返回命中的常量。"""
    value = _polymod(_hrp_expand(hrp) + data)
    if value == _BECH32M_CONST:
        return _BECH32M_CONST
    if value == 1:
        return 1
    return None


def convertbits(data: Any, frombits: int, tobits: int, pad: bool = True) -> list[int]:
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or value >> frombits:
            raise ChainError("convertbits: 数值超出位宽")
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise ChainError("convertbits: 非零填充位")
    return ret


def bech32_encode(hrp: str, payload: bytes, const: int = _BECH32M_CONST) -> str:
    data = convertbits(payload, 8, 5)
    combined = data + _create_checksum(hrp, data, const)
    return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in combined)


def bech32_decode(value: str) -> tuple[str, bytes]:
    if not isinstance(value, str) or not value:
        raise ChainError("空地址")
    if value.lower() != value and value.upper() != value:
        raise ChainError("bech32m 地址大小写混用")
    value = value.lower()
    pos = value.rfind("1")
    if pos < 1 or pos + 7 > len(value) or len(value) > 200:
        raise ChainError(f"无效的 bech32m 地址: {value[:24]}…")
    hrp = value[:pos]
    data = []
    for ch in value[pos + 1:]:
        if ch not in _BECH32_CHARSET:
            raise ChainError(f"bech32m 地址含非法字符: {ch!r}")
        data.append(_BECH32_CHARSET.index(ch))
    if _verify_checksum(hrp, data) is None:
        raise ChainError("bech32m 校验和错误")
    return hrp, bytes(convertbits(data[:-6], 5, 8, pad=False))


# RFC-0021 常用 code_hash 表（短地址 code_hash_index → lock script）
SHORT_CODE_HASHES = {
    0x00: ("0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8", "type"),
    0x01: ("0x5c5069eb0857efc65e1bca0c07df34c31663b3622fd3876c876320fc9634e2a8", "type"),
}
_SHORT_CODE_HASH_NAMES = SHORT_CODE_HASHES  # 内部别名
_HASH_TYPE_CODES = {"data": 0, "type": 1, "data1": 2, "data2": 3}
_HASH_TYPE_NAMES = {0: "data", 1: "type", 2: "data1", 3: "data2"}


def encode_address(code_hash: str, hash_type: str | int, args: str,
                   hrp: str = "ckb") -> str:
    """CKB 全地址：data = 0x00 | code_hash(32B) | hash_type(1B) | args（bech32m）。"""
    code = bytes.fromhex(code_hash.removeprefix("0x"))
    arg_bytes = bytes.fromhex(args.removeprefix("0x"))
    if len(code) != 32:
        raise ChainError("code_hash 必须是 32 字节")
    if isinstance(hash_type, str):
        if hash_type not in _HASH_TYPE_CODES:
            raise ChainError(f"未知 hash_type: {hash_type}")
        hash_type = _HASH_TYPE_CODES[hash_type]
    payload = b"\x00" + code + bytes([int(hash_type)]) + arg_bytes
    return bech32_encode(hrp, payload)


def decode_address(address: str) -> dict[str, Any]:
    """CKB 地址 → {code_hash, hash_type, args}（均为 0x 十六进制字符串）。

    支持全地址（0x00|code_hash|hash_type|args，bech32m）、旧版短地址
    （0x01|code_hash_index|args，bech32）与旧版全地址（0x02/0x04，bech32）。
    """
    hrp, payload = bech32_decode(address)
    if hrp not in {"ckb", "ckt"}:
        raise ChainError(f"未知地址前缀 hrp: {hrp}")
    if len(payload) < 2:
        raise ChainError("地址负载过短")
    format_type = payload[0]
    if format_type == 0x00:
        if len(payload) < 34:
            raise ChainError("全地址负载过短")
        code_hash = "0x" + payload[1:33].hex()
        hash_type = payload[33]
        args = "0x" + payload[34:].hex()
    elif format_type == 0x01:
        index = payload[1]
        if index not in _SHORT_CODE_HASH_NAMES:
            raise ChainError(f"未知 code_hash_index: 0x{index:02x}")
        code_hash, hash_type = _SHORT_CODE_HASH_NAMES[index]
        args = "0x" + payload[2:].hex()
    elif format_type in (0x02, 0x04):
        if len(payload) < 34:
            raise ChainError("旧版全地址负载过短")
        code_hash = "0x" + payload[1:33].hex()
        hash_type = "data" if format_type == 0x02 else "type"
        args = "0x" + payload[33:].hex()
    else:
        raise ChainError(f"未知地址格式类型: 0x{format_type:02x}")
    return {"code_hash": code_hash, "hash_type": hash_type, "args": args}


# ---------------------------------------------------------------------------
# JSON-RPC（6 次重试，指数退避）
# ---------------------------------------------------------------------------

class ChainRPC:
    def __init__(self, url: str = DEFAULT_RPC_URL, retries: int = RPC_RETRIES,
                 timeout: int = RPC_TIMEOUT):
        self.url = url
        self.retries = retries
        self.timeout = timeout
        self._id = 0

    def rpc(self, method: str, params: list[Any]) -> Any:
        """JSON-RPC 2.0 调用，网络间歇断连时每请求重试 retries 次。"""
        body = json.dumps({"id": self._next_id(), "jsonrpc": "2.0",
                           "method": method, "params": params}).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.retries):
            request = urllib.request.Request(self.url, data=body, method="POST",
                                             headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("error"):
                    raise ChainError(f"RPC {method} 返回错误: {payload['error']}")
                return payload.get("result")
            except (urllib.error.URLError, TimeoutError, ConnectionError, ChainError,
                    json.JSONDecodeError, OSError) as exc:
                last_error = exc
                if isinstance(exc, ChainError) and not str(exc).startswith("RPC"):
                    raise
                if attempt + 1 < self.retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
        raise ChainError(f"RPC {method} 连续 {self.retries} 次失败: {last_error}")

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def get_transactions(self, lock: dict[str, Any]) -> list[dict]:
        """indexer get_transactions 全量分页（group_by_transaction，after 游标）。"""
        script = {"code_hash": lock["code_hash"], "hash_type": _hash_type_name(lock.get("hash_type", 1)),
                  "args": lock["args"]}
        results: list[dict] = []
        after: str | None = None
        while True:
            params = [{"script": script, "script_type": "lock", "order": "desc",
                       "limit": str(INDEXER_PAGE_SIZE), "group_by_transaction": True}]
            if after:
                params[0]["after"] = after
            page = self.rpc("get_transactions", params) or {}
            objects = page.get("objects") or []
            results.extend(objects)
            cursor = page.get("last_cursor")
            if not objects or not cursor or cursor == after:
                return results
            after = cursor

    def get_transaction(self, tx_hash: str) -> dict:
        result = self.rpc("get_transaction", [tx_hash])
        if not result or not result.get("transaction"):
            raise ChainError(f"链上找不到交易 {tx_hash}")
        return result

    def get_block_timestamp(self, block_hash: str) -> int | None:
        block = self.rpc("get_block", [block_hash])
        header = (block or {}).get("header") or {}
        raw = header.get("timestamp")
        if raw is None:
            return None
        return int(str(raw), 16) if str(raw).startswith("0x") else int(raw)

    def resolve_input(self, cell_input: dict) -> dict | None:
        """回溯 input 引用的上一笔交易输出，返回 {lock, capacity}；失败返回 None。"""
        try:
            previous = (cell_input or {}).get("previous_output") or {}
            tx_hash, index = previous.get("tx_hash"), previous.get("index")
            if not tx_hash or index is None:
                return None
            detail = self.get_transaction(tx_hash)["transaction"]
            outputs = detail.get("outputs") or []
            index = int(str(index), 16) if str(index).startswith("0x") else int(index)
            if index >= len(outputs):
                return None
            data = (detail.get("outputs_data") or [""] * len(outputs))
            capacity = (outputs[index] or {}).get("capacity", data[index])
            return {"lock": (outputs[index] or {}).get("lock"), "capacity": capacity}
        except Exception:
            return None


def _hash_type_name(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _HASH_TYPE_NAMES.get(int(value), "type")


def _hex_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.startswith("0x") else int(text)


def _capacity_ckb(capacity: Any) -> float:
    return _hex_int(capacity) / 100_000_000.0  # 1 CKB = 10^8 shannon


def _norm_hex(value: Any) -> str:
    if not value:
        return ""
    text = str(value).lower()
    return text[2:] if text.startswith("0x") else text


def lock_matches(lock: dict | None, target: dict) -> bool:
    """lock 是否属于 target 钱包：args 必须相等，code_hash/hash_type 双方都有时须一致。"""
    if not lock:
        return False
    if _norm_hex(lock.get("args")) != _norm_hex(target.get("args")):
        return False
    if target.get("code_hash") and lock.get("code_hash"):
        if _norm_hex(lock.get("code_hash")) != _norm_hex(target.get("code_hash")):
            return False
    if target.get("hash_type") is not None and lock.get("hash_type") is not None:
        try:
            if _hash_type_name(lock.get("hash_type")) != _hash_type_name(target.get("hash_type")):
                return False
        except Exception:
            return False
    return True


def lock_to_address(lock: dict | None) -> str | None:
    """lock → CKB 全地址；缺少必要字段时返回 None。"""
    if not lock or not lock.get("code_hash") or lock.get("hash_type") is None:
        return None
    try:
        return encode_address(lock["code_hash"], lock.get("hash_type"), lock.get("args") or "0x")
    except Exception:
        return None


def classify_main_wallet_txs(txs: list[dict], main_args: Any,
                             rpc: ChainRPC | None = None) -> list[dict]:
    """把主钱包相关交易分类为 提款(out)/存款(in) 记录。

    每个 tx 的输入结构（由链上扫描侧组装）：
      tx_hash, timestamp(秒, 可选), outputs: [{lock, capacity}],
      main_inputs: [主钱包 input 下标], main_outputs: [主钱包 output 下标],
      inputs: [{lock, capacity}]（可选，用于 in 方向对手方判定）
    返回记录: {date(UTC ISO), direction, amount_ckb, counterparty_lock,
               counterparty_address, tx_hash, is_change}
    """
    if isinstance(main_args, dict):
        main_lock = dict(main_args)
    else:
        main_lock = {"code_hash": None, "hash_type": None, "args": main_args}
    records: list[dict] = []
    for tx in txs or []:
        outputs = tx.get("outputs") or []
        main_inputs = set(tx.get("main_inputs") or [])
        main_outputs = set(tx.get("main_outputs") or [])
        timestamp = tx.get("timestamp")

        def utc_iso() -> str | None:
            if timestamp is None:
                return None
            return dt.datetime.fromtimestamp(int(timestamp), dt.timezone.utc).isoformat()

        paid_out = [(i, cell) for i, cell in enumerate(outputs)
                    if i not in main_outputs and (cell or {}).get("lock")]
        received = [(i, cell) for i, cell in enumerate(outputs)
                    if i in main_outputs]
        if main_inputs:
            # 主钱包在 inputs：向非主钱包地址付款 = 提款；找零回主钱包的输出忽略
            if not paid_out:
                continue  # 纯自我整理交易，不进重要交易记录表
            amount = sum(_capacity_ckb(cell.get("capacity")) for _, cell in paid_out)
            counter_lock = paid_out[0][1].get("lock")
            records.append({
                "date": utc_iso(), "direction": "out", "amount_ckb": amount,
                "counterparty_lock": counter_lock,
                "counterparty_address": lock_to_address(counter_lock),
                "tx_hash": tx.get("tx_hash") or "", "is_change": bool(received),
            })
        elif main_outputs:
            # 主钱包不在 inputs 但在 outputs：主钱包收到款 = 存款
            amount = sum(_capacity_ckb(cell.get("capacity")) for _, cell in received)
            counter_lock, counter_address = _resolve_counterparty(tx, main_lock, rpc)
            records.append({
                "date": utc_iso(), "direction": "in", "amount_ckb": amount,
                "counterparty_lock": counter_lock, "counterparty_address": counter_address,
                "tx_hash": tx.get("tx_hash") or "", "is_change": False,
            })
    return records


def _resolve_counterparty(tx: dict, main_lock: dict, rpc: ChainRPC | None):
    """in 方向对手方 = 第一个已解析的非主钱包 input lock；缺失时按需回溯链上数据。"""
    for cell in tx.get("inputs") or []:
        lock = (cell or {}).get("lock")
        if lock and not lock_matches(lock, main_lock):
            return lock, lock_to_address(lock)
    if rpc is not None:
        for cell_input in (tx.get("raw_inputs") or [])[:20]:
            resolved = rpc.resolve_input(cell_input)
            lock = (resolved or {}).get("lock")
            if lock and not lock_matches(lock, main_lock):
                return lock, lock_to_address(lock)
    return None, None


# ---------------------------------------------------------------------------
# 金额 / 日期 / 用途格式化
# ---------------------------------------------------------------------------

def format_ckb(amount: float) -> str:
    """金额格式化为 "N,NNN,NNN CKB"（整数）或带小数的千分位格式。"""
    value = float(amount or 0)
    if value == int(value):
        return f"{int(value):,} CKB"
    return f"{value:,.8f}".rstrip("0").rstrip(".") + " CKB"


def beijing_date(value: Any) -> str:
    """UTC ISO 时间或秒时间戳 → 北京时区日期 YYYY-MM-DD。"""
    if value is None:
        return ""
    if isinstance(value, (int, float)) or str(value).isdigit():
        moment = dt.datetime.fromtimestamp(int(value), dt.timezone.utc)
    else:
        text = str(value).replace("Z", "+00:00")
        moment = dt.datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(BEIJING_TZ).date().isoformat()


def tx_purpose(record: dict, known_projects: dict[str, str]) -> str:
    """用途：对手方是已知项目多签 → "To/From <项目名>"，否则用地址前 20 字符。"""
    verb = "To" if record.get("direction") == "out" else "From"
    address = record.get("counterparty_address") or ""
    if address and address in known_projects:
        return f"{verb} {known_projects[address]}"
    if address:
        return f"{verb} {address[:20]}…"
    return f"{verb} 未知地址"


# ---------------------------------------------------------------------------
# 链上扫描（组装 classify 需要的 tx 结构）
# ---------------------------------------------------------------------------

def fetch_main_wallet_records(rpc: ChainRPC, main_address: str = MAIN_WALLET_ADDRESS,
                              limit: int = 500) -> list[dict]:
    """拉主钱包全量交易 → classify 记录（最多 limit 条，按时间倒序）。"""
    main_lock = decode_address(main_address)
    grouped = rpc.get_transactions(main_lock)
    txs: list[dict] = []
    for item in grouped[:max(1, int(limit))]:
        tx_hash = item.get("tx_hash")
        if not tx_hash:
            continue
        detail = rpc.get_transaction(tx_hash)
        transaction = detail.get("transaction") or {}
        cells = item.get("cells") or []
        if not cells and "io_type" in item:
            cells = [[item.get("io_type"), item.get("io_index")]]
        main_inputs = [int(i) for kind, i in cells if int(kind) == 0 for i in [int(i)]]
        main_outputs = [int(i) for kind, i in cells if int(kind) == 1 for i in [int(i)]]
        timestamp = None
        block_hash = (detail.get("tx_status") or {}).get("block_hash")
        if block_hash:
            timestamp = rpc.get_block_timestamp(block_hash)
        txs.append({
            "tx_hash": tx_hash, "timestamp": timestamp,
            "outputs": transaction.get("outputs") or [],
            "raw_inputs": transaction.get("inputs") or [],
            "main_inputs": main_inputs, "main_outputs": main_outputs,
        })
    return classify_main_wallet_txs(txs, main_lock, rpc=rpc)


def fetch_multisig_records(rpc: ChainRPC, address: str) -> list[dict]:
    """拉某个多签钱包的交易，只保留「划出到非主钱包地址」的提款记录。"""
    lock = decode_address(address)
    main_lock = decode_address(MAIN_WALLET_ADDRESS)
    grouped = rpc.get_transactions(lock)
    records: list[dict] = []
    for item in grouped:
        tx_hash = item.get("tx_hash")
        if not tx_hash:
            continue
        detail = rpc.get_transaction(tx_hash)
        transaction = detail.get("transaction") or {}
        cells = item.get("cells") or []
        if not cells and "io_type" in item:
            cells = [[item.get("io_type"), item.get("io_index")]]
        own_inputs = {int(i) for kind, i in cells if int(kind) == 0}
        own_outputs = {int(i) for kind, i in cells if int(kind) == 1}
        if not own_inputs:
            continue  # 只关心多签作为付款方的交易
        outputs = transaction.get("outputs") or []
        paid = [(i, cell) for i, cell in enumerate(outputs)
                if i not in own_outputs and (cell or {}).get("lock")
                and not lock_matches(cell.get("lock"), lock)]
        if not paid:
            continue
        amount = sum(_capacity_ckb(cell.get("capacity")) for _, cell in paid)
        timestamp = None
        block_hash = (detail.get("tx_status") or {}).get("block_hash")
        if block_hash:
            timestamp = rpc.get_block_timestamp(block_hash)
        counter_lock = paid[0][1].get("lock")
        records.append({
            "date": (dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat()
                     if timestamp is not None else None),
            "direction": "out", "amount_ckb": amount,
            "counterparty_lock": counter_lock,
            "counterparty_address": lock_to_address(counter_lock),
            "tx_hash": tx_hash, "is_change": bool(own_outputs),
            "to_main": lock_matches(counter_lock, main_lock),
        })
    return records


# ---------------------------------------------------------------------------
# Notion 读取与比对（notion 参数为 duck-typed 的 NotionClient）
# ---------------------------------------------------------------------------

def _prop(config: dict, source: str, key: str) -> str:
    return config["properties"][source][key]


def _plain(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item.get("plain_text") or (item.get("text") or {}).get("content") or "")
                       for item in value if isinstance(item, dict))
    return str(value or "")


def _page_property(page: dict, name: str) -> Any:
    """不依赖 notion.property_value 的属性读取（兼容 FakeNotion 的归一化结构）。"""
    from .notion import property_value
    try:
        return property_value(page, name)
    except Exception:
        prop = (page.get("properties") or {}).get(name) or {}
        kind = prop.get("type")
        return prop.get(kind) if kind else None


def read_transactions_rows(notion: Any, config: dict) -> dict[str, dict]:
    """transactions 表已有行，按交易哈希索引（含日期/类型/金额摘要）。"""
    title = _prop(config, "transactions", "title")
    type_key = _prop(config, "transactions", "type")
    date_key = _prop(config, "transactions", "date")
    amount_key = _prop(config, "transactions", "amount")
    rows: dict[str, dict] = {}
    for row in notion.query(config["data_sources"]["transactions"]):
        tx_hash = str(_page_property(row, title) or "").strip()
        if tx_hash:
            rows[tx_hash] = {
                "id": row.get("id"), "tx_hash": tx_hash,
                "type": _page_property(row, type_key) or "",
                "date": _page_property(row, date_key) or "",
                "amount": _page_property(row, amount_key) or "",
            }
    return rows


def _block_text(block: dict) -> str:
    body = block.get(block.get("type") or "", {}) or {}
    if not isinstance(body, dict):
        return ""
    return "".join(str(item.get("plain_text") or (item.get("text") or {}).get("content") or "")
                   for item in body.get("rich_text") or [])


def read_project_wallets(notion: Any, config: dict) -> dict[str, str]:
    """Notion 项目列表（进行中+已完成）中的多签地址 → 项目名。

    地址来源：行「钱包」属性；属性为空时退而检索项目页内容块文本。"""
    found: dict[str, str] = {}
    for source in ("ongoing_projects", "completed_projects"):
        wallet_key = _prop(config, source, "wallet")
        title_key = _prop(config, source, "title")
        for row in notion.query(config["data_sources"][source]):
            project = str(_page_property(row, title_key) or "").strip() or row.get("id", "")
            addresses = set(ADDRESS_RE.findall(str(_page_property(row, wallet_key) or "")))
            if not addresses and row.get("id"):
                try:
                    for block in notion.list_children(row["id"], recursive=True):
                        addresses.update(ADDRESS_RE.findall(_block_text(block)))
                except Exception:
                    pass
            for address in addresses:
                found.setdefault(address, project)
    return found


# ---------------------------------------------------------------------------
# 主钱包：scan / apply
# ---------------------------------------------------------------------------

def scan_main(notion: Any, config: dict, rpc: ChainRPC | None = None,
              records: list[dict] | None = None, limit: int = 500) -> dict:
    """对照链上与 transactions 表：missing / mismatched / extra（不落库）。"""
    if records is None:
        if rpc is None:
            raise ChainError("scan_main 需要 rpc 或 records 之一")
        records = fetch_main_wallet_records(rpc, limit=limit)
    existing = read_transactions_rows(notion, config)
    known_projects = read_project_wallets(notion, config)

    from .notion import values_match
    withdrawal = config["select_values"]["withdrawal"]
    deposit = config["select_values"]["deposit"]
    missing, mismatched = [], []
    seen = set()
    for record in records:
        tx_hash = record.get("tx_hash") or ""
        if not tx_hash or tx_hash in seen:
            continue
        seen.add(tx_hash)
        row = {
            "tx_hash": tx_hash, "date": beijing_date(record.get("date")),
            "type": withdrawal if record.get("direction") == "out" else deposit,
            "amount": format_ckb(record.get("amount_ckb")),
            "purpose": tx_purpose(record, known_projects),
            "direction": record.get("direction"),
        }
        current = existing.get(tx_hash)
        if current is None:
            missing.append(row)
        else:
            diffs = {name: {"expected": row[name], "actual": current.get(name)}
                     for name in ("date", "type", "amount")
                     if not values_match(row[name], current.get(name) or "")}
            if diffs:
                row["id"] = current["id"]
                row["diffs"] = diffs
                mismatched.append(row)
    extra = [row for tx_hash, row in existing.items() if tx_hash not in seen]
    return {"missing": missing, "mismatched": mismatched, "extra": extra,
            "scanned": len(seen), "known_multisig": known_projects}


def _tx_properties(config: dict, row: dict) -> dict:
    from .notion import prop_date, prop_select, prop_text, prop_title
    tp = config["properties"]["transactions"]
    return {
        tp["title"]: prop_title(row["tx_hash"]),
        tp["type"]: prop_select(row["type"]),
        tp["date"]: prop_date(row["date"]),
        tp["amount"]: prop_text(row["amount"]),
        tp["purpose"]: prop_text(row["purpose"]),
    }


def apply_main(notion: Any, config: dict, records: list[dict],
               only: list[str] | None = None) -> dict:
    """把 missing/mismatched 写入 transactions 表并回读校验；绝不删除 extra 行。"""
    from .notion import values_match
    existing = read_transactions_rows(notion, config)
    known_projects = read_project_wallets(notion, config)
    withdrawal = config["select_values"]["withdrawal"]
    wanted = {str(x) for x in (only or [])}
    created, updated, skipped, errors = [], [], [], []
    for record in records or []:
        tx_hash = record.get("tx_hash") or ""
        if not tx_hash or (wanted and tx_hash not in wanted):
            continue
        row = {
            "tx_hash": tx_hash, "date": beijing_date(record.get("date")),
            "type": withdrawal if record.get("direction") == "out" else deposit_of(config),
            "amount": format_ckb(record.get("amount_ckb")),
            "purpose": tx_purpose(record, known_projects),
        }
        current = existing.get(tx_hash)
        properties = _tx_properties(config, row)
        try:
            if current is None:
                page = notion.create_page(config["data_sources"]["transactions"], properties)
                _verify_row(notion, page.get("id"), properties)
                created.append(tx_hash)
            else:
                stale = {name for name in ("date", "type", "amount")
                         if not values_match(row[name], current.get(name) or "")}
                if not stale:
                    skipped.append(tx_hash)
                    continue
                update_props = {key: value for key, value in properties.items()
                                if key != _prop(config, "transactions", "title")}
                page = notion.update_page(current["id"], update_props)
                _verify_row(notion, current["id"], update_props)
                updated.append(tx_hash)
        except Exception as exc:
            errors.append({"tx_hash": tx_hash, "error": str(exc)})
    return {"created": created, "updated": updated, "skipped": skipped,
            "errors": errors, "deleted": []}


def deposit_of(config: dict) -> str:
    return config["select_values"]["deposit"]


def _property_plain(prop_value: Any) -> str:
    """prop_* 构造值 → 纯文本，供 values_match 比对。"""
    if not isinstance(prop_value, dict):
        return str(prop_value or "")
    kind = next(iter(prop_value), None)
    body = prop_value.get(kind)
    if kind in {"title", "rich_text"}:
        return "".join((item.get("text") or {}).get("content") or "" for item in body or [])
    if kind == "select":
        return (body or {}).get("name") or ""
    if kind == "date":
        return (body or {}).get("start") or ""
    if kind == "url":
        return body or ""
    return str(body or "")


def _plain_items_text(items: Any) -> str:
    return "".join(str(item.get("plain_text") or (item.get("text") or {}).get("content") or "")
                   for item in items or [] if isinstance(item, dict))


def _verify_row(notion: Any, page_id: str | None, properties: dict) -> None:
    if not page_id:
        raise ChainError("写入后未返回页面 id")
    from .notion import values_match
    page = notion.retrieve_page(page_id)
    conflicts = {}
    for name, wanted in properties.items():
        actual = _page_property(page, name)
        if isinstance(actual, list):  # title/rich_text 原样结构
            actual = _plain_items_text(actual)
        if not values_match(_property_plain(wanted), actual):
            conflicts[name] = actual
    if conflicts:
        raise ChainError("写入后回读校验失败: " + json.dumps(conflicts, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 项目多签：scan / apply
# ---------------------------------------------------------------------------

def _inline_fund_source(notion: Any, page_id: str) -> str | None:
    for block in notion.list_children(page_id, recursive=False):
        if block.get("type") != "child_database":
            continue
        if (block.get("child_database") or {}).get("title") != FUND_DB_TITLE:
            continue
        try:
            return notion.database_data_source_id(block["id"])
        except Exception:
            return None
    return None


def _multisig_pages(notion: Any, config: dict) -> dict[str, dict]:
    """多签地址 → {project, page_id, source}（source 为资金使用情况内联库 id）。"""
    pages: dict[str, dict] = {}
    for source in ("ongoing_projects", "completed_projects"):
        wallet_key = _prop(config, source, "wallet")
        title_key = _prop(config, source, "title")
        for row in notion.query(config["data_sources"][source]):
            addresses = set(ADDRESS_RE.findall(str(_page_property(row, wallet_key) or "")))
            if not addresses:
                continue
            fund_source = _inline_fund_source(notion, row["id"])
            for address in addresses:
                pages.setdefault(address, {
                    "project": str(_page_property(row, title_key) or "").strip() or row["id"],
                    "page_id": row["id"], "source": fund_source,
                })
    return pages


def scan_multisig(notion: Any, config: dict, rpc: ChainRPC,
                  main_records: list[dict] | None = None) -> list[dict]:
    """对每个已知项目多签拉交易，只保留划出到非主钱包地址的提款，
    按 tx_hash 对照各项目页「资金使用情况」内联库去重（不落库）。"""
    if main_records is None:
        main_records = fetch_main_wallet_records(rpc)
    # 条件①：主钱包历史流出交易的对手方；条件②：Notion 项目中出现的地址
    main_counterparties = {r.get("counterparty_address") for r in main_records
                           if r.get("direction") == "out" and r.get("counterparty_address")}
    pages = _multisig_pages(notion, config)
    results = []
    for address, info in sorted(pages.items()):
        if address not in main_counterparties:
            continue  # 两个条件缺一不可
        existing_hashes = set()
        if info["source"]:
            for row in notion.query(info["source"]):
                tx_hash = str(_page_property(row, FUND_COL_HASH) or "").strip()
                if tx_hash:
                    existing_hashes.add(tx_hash)
        records = fetch_multisig_records(rpc, address)
        rows = []
        for record in records:
            if record.get("to_main"):
                continue  # 退回主钱包的钱由主钱包侧记录，不进项目页提款
            if record["tx_hash"] in existing_hashes:
                continue
            rows.append({
                "tx_hash": record["tx_hash"],
                "date": beijing_date(record.get("date")),
                "amount": format_ckb(record.get("amount_ckb")),
                "type": FUND_TYPE_WITHDRAWAL,
                "counterparty_address": record.get("counterparty_address") or "",
            })
        results.append({"multisig": address, "project": info["project"],
                        "page_id": info["page_id"], "source": info["source"],
                        "rows": rows, "existing": len(existing_hashes)})
    return results


def apply_multisig(notion: Any, config: dict, rpc: ChainRPC,
                   results: list[dict] | None = None) -> dict:
    """把项目页「资金使用情况」内联库缺失的行写入（列名按 FUND_DB_SCHEMA）。"""
    if results is None:
        results = scan_multisig(notion, config, rpc)
    from .notion import prop_date, prop_select, prop_text, prop_url
    written, errors = [], []
    for item in results or []:
        source = item.get("source")
        if not source:
            errors.append({"multisig": item.get("multisig"),
                           "error": "项目页缺少「资金使用情况」内联库"})
            continue
        existing_hashes = {str(_page_property(row, FUND_COL_HASH) or "").strip()
                           for row in notion.query(source)}
        for row in item.get("rows") or []:
            tx_hash = row.get("tx_hash") or ""
            if not tx_hash or tx_hash in existing_hashes:
                continue
            properties = {
                FUND_COL_DATE: prop_date(row.get("date") or ""),
                FUND_COL_TYPE: prop_select(row.get("type") or FUND_TYPE_WITHDRAWAL),
                FUND_COL_AMOUNT: prop_text(row.get("amount") or ""),
                FUND_COL_HASH: prop_url(tx_hash),
                FUND_COL_NOTES: prop_text("链上检索自动登记 / Fetched from chain"),
            }
            try:
                page = notion.create_page(source, properties)
                _verify_row(notion, page.get("id"), properties)
                existing_hashes.add(tx_hash)
                written.append({"multisig": item.get("multisig"), "project": item.get("project"),
                                "tx_hash": tx_hash, "id": page.get("id")})
            except Exception as exc:
                errors.append({"multisig": item.get("multisig"), "tx_hash": tx_hash,
                               "error": str(exc)})
    return {"written": written, "errors": errors}
