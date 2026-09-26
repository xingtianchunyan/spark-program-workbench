import datetime as dt
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from spark_web import chain
from spark_web.config import load_workspace_config
from spark_web.notion import prop_text, prop_title, property_value
from spark_web.storage import Store

from test_workflows import FakeNotion


MAIN_LOCK = {"code_hash": "0x" + "11" * 32, "hash_type": 1, "args": "0x" + "aa" * 20}
OTHER_LOCK = {"code_hash": "0x" + "11" * 32, "hash_type": 1, "args": "0x" + "bb" * 20}
SENDER_LOCK = {"code_hash": "0x" + "11" * 32, "hash_type": 1, "args": "0x" + "cc" * 20}
OTHER_ADDRESS = chain.lock_to_address(OTHER_LOCK)
SENDER_ADDRESS = chain.lock_to_address(SENDER_LOCK)
TS = int(dt.datetime(2026, 1, 2, 20, 30, tzinfo=dt.timezone.utc).timestamp())  # 2026-01-03 北京
ISO = "2026-01-02T20:30:00+00:00"


def shannon(ckb):
    return hex(int(ckb * 100_000_000))


class Bech32mTests(unittest.TestCase):
    def test_main_wallet_full_address_roundtrip(self):
        decoded = chain.decode_address(chain.MAIN_WALLET_ADDRESS)
        self.assertEqual(decoded["code_hash"],
                         "0xd1a9f877aed3f5e07cb9c52b61ab96d06f250ae6883cc7f0a2423db0976fc821")
        self.assertEqual(decoded["hash_type"], 1)
        self.assertEqual(decoded["args"], "0x1eb045feedea09b8fe327d3137391d14c277ceb7")
        self.assertEqual(chain.encode_address(decoded["code_hash"], decoded["hash_type"],
                                              decoded["args"]),
                         chain.MAIN_WALLET_ADDRESS)

    def test_short_address_decode_and_full_form_roundtrip(self):
        decoded = chain.decode_address("ckb1qyqww4pllswty5l0e5676hyjs725qzga28zs0t3krk")
        self.assertEqual(decoded["code_hash"],
                         "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8")
        self.assertEqual(decoded["hash_type"], "type")
        self.assertEqual(decoded["args"], "0xe7543ffc1cb253efcd35ed5c92879540091d51c5")
        full = chain.encode_address(decoded["code_hash"], decoded["hash_type"], decoded["args"])
        self.assertEqual(chain.decode_address(full)["args"], decoded["args"])
        self.assertEqual(chain.decode_address(full)["code_hash"], decoded["code_hash"])

    def test_invalid_address_rejected(self):
        with self.assertRaises(chain.ChainError):
            chain.decode_address("ckb1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq")
        with self.assertRaises(chain.ChainError):
            chain.decode_address("")


class ChainRPCTests(unittest.TestCase):
    """多端点故障转移与 indexer 字段归一化（用假 urlopen 注入，不走真实网络）。"""

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def _patch_urlopen(self, handler):
        original = urllib.request.urlopen
        urllib.request.urlopen = handler
        self.addCleanup(setattr, urllib.request, "urlopen", original)

    def test_403_fails_over_to_next_endpoint_without_retry(self):
        calls = []

        def handler(request, timeout=0):
            endpoint = request.full_url
            calls.append(endpoint)
            if endpoint == "https://a.example/rpc":
                raise urllib.error.HTTPError(endpoint, 403, "Forbidden", None, None)
            return ChainRPCTests.FakeResponse(
                {"id": 1, "jsonrpc": "2.0", "result": "0x10"})

        self._patch_urlopen(handler)
        rpc = chain.ChainRPC(urls=["https://a.example/rpc", "https://b.example/rpc"])
        self.assertEqual(rpc.rpc("get_tip_block_number", []), "0x10")
        self.assertEqual(calls.count("https://a.example/rpc"), 1)  # 403 不重试
        self.assertEqual(calls.count("https://b.example/rpc"), 1)

    def test_all_endpoints_fail_error_carries_each_status(self):
        def handler(request, timeout=0):
            endpoint = request.full_url
            raise urllib.error.HTTPError(endpoint, 403, "Forbidden", None, None)

        self._patch_urlopen(handler)
        rpc = chain.ChainRPC(urls=["https://a.example/rpc", "https://b.example/rpc"])
        with self.assertRaises(chain.ChainError) as ctx:
            rpc.rpc("get_transactions", [])
        message = str(ctx.exception)
        self.assertIn("https://a.example/rpc -> HTTP 403", message)
        self.assertIn("https://b.example/rpc -> HTTP 403", message)

    def test_io_kind_and_index_accept_modern_and_legacy_formats(self):
        self.assertEqual(chain._io_kind("input"), 0)
        self.assertEqual(chain._io_kind("output"), 1)
        self.assertEqual(chain._io_kind(0), 0)
        self.assertEqual(chain._io_kind(1), 1)
        self.assertEqual(chain._io_kind("0x1"), 1)
        self.assertEqual(chain._io_index("0x2"), 2)
        self.assertEqual(chain._io_index(3), 3)


class ClassifyTests(unittest.TestCase):
    def test_out_direction_with_change_ignored(self):
        txs = [{
            "tx_hash": "0xout1", "timestamp": TS,
            "main_inputs": [0], "main_outputs": [2],
            "outputs": [
                {"lock": OTHER_LOCK, "capacity": shannon(100)},
                {"lock": OTHER_LOCK, "capacity": shannon(23.5)},
                {"lock": MAIN_LOCK, "capacity": shannon(400)},  # 找零
            ],
        }]
        records = chain.classify_main_wallet_txs(txs, MAIN_LOCK["args"])
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["direction"], "out")
        self.assertAlmostEqual(rec["amount_ckb"], 123.5)
        self.assertTrue(rec["is_change"])
        self.assertEqual(rec["tx_hash"], "0xout1")
        self.assertEqual(rec["counterparty_lock"], OTHER_LOCK)
        self.assertEqual(rec["counterparty_address"], OTHER_ADDRESS)
        self.assertEqual(rec["date"], ISO)

    def test_in_direction_uses_first_non_main_input(self):
        txs = [{
            "tx_hash": "0xin1", "timestamp": TS,
            "main_inputs": [], "main_outputs": [0, 1],
            "inputs": [{"lock": SENDER_LOCK, "capacity": shannon(600)}],
            "outputs": [
                {"lock": MAIN_LOCK, "capacity": shannon(500)},
                {"lock": MAIN_LOCK, "capacity": shannon(61)},
            ],
        }]
        records = chain.classify_main_wallet_txs(txs, MAIN_LOCK["args"])
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["direction"], "in")
        self.assertAlmostEqual(rec["amount_ckb"], 561)
        self.assertFalse(rec["is_change"])
        self.assertEqual(rec["counterparty_address"], SENDER_ADDRESS)

    def test_self_transfer_and_unrelated_tx_skipped(self):
        txs = [
            {"tx_hash": "0xself", "timestamp": TS, "main_inputs": [0],
             "main_outputs": [0],
             "outputs": [{"lock": MAIN_LOCK, "capacity": shannon(100)}]},
            {"tx_hash": "0xother", "timestamp": TS,
             "main_outputs": [],
             "outputs": [{"lock": OTHER_LOCK, "capacity": shannon(100)}]},
        ]
        self.assertEqual(chain.classify_main_wallet_txs(txs, MAIN_LOCK["args"]), [])


class ApplyMainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")
        self.store.export_legacy_status = lambda: None
        self.fake = FakeNotion()
        self.config = load_workspace_config()
        op = self.config["properties"]["ongoing_projects"]
        self.fake.create_page(self.config["data_sources"]["ongoing_projects"], {
            op["title"]: prop_title("Example"),
            op["wallet"]: prop_text(OTHER_ADDRESS),
        })

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def records():
        return [
            {"tx_hash": "0xaaa", "direction": "out", "amount_ckb": 1234567,
             "counterparty_address": OTHER_ADDRESS, "counterparty_lock": OTHER_LOCK,
             "date": "2026-01-02T20:30:00+00:00", "is_change": True},
            {"tx_hash": "0xbbb", "direction": "in", "amount_ckb": 500,
             "counterparty_address": SENDER_ADDRESS, "counterparty_lock": SENDER_LOCK,
             "date": "2026-01-05T02:00:00+00:00", "is_change": False},
        ]

    def tx_rows(self):
        tp = self.config["properties"]["transactions"]
        return {property_value(r, tp["title"]): r
                for r in self.fake.query(self.config["data_sources"]["transactions"])}

    def test_apply_main_writes_formatted_rows_and_verifies(self):
        result = chain.apply_main(self.fake, self.config, self.records())
        self.assertEqual(result["errors"], [])
        self.assertEqual(sorted(result["created"]), ["0xaaa", "0xbbb"])
        self.assertEqual(result["updated"], [])
        rows = self.tx_rows()
        tp = self.config["properties"]["transactions"]
        self.assertEqual(len(rows), 2)
        out_row, in_row = rows["0xaaa"], rows["0xbbb"]
        # 金额千分位、UTC→北京日期、类型与用途
        self.assertEqual(property_value(out_row, tp["amount"]), "1,234,567 CKB")
        self.assertEqual(property_value(out_row, tp["date"]), "2026-01-03")
        self.assertEqual(property_value(out_row, tp["type"]),
                         self.config["select_values"]["withdrawal"])
        self.assertEqual(property_value(out_row, tp["purpose"]), "To Example")
        self.assertEqual(property_value(in_row, tp["amount"]), "500 CKB")
        self.assertEqual(property_value(in_row, tp["date"]), "2026-01-05")
        self.assertEqual(property_value(in_row, tp["type"]),
                         self.config["select_values"]["deposit"])
        self.assertTrue(property_value(in_row, tp["purpose"]).startswith("From ckb1"))

    def test_apply_main_is_idempotent_and_never_deletes(self):
        chain.apply_main(self.fake, self.config, self.records())
        again = chain.apply_main(self.fake, self.config, self.records())
        self.assertEqual(again["created"], [])
        self.assertEqual(again["updated"], [])
        self.assertEqual(sorted(again["skipped"]), ["0xaaa", "0xbbb"])
        self.assertEqual(len(self.tx_rows()), 2)

    def test_apply_main_updates_mismatched_row(self):
        chain.apply_main(self.fake, self.config, self.records())
        tp = self.config["properties"]["transactions"]
        # 人工把金额改坏 → apply 应按链上数据修正回来
        row = self.tx_rows()["0xaaa"]
        self.fake.update_page(row["id"], {tp["amount"]: prop_text("1 CKB")})
        result = chain.apply_main(self.fake, self.config, self.records())
        self.assertEqual(result["created"], [])
        self.assertEqual(result["updated"], ["0xaaa"])
        self.assertEqual(property_value(self.tx_rows()["0xaaa"], tp["amount"]),
                         "1,234,567 CKB")

    def test_scan_main_buckets_missing_mismatched_extra(self):
        chain.apply_main(self.fake, self.config, self.records())
        # 手工加一笔链上找不到的表内行 → extra
        tp = self.config["properties"]["transactions"]
        from spark_web.notion import prop_date, prop_select
        self.fake.create_page(self.config["data_sources"]["transactions"], {
            tp["title"]: prop_title("0xmanual"),
            tp["type"]: prop_select(self.config["select_values"]["withdrawal"]),
            tp["date"]: prop_date("2026-01-01"),
            tp["amount"]: prop_text("1 CKB"),
        })
        result = chain.scan_main(self.fake, self.config, records=self.records())
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["mismatched"], [])
        self.assertEqual([r["tx_hash"] for r in result["extra"]], ["0xmanual"])
        self.assertEqual(result["scanned"], 2)
        # 改坏一行 → mismatched
        row = self.tx_rows()["0xbbb"]
        self.fake.update_page(row["id"], {tp["amount"]: prop_text("1 CKB")})
        result = chain.scan_main(self.fake, self.config, records=self.records())
        self.assertEqual([r["tx_hash"] for r in result["mismatched"]], ["0xbbb"])
        self.assertEqual(result["missing"], [])


if __name__ == "__main__":
    unittest.main()
