"""Offline tests: no LLM key or network needed.

Run from the project root with the app's dependencies installed:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AWS_BEARER_TOKEN_BEDROCK", "test-key-not-used")

import data  # noqa: E402
import tools  # noqa: E402
from config import Config  # noqa: E402


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        # The mock data is mutable module state; restore it after each test.
        self._snapshot = copy.deepcopy((data.ACCOUNTS, data.TRANSACTIONS, data.CARDS))

    def tearDown(self) -> None:
        for live, saved in zip((data.ACCOUNTS, data.TRANSACTIONS, data.CARDS), self._snapshot):
            live.clear()
            live.update(saved)

    def test_list_accounts_masks_numbers(self) -> None:
        out = json.loads(tools.list_accounts())
        self.assertEqual(len(out["accounts"]), 3)
        for acct in out["accounts"]:
            self.assertNotIn("number_last4", acct)
            self.assertEqual(len(acct["ending_in"]), 4)
        card = next(a for a in out["accounts"] if a["account_id"] == "CC-3001")
        self.assertEqual(card["available_credit"], 4359.80)

    def test_transactions_unknown_account_and_limit_clamp(self) -> None:
        self.assertIn("error", json.loads(tools.get_recent_transactions("NOPE")))
        out = json.loads(tools.get_recent_transactions("chk-1001", limit=999))
        self.assertEqual(len(out["transactions"]), 6)  # only 6 exist; clamped to <=10

    def test_transfer_moves_money_and_records_both_sides(self) -> None:
        out = json.loads(tools.transfer_between_accounts("CHK-1001", "SAV-2001", 250))
        self.assertEqual(out["status"], "completed")
        self.assertEqual(data.ACCOUNTS["CHK-1001"]["balance"], 2200.75)
        self.assertEqual(data.ACCOUNTS["SAV-2001"]["balance"], 13050.00)
        self.assertEqual(data.TRANSACTIONS["CHK-1001"][0]["amount"], -250.0)
        self.assertEqual(data.TRANSACTIONS["SAV-2001"][0]["amount"], 250.0)

    def test_transfer_rejections_leave_balances_untouched(self) -> None:
        before = (data.ACCOUNTS["CHK-1001"]["balance"], data.ACCOUNTS["SAV-2001"]["balance"])
        cases = [
            ("CHK-1001", "SAV-2001", 0),
            ("CHK-1001", "SAV-2001", -5),
            ("CHK-1001", "SAV-2001", float("nan")),
            ("CHK-1001", "SAV-2001", float("inf")),
            ("CHK-1001", "CHK-1001", 10),  # same account
            ("CHK-1001", "SAV-2001", 9999),  # over the per-transfer limit
            ("CHK-1001", "SAV-2001", 3000),  # over the balance (limit is 5000)
            ("CHK-1001", "CC-3001", 10),  # credit card cannot receive transfers
            ("CC-3001", "CHK-1001", 10),  # ...or send them
            ("CHK-1001", "NOPE", 10),
            ("NOPE", "SAV-2001", 10),
        ]
        for src, dst, amount in cases:
            with self.subTest(src=src, dst=dst, amount=amount):
                self.assertIn("error", json.loads(tools.transfer_between_accounts(src, dst, amount)))
        self.assertEqual(
            before, (data.ACCOUNTS["CHK-1001"]["balance"], data.ACCOUNTS["SAV-2001"]["balance"])
        )

    def test_block_card(self) -> None:
        out = json.loads(tools.block_card("card-1"))
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(data.CARDS["CARD-1"]["status"], "blocked")
        self.assertEqual(data.CARDS["CARD-2"]["status"], "active")
        self.assertEqual(json.loads(tools.block_card("CARD-1"))["note"], "Already blocked.")
        self.assertIn("error", json.loads(tools.block_card("CARD-99")))

    def test_product_rates(self) -> None:
        self.assertEqual(len(json.loads(tools.get_product_rates())["rates"]), 4)
        self.assertEqual(json.loads(tools.get_product_rates("savings"))["apy_percent"], 4.10)
        self.assertIn("error", json.loads(tools.get_product_rates("crypto")))

    def test_customer_profile_returns_contact_details_only(self) -> None:
        out = json.loads(tools.get_customer_profile())
        self.assertEqual(out["name"], "Alex Morgan")
        self.assertEqual(out["email"], "alex.morgan@example.com")
        self.assertEqual(out["phone"], "+1 (415) 555-0142")
        self.assertIn("San Francisco", out["mailing_address"])
        self.assertEqual(out["customer_id"], "CUS-100245")
        # Nothing the agent must never repeat is held or returned.
        text = json.dumps(out).lower()
        for forbidden in ("ssn", "social", "date_of_birth", "dob", "password", "pin"):
            self.assertNotIn(forbidden, set(out), forbidden)
        self.assertNotIn("password", text)
        # list_accounts greets the same customer, from the same record.
        self.assertEqual(json.loads(tools.list_accounts())["customer"], out["name"])

    def test_tool_registry_and_prompt_agree(self) -> None:
        from system_prompt import SYSTEM_PROMPT

        names = {t.tool_name for t in tools.ALL_TOOLS}
        self.assertEqual(
            names,
            {
                "list_accounts",
                "get_customer_profile",
                "get_recent_transactions",
                "transfer_between_accounts",
                "list_cards",
                "block_card",
                "get_product_rates",
            },
        )
        prompt = SYSTEM_PROMPT.format(bank="Demo Bank")  # must still format cleanly
        self.assertIn("Demo Bank", prompt)
        # Any tool the prompt names by identifier must exist.
        for name in re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", prompt):
            self.assertIn(name, names)


class ConfigTests(unittest.TestCase):
    def _from_env(self, env: dict) -> Config:
        with mock.patch.dict(os.environ, env, clear=True):
            return Config.from_env()

    def test_direct_mode_is_the_default_and_needs_the_bedrock_key(self) -> None:
        cfg = self._from_env({"AWS_BEARER_TOKEN_BEDROCK": "k"})
        self.assertFalse(cfg.use_llm_provider)
        self.assertEqual(cfg.llm_route, "direct-bedrock")
        self.assertEqual(cfg.bedrock_api_key, "k")
        self.assertEqual(cfg.aws_region, "us-east-1")
        with self.assertRaisesRegex(RuntimeError, "AWS_BEARER_TOKEN_BEDROCK"):
            self._from_env({})

    def test_gateway_mode_needs_url_and_key_but_not_the_bedrock_key(self) -> None:
        cfg = self._from_env(
            {
                "USE_LLM_PROVIDER": "TRUE",
                "LLM_PROVIDER_URL": "http://gw.default:22893/bedrock/",
                "LLM_PROVIDER_KEY": "gw-key",
                # A leftover Bedrock key must be ignored, never carried into the config.
                "AWS_BEARER_TOKEN_BEDROCK": "leftover",
            }
        )
        self.assertTrue(cfg.use_llm_provider)
        self.assertEqual(cfg.llm_route, "agent-manager-gateway")
        self.assertEqual(cfg.llm_provider_url, "http://gw.default:22893/bedrock")  # slash trimmed
        self.assertEqual(cfg.bedrock_api_key, "")
        for missing in ({"LLM_PROVIDER_URL": "http://gw/x"}, {"LLM_PROVIDER_KEY": "k"}, {}):
            with self.subTest(env=missing), self.assertRaisesRegex(RuntimeError, "LLM_PROVIDER_URL"):
                self._from_env({"USE_LLM_PROVIDER": "true", **missing})

    def test_bad_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be true or false"):
            self._from_env({"USE_LLM_PROVIDER": "maybe", "AWS_BEARER_TOKEN_BEDROCK": "k"})
        for url in ("ftp://gw/x", "http://user:pw@gw/x", "http://gw/x?a=1", "http://gw/x#f", "not a url"):
            with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, "LLM_PROVIDER_URL"):
                self._from_env(
                    {"USE_LLM_PROVIDER": "true", "LLM_PROVIDER_URL": url, "LLM_PROVIDER_KEY": "k"}
                )


class TracingTests(unittest.TestCase):
    VAR = "OTEL_SEMCONV_STABILITY_OPT_IN"

    def test_configure_adds_required_tokens_and_keeps_existing_ones(self) -> None:
        import tracing

        cases = {
            "": {"gen_ai_latest_experimental", "gen_ai_span_attributes_only"},
            "gen_ai_latest_experimental": {"gen_ai_latest_experimental", "gen_ai_span_attributes_only"},
            " gen_ai_tool_definitions , gen_ai_latest_experimental": {
                "gen_ai_tool_definitions",
                "gen_ai_latest_experimental",
                "gen_ai_span_attributes_only",
            },
        }
        for before, expected in cases.items():
            with self.subTest(before=before), mock.patch.dict(os.environ, {self.VAR: before}):
                tracing.configure()
                tokens = os.environ[self.VAR].split(",")
                self.assertEqual(set(tokens), expected)
                self.assertEqual(len(tokens), len(expected), "no duplicates")
        with mock.patch.dict(os.environ, {}, clear=True):
            tracing.configure()
            self.assertEqual(set(os.environ[self.VAR].split(",")), set(tracing.REQUIRED))

    def test_importing_the_app_enables_attribute_based_traces(self) -> None:
        # Strands reads the setting when its tracer is first created, so the app must
        # have set it by the time it imports Strands.
        import app  # noqa: F401

        tokens = set(os.environ[self.VAR].split(","))
        self.assertLessEqual({"gen_ai_latest_experimental", "gen_ai_span_attributes_only"}, tokens)


def converse_reply(content: list[dict], stop_reason: str = "end_turn") -> dict:
    """A Bedrock Converse response body with the given assistant content blocks."""
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop_reason,
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1},
    }


class FakeConverseServer:
    """Local stand-in for Bedrock or the Agent Manager gateway: records every
    request and answers with a Converse text reply ("hello"), or with whatever
    `responder(request_body)` returns as (http_status, json_body). Makes no network
    calls."""

    def __init__(self, responder=None) -> None:
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                status, payload = 200, converse_reply([{"text": "hello"}])
                if responder is not None:
                    status, payload = responder(body)
                reply = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def __enter__(self) -> "FakeConverseServer":
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class ModelRoutingTests(unittest.TestCase):
    MODEL = "us.anthropic.claude-sonnet-4-6"

    def _cfg(self, **overrides) -> Config:
        base = dict(
            use_llm_provider=False,
            llm_provider_url="",
            llm_provider_key="",
            bedrock_api_key="direct-key-123",
            bedrock_model_id=self.MODEL,
            aws_region="us-east-1",
            bank_name="Demo Bank",
            agent_version="t",
        )
        base.update(overrides)
        return Config(**base)

    def test_gateway_route_uses_the_gateway_key_and_nothing_else(self) -> None:
        from agent import build_agent

        with FakeConverseServer() as gw:
            cfg = self._cfg(
                use_llm_provider=True,
                llm_provider_url=f"{gw.url}/banking-bedrock",
                llm_provider_key="GW-KEY-123",
                bedrock_api_key="",
            )
            # The module-level test env has a Bedrock key set: it must not leak.
            self.assertTrue(os.environ.get("AWS_BEARER_TOKEN_BEDROCK"))
            self.assertEqual(str(build_agent(cfg)("hi")).strip(), "hello")
        req = gw.requests[0]
        self.assertEqual(req["path"], f"/banking-bedrock/model/{self.MODEL}/converse")
        self.assertEqual(req["headers"]["api-key"], "GW-KEY-123")
        self.assertNotIn("authorization", req["headers"])
        self.assertFalse([k for k in req["headers"] if k.startswith("x-amz")])
        self.assertFalse([v for v in req["headers"].values() if "test-key-not-used" in v])
        self.assertIn("Demo Bank", req["body"]["system"][0]["text"])
        self.assertEqual(len(req["body"]["toolConfig"]["tools"]), 7)

    def test_direct_route_still_sends_the_bedrock_bearer_key(self) -> None:
        from agent import build_agent

        with FakeConverseServer() as bedrock:
            with mock.patch.dict(os.environ, {"AWS_ENDPOINT_URL_BEDROCK_RUNTIME": bedrock.url}):
                self.assertEqual(str(build_agent(self._cfg())("hi")).strip(), "hello")
        req = bedrock.requests[0]
        self.assertEqual(req["path"], f"/model/{self.MODEL}/converse")
        self.assertEqual(req["headers"]["authorization"], "Bearer direct-key-123")
        self.assertNotIn("api-key", req["headers"])


class FailedTurnTests(unittest.TestCase):
    """What the app does when a model call fails: the real Strands agent on the
    gateway route, through the real /chat endpoint, against a local stand-in for
    Agent Manager's gateway."""

    # What Agent Manager's gateway answers when one of its policies refuses a model
    # call (HTTP 422). Body copied from a real deployment's log. The agent gives it
    # no special treatment: to the agent it is one more failed model call.
    REJECTION = {
        "type": "AWS_BEDROCK_GUARDRAIL",
        "message": {
            "action": "GUARDRAIL_INTERVENED",
            "actionReason": "Violation of AWS Bedrock Guardrail detected.",
            "direction": "REQUEST",
            "interveningGuardrail": "AWS Bedrock Guardrail",
        },
    }

    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient

        import app as app_module

        cls.app_module = app_module
        cls.client = TestClient(app_module.app)

    def setUp(self) -> None:
        from agent import build_agent

        self.app_module.SESSIONS.clear()
        self.gateway = FakeConverseServer(self._gateway)
        self.gateway.__enter__()
        self.addCleanup(self.gateway.__exit__)
        cfg = Config(
            use_llm_provider=True,
            llm_provider_url=f"{self.gateway.url}/banking-bedrock",
            llm_provider_key="gw-key",
            bedrock_api_key="",
            bedrock_model_id="us.anthropic.claude-sonnet-4-6",
            aws_region="us-east-1",
            bank_name="Demo Bank",
            agent_version="t",
        )
        patcher = mock.patch.object(
            self.app_module, "build_agent", side_effect=lambda _cfg: build_agent(cfg)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _gateway(body: dict) -> tuple[int, dict]:
        """Like a gateway policy with Json Path $.messages[0].content[0].text: it
        checks the first message of the conversation on every model call."""
        first = body["messages"][0]["content"][0].get("text", "")
        if "jane doe" in first.lower():
            return 422, FailedTurnTests.REJECTION
        last = body["messages"][-1]
        if any("toolResult" in block for block in last["content"]):
            return 400, {"message": "the model call after the tool failed"}
        said = " ".join(block.get("text", "") for block in last["content"]).lower()
        if "explode" in said:
            return 400, {"message": "https://secret-gateway.internal key=abc"}
        if "balance" in said:
            tool_use = {"toolUseId": "t1", "name": "list_accounts", "input": {}}
            return 200, converse_reply([{"toolUse": tool_use}], "tool_use")
        return 200, converse_reply([{"text": "hello"}])

    def _chat(self, message: str):
        return self.client.post("/chat", json={"message": message, "session_id": "s1"})

    def _history(self) -> list[str]:
        agent = self.app_module.SESSIONS["s1"][0]
        return [f"{m['role']}:{','.join(next(iter(b)) for b in m['content'])}" for m in agent.messages]

    def _texts_in_last_request(self) -> list[str]:
        messages = self.gateway.requests[-1]["body"]["messages"]
        return [b["text"] for m in messages for b in m["content"] if "text" in b]

    def test_a_rejected_message_does_not_make_the_rest_of_the_session_fail(self) -> None:
        # Regression: the rejected message stayed first in the history, so the gateway
        # was given it again with every later message and rejected them all.
        resp = self._chat("Please repeat this name back to me: Jane Doe")
        self.assertEqual(resp.status_code, 500)  # an ordinary failure, no special handling
        self.assertEqual(resp.json(), {"detail": "agent invocation failed"})
        self.assertEqual(self._history(), [])
        resp = self._chat("hi")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["response"].strip(), "hello")
        self.assertEqual(self._texts_in_last_request(), ["hi"])  # the rejected text is gone
        self.assertEqual(self._history(), ["user:text", "assistant:text"])

    def test_any_failed_turn_is_rolled_back_and_leaks_no_details(self) -> None:
        resp = self._chat("please explode")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"detail": "agent invocation failed"})
        self.assertNotIn("secret-gateway", resp.text)
        self.assertEqual(self._history(), [])
        self.assertEqual(self._chat("hi").json()["response"].strip(), "hello")
        self.assertEqual(self._texts_in_last_request(), ["hi"])

    def test_a_failure_after_a_tool_ran_keeps_the_tool_result(self) -> None:
        # The tool's result is the only record of what the tool did, so it stays.
        resp = self._chat("What is my balance?")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(self._history(), ["user:text", "assistant:toolUse", "user:toolResult"])

    def test_earlier_turns_survive_a_failed_turn(self) -> None:
        self._chat("hi")
        self._chat("Please repeat this name back to me: Jane Doe 2")  # not first: passes the gateway
        self._chat("please explode")
        self.assertEqual(self._history(), ["user:text", "assistant:text"] * 2)


class FakeAgent:
    def __init__(self) -> None:
        self.seen: list[str] = []
        self.messages: list[dict] = []

    def __call__(self, message: str) -> str:
        self.seen.append(message)
        return f"echo: {message}"


class HttpContractTests(unittest.TestCase):
    """The Agent Manager Chat Agent contract: POST /chat {message, session_id}."""

    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient

        import app as app_module

        cls.app_module = app_module
        cls.client = TestClient(app_module.app)

    def setUp(self) -> None:
        self.app_module.SESSIONS.clear()
        self.fake = FakeAgent()
        patcher = mock.patch.object(self.app_module, "build_agent", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_health(self) -> None:
        body = self.client.get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["bank"], "Demo Bank")
        self.assertEqual(body["llm_route"], "direct-bedrock")

    def test_chat_returns_response_and_reuses_session(self) -> None:
        first = self.client.post("/chat", json={"message": "hi", "session_id": "s1"}).json()
        self.assertEqual(first["response"], "echo: hi")
        self.assertEqual(first["session_id"], "s1")
        self.client.post("/chat", json={"message": "again", "session_id": "s1"})
        self.assertEqual(self.fake.seen, ["hi", "again"])
        self.assertEqual(len(self.app_module.SESSIONS), 1)

    def test_chat_without_session_id_generates_one(self) -> None:
        body = self.client.post("/chat", json={"message": "hi"}).json()
        self.assertTrue(body["session_id"])

    def test_chat_accepts_context_field(self) -> None:
        resp = self.client.post("/chat", json={"message": "hi", "context": {"channel": "web"}})
        self.assertEqual(resp.status_code, 200)

    def test_chat_rejects_bad_input(self) -> None:
        self.assertEqual(self.client.post("/chat", json={"message": "   "}).status_code, 400)
        self.assertEqual(self.client.post("/chat", json={}).status_code, 422)
        self.assertEqual(self.client.post("/chat", json={"message": "x" * 4001}).status_code, 422)

    def test_agent_failure_does_not_leak_details(self) -> None:
        boom = mock.Mock(
            side_effect=RuntimeError("https://secret-gateway.internal key=abc"), messages=[]
        )
        with mock.patch.object(self.app_module, "build_agent", return_value=boom):
            resp = self.client.post("/chat", json={"message": "hi"})
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn("secret-gateway", resp.text)


class EntrypointTests(unittest.TestCase):
    def test_binds_8000_even_when_PORT_env_is_set(self) -> None:
        # Regression: the deployed pod had PORT=8080, so the agent listened where
        # Agent Manager's port-8000 startup probe never looked.
        import main

        with mock.patch.dict(os.environ, {"PORT": "8080"}), mock.patch.object(
            main.uvicorn, "run"
        ) as run:
            main.main()
        self.assertEqual(run.call_args.kwargs["port"], 8000)
        self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
