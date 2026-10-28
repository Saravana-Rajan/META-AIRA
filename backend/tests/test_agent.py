import copy
import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from aira import agent as agent_mod
from aira.agent import FALLBACK, MAX_HISTORY_TURNS, MAX_ITERATIONS, TOO_MANY_STEPS, Agent
from aira.config import Settings
from aira.contracts import CueMsg
from aira.mock_agent import MockAgent, parse_items
from aira.prompt import system_prompt
from aira.tools.base import Tool, ToolContext, obj, ok

CATALOG = [
    {"sku": "BALL-TEN", "name": "Tennis Ball", "unit": "piece", "price": 60,
     "aliases": {"en": ["tennis ball"], "ta": ["பந்து"], "hi": ["गेंद"]}},
    {"sku": "GRIP", "name": "Bat Grip", "unit": "piece", "price": 150, "aliases": {"en": ["grip"]}},
]


class FakeStore:
    async def products(self):
        return CATALOG


def make_ctx(lang: str = "en") -> tuple[ToolContext, list]:
    sent: list = []

    async def send(msg):
        sent.append(msg)

    async def request_frame(purpose):
        return None

    ctx = ToolContext(session_id="s1", shop_id="shop", store=FakeStore(), confirms=None,
                      request_frame=request_frame, send=send, lang=lang)
    return ctx, sent


# ---- fake OpenAI client --------------------------------------------------------------------------------
def tool_call(cid: str, name: str, args) -> SimpleNamespace:
    arguments = args if isinstance(args, str) else json.dumps(args)
    return SimpleNamespace(id=cid, type="function", function=SimpleNamespace(name=name, arguments=arguments))


def reply(content: str | None = None, calls: list | None = None) -> SimpleNamespace:
    msg = SimpleNamespace(role="assistant", content=content, tool_calls=calls or None)
    finish = "tool_calls" if calls else "stop"
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        item = self.script.pop(0) if self.script else reply("fallback")
        if isinstance(item, Exception):
            raise item
        return item


SETTINGS = Settings(_env_file=None, aira_mock=False, model_api_key="test")


def recording_tools(log: list):
    async def price_query(ctx, product: str | None = None):
        log.append(("price_query", ctx.call_id, product))
        return ok(f"{product} is 150 rupees", product=product, price=150)

    async def stock_query(ctx, product: str | None = None):
        log.append(("stock_query", ctx.call_id, product))
        return ok("12 left", on_hand=12)

    async def boom(ctx):
        raise RuntimeError("kaput")

    return [
        Tool("price_query", "Price", obj({"product": {"type": "string"}}), price_query),
        Tool("stock_query", "Stock", obj({"product": {"type": "string"}}), stock_query),
        Tool("boom", "Fails", obj({}), boom),
    ]


def make_agent(script, lang="en"):
    ctx, sent = make_ctx(lang)
    log: list = []
    client = FakeClient(script)
    a = Agent(ctx, recording_tools(log), FakeStore(), client=client, settings=SETTINGS)
    a.backoff_s = 0
    return a, client, ctx, sent, log


# ---- real path -----------------------------------------------------------------------------------------
async def test_tool_loop_ordering_and_wiring():
    a, client, ctx, _, log = make_agent([
        reply(calls=[tool_call("call_1", "price_query", {"product": "grip"})]),
        reply("A grip is 150 rupees."),
    ])
    text = await a.turn("price of grip")
    assert text == "A grip is 150 rupees."
    assert len(client.calls) == 2

    first = client.calls[0]
    assert first["model"] == SETTINGS.model_for("agent")
    assert first["reasoning_effort"] == SETTINGS.reasoning_effort
    assert first["tool_choice"] == "auto"
    assert [t["function"]["name"] for t in first["tools"]] == ["price_query", "stock_query", "boom"]
    for banned in ("stop", "n", "logit_bias", "logprobs", "temperature"):
        assert banned not in first
    assert [m["role"] for m in first["messages"]] == ["system", "user"]

    msgs = client.calls[1]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool"]
    assert msgs[2]["tool_calls"][0]["id"] == "call_1"
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "price_query", "arguments": '{"product": "grip"}'}
    assert msgs[3]["tool_call_id"] == "call_1"
    assert json.loads(msgs[3]["content"])["price"] == 150

    assert log == [("price_query", "call_1", "grip")]
    assert ctx.results[-1]["price"] == 150
    assert a.history[-1][-1] == {"role": "assistant", "content": "A grip is 150 rupees."}


async def test_parallel_tool_calls_each_get_a_tool_message():
    a, client, ctx, _, log = make_agent([
        reply(calls=[tool_call("c1", "price_query", {"product": "grip"}),
                     tool_call("c2", "stock_query", {})]),
        reply("Grip 150 rupees, 12 left."),
    ])
    await a.turn("grip price and stock")
    msgs = client.calls[1]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "tool"]
    assert [m["tool_call_id"] for m in msgs[3:]] == ["c1", "c2"]
    assert [x[1] for x in log] == ["c1", "c2"]
    assert len(ctx.results) == 2


async def test_bad_args_unknown_tool_and_failing_tool_become_error_results():
    a, client, ctx, _, _ = make_agent([
        reply(calls=[
            tool_call("c1", "nope", {}),
            tool_call("c2", "price_query", "{not json"),
            tool_call("c3", "price_query", "[1, 2]"),
            tool_call("c4", "price_query", {"colour": "red"}),
            tool_call("c5", "boom", {}),
        ]),
        reply("Sorry, that did not work."),
    ])
    assert await a.turn("x") == "Sorry, that did not work."
    tool_msgs = [m for m in client.calls[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2", "c3", "c4", "c5"]
    for m in tool_msgs:
        assert json.loads(m["content"])["status"] == "error"
    assert len(ctx.results) == 5
    assert "unknown tool" in ctx.results[0]["message"]


async def test_safety_correction_round():
    a, client, ctx, sent, _ = make_agent([
        reply(calls=[tool_call("c1", "price_query", {"product": "grip"})]),
        reply("A grip is 175 rupees."),
        reply("A grip is 150 rupees."),
    ])
    assert await a.turn("price of grip") == "A grip is 150 rupees."
    assert len(client.calls) == 3
    last = client.calls[2]["messages"]
    assert last[-2] == {"role": "assistant", "content": "A grip is 175 rupees."}
    assert last[-1]["role"] == "user"
    assert last[-1]["content"].startswith("[SYSTEM NOTE] Correction needed:")
    assert "175" in last[-1]["content"]
    assert any(isinstance(m, CueMsg) and m.cue == "warn" for m in sent)


async def test_only_one_correction_round():
    a, client, _, _, _ = make_agent([reply("That is 999 rupees."), reply("Still 888 rupees.")])
    assert await a.turn("price?") == "Still 888 rupees."
    assert len(client.calls) == 2


async def test_catalog_price_needs_no_correction():
    a, client, _, sent, _ = make_agent([reply("A tennis ball is 60 rupees.")])
    assert await a.turn("tennis ball price") == "A tennis ball is 60 rupees."
    assert len(client.calls) == 1 and sent == []


async def test_iteration_cap():
    script = [reply(calls=[tool_call(f"c{i}", "stock_query", {})]) for i in range(10)]
    a, client, ctx, _, _ = make_agent(script)
    assert await a.turn("loop forever") == TOO_MANY_STEPS["en"]
    assert len(client.calls) == MAX_ITERATIONS
    assert len(ctx.results) == MAX_ITERATIONS
    assert a.history[-1][-1] == {"role": "assistant", "content": TOO_MANY_STEPS["en"]}


def _rate_limit():
    resp = httpx.Response(429, headers={"retry-after": "0"}, request=httpx.Request("POST", "http://x"))
    return openai.RateLimitError("slow down", response=resp, body=None)


def _server_error():
    resp = httpx.Response(503, request=httpx.Request("POST", "http://x"))
    return openai.InternalServerError("down", response=resp, body=None)


async def test_retry_once_on_429_then_succeed():
    a, client, _, _, _ = make_agent([_rate_limit(), reply("Okay.")])
    assert await a.turn("hi") == "Okay."
    assert len(client.calls) == 2


async def test_two_failures_give_spoken_error(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(agent_mod.asyncio, "sleep", fake_sleep)
    a, client, _, _, _ = make_agent([_server_error(), _server_error()], lang="ta")
    assert await a.turn("hi") == FALLBACK["ta"]
    assert len(client.calls) == 2 and len(slept) == 1


async def test_non_retryable_error_is_not_retried():
    resp = httpx.Response(400, request=httpx.Request("POST", "http://x"))
    a, client, _, _, _ = make_agent([openai.BadRequestError("bad", response=resp, body=None)])
    assert await a.turn("hi") == FALLBACK["en"]
    assert len(client.calls) == 1


async def test_history_persists_and_is_capped():
    a, client, _, _, _ = make_agent([reply(f"ok {i}") for i in range(MAX_HISTORY_TURNS + 5)])
    for i in range(MAX_HISTORY_TURNS + 5):
        await a.turn(f"turn {i}")
    assert len(a.history) == MAX_HISTORY_TURNS
    msgs = client.calls[-1]["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "turn 5"}
    assert msgs[-1] == {"role": "user", "content": f"turn {MAX_HISTORY_TURNS + 4}"}


async def test_system_prompt_in_messages_uses_catalog_and_language():
    a, client, _, _, _ = make_agent([reply("சரி.")], lang="ta")
    await a.turn("வணக்கம்")
    sysmsg = client.calls[0]["messages"][0]["content"]
    assert "Tamil" in sysmsg and "BALL-TEN" in sysmsg and "150" in sysmsg


async def test_mock_mode_delegates_to_mock_agent():
    ctx, _ = make_ctx()
    a = Agent(ctx, [], FakeStore(), settings=Settings(_env_file=None, aira_mock=True))
    assert isinstance(a._mock, MockAgent)
    assert "did not catch" in await a.turn("gibberish words")


# ---- prompt --------------------------------------------------------------------------------------------
def test_system_prompt_rules():
    p = system_prompt(CATALOG, "hi")
    for needle in ("AIRA", "Hindi", "[SYSTEM NOTE]", "Correction needed:", "yes", "real or fake",
                   "two tool calls", "BALL-TEN | Tennis Ball | piece | 60 | tennis ball, பந்து, गेंद"):
        assert needle in p
    assert "catalog not loaded" in system_prompt([], "en")


# ---- mock agent ----------------------------------------------------------------------------------------
def shop_fakes(calls: list):
    state = {"n": 0}

    def rec(name):
        def deco(fn):
            async def handler(ctx, **kw):
                calls.append((name, kw))
                return await fn(ctx, **kw)

            return Tool(name, name, obj({}), handler)

        return deco

    @rec("price_query")
    async def price_query(ctx, product=None):
        return ok(f"{product} is 60 rupees.", price=60)

    @rec("stock_query")
    async def stock_query(ctx, product=None):
        return ok(f"{product or 'all'}: 14 left.", on_hand=14)

    @rec("sale_start")
    async def sale_start(ctx, items):
        return ok("Sale open, total 270 rupees.", sale_id="S1", total=270)

    @rec("sale_add")
    async def sale_add(ctx, sale_id, items):
        return ok("Added, total 1470 rupees.", sale_id=sale_id, total=1470)

    @rec("sale_pay")
    async def sale_pay(ctx, sale_id, method):
        state["n"] += 1
        cid = f"cf{state['n']}"
        return ok(f"{method} for {sale_id}. Say yes.", needs_confirm=True, confirm_id=cid)

    @rec("confirm_action")
    async def confirm_action(ctx, confirm_id, answer):
        return ok("Done, sale saved." if answer == "yes" else "Cancelled.", confirmed=answer == "yes")

    @rec("delivery_check")
    async def delivery_check(ctx, product, expected=None):
        return ok("I count 10, expected 12.", counted=10, expected=expected, needs_confirm=True,
                  confirm_id="dv1")

    @rec("camera_ask")
    async def camera_ask(ctx, question):
        return ok("A box of tennis balls.")

    @rec("day_summary")
    async def day_summary(ctx):
        return ok("3 sales today.")

    return [price_query, stock_query, sale_start, sale_add, sale_pay, confirm_action, delivery_check,
            camera_ask, day_summary]


def test_parse_items():
    assert parse_items("two tennis balls and one grip") == [
        {"product": "tennis balls", "qty": 2}, {"product": "grip", "qty": 1}]
    assert parse_items("bat, 3 grips please") == [
        {"product": "bat", "qty": 1}, {"product": "grips", "qty": 3}]


async def test_mock_agent_end_to_end():
    ctx, _ = make_ctx()
    calls: list = []
    m = MockAgent(ctx, shop_fakes(calls), FakeStore())

    assert await m.turn("What's the price of the tennis ball?") == "tennis ball is 60 rupees."
    assert calls[-1] == ("price_query", {"product": "tennis ball"})

    assert await m.turn("How many tennis balls do we have?") == "tennis balls: 14 left."
    assert calls[-1] == ("stock_query", {"product": "tennis balls"})

    assert await m.turn("upi") == "There is no open sale to pay for."

    assert await m.turn("Sell two tennis balls and one grip") == "Sale open, total 270 rupees."
    assert calls[-1] == ("sale_start", {"items": [{"product": "tennis balls", "qty": 2},
                                                   {"product": "grip", "qty": 1}]})
    assert await m.turn("add 1 bat") == "Added, total 1470 rupees."
    assert calls[-1] == ("sale_add", {"sale_id": "S1", "items": [{"product": "bat", "qty": 1}]})

    assert await m.turn("pay cash") == "cash for S1. Say yes."
    assert calls[-1] == ("sale_pay", {"sale_id": "S1", "method": "cash"})
    assert await m.turn("ஆம்") == "Done, sale saved."
    assert calls[-1] == ("confirm_action", {"confirm_id": "cf1", "answer": "yes"})
    assert m.open_sale_id is None and m.pending == []

    assert await m.turn("haan") == "There is nothing waiting for a yes or no."

    assert await m.turn("check delivery of twelve tennis balls") == "I count 10, expected 12."
    assert calls[-1] == ("delivery_check", {"product": "tennis balls", "expected": 12})
    assert await m.turn("no") == "Cancelled."
    assert calls[-1] == ("confirm_action", {"confirm_id": "dv1", "answer": "no"})

    assert await m.turn("What is this?") == "A box of tennis balls."
    assert calls[-1] == ("camera_ask", {"question": "What is this?"})
    assert await m.turn("give me the summary") == "3 sales today."
    assert calls[-1] == ("day_summary", {})
    assert await m.turn("stock") == "all: 14 left."

    assert len(ctx.results) == len(calls)
    assert ctx.call_id.startswith("mock-s1-")


async def test_mock_agent_upi_and_hindi_yes():
    ctx, _ = make_ctx()
    calls: list = []
    m = MockAgent(ctx, shop_fakes(calls), None)
    await m.turn("sell 1 grip")
    assert await m.turn("UPI") == "upi for S1. Say yes."
    assert await m.turn("हाँ") == "Done, sale saved."
    assert calls[-1] == ("confirm_action", {"confirm_id": "cf1", "answer": "yes"})


async def test_mock_agent_unknown_tool_and_failure():
    ctx, _ = make_ctx()

    async def broken(ctx, **kw):
        raise RuntimeError("nope")

    m = MockAgent(ctx, [Tool("price_query", "p", obj({}), broken)], None)
    assert (await m.turn("price of bat")).startswith("Sorry, price_query failed")
    assert (await m.turn("summary")).startswith("Sorry, unknown tool")
    assert [r["status"] for r in ctx.results] == ["error", "error"]


@pytest.mark.parametrize("lang", ["en", "ta", "hi"])
def test_fallbacks_exist(lang):
    assert FALLBACK[lang] and TOO_MANY_STEPS[lang]
