# Backend module contracts (read before editing any module)

Python 3.12, async everywhere, `from aira.config import get_settings`. **Mock mode** (`get_settings().mock`) must never touch the network: every model-calling function has a deterministic mock path. Tests run offline with `uv run pytest`.

Already written (do not change signatures without updating this file): `aira/config.py`, `aira/meta_client.py`, `aira/contracts.py`, `aira/tools/base.py`.

## Ownership (one owner per file)
| Owner | Files |
|---|---|
| A business | `aira/store.py`, `aira/firestore_store.py`, `aira/confirm.py`, `aira/catalog.py`, `aira/data/catalog.json`, `aira/tools/shop.py`, `tests/test_store.py`, `tests/test_confirm.py`, `tests/test_shop_tools.py` |
| B agent | `aira/agent.py`, `aira/mock_agent.py`, `aira/prompt.py`, `aira/safety.py`, `tests/test_agent.py`, `tests/test_safety.py` |
| C vision | `aira/vision/*.py`, `aira/tools/vision_tools.py`, `tests/test_vision*.py` |
| D voice | `aira/voice.py`, `tests/test_voice.py` |
| E server | `aira/main.py`, `aira/session.py`, `aira/events.py`, `tests/test_server.py` |
| F app | `frontend/app/**` |
| G dashboard | `frontend/dashboard/**` |
| H infra | `Dockerfile`, `.dockerignore`, `infra/**`, `.github/workflows/**`, `.env.example`, root `README.md` |

`aira/registry.py` (written by the lead): `ALL_TOOLS = shop.TOOLS + vision_tools.TOOLS`, `BY_NAME`. Import it lazily (inside functions) where needed.

## A: store (`aira/store.py`)
```python
class Store(Protocol):            # implemented by MemoryStore (store.py) and FirestoreStore (firestore_store.py)
    async def products(self) -> list[dict]                       # active products, catalog order
    async def product(self, sku: str) -> dict | None
    async def find_product(self, name: str) -> dict | None       # case-insensitive match on sku, name, aliases.{en,ta,hi}; singular/plural tolerant ("bats" -> bat)
    async def stock(self, sku: str) -> int
    async def stock_all(self) -> dict[str, int]
    async def get(self, collection: str, doc_id: str) -> dict | None
    async def put(self, collection: str, doc_id: str, data: dict) -> None
    async def query(self, collection: str, **equals) -> list[dict]
    async def commit(self, idem_key: str, *, ledger: list[dict], docs: list[tuple[str, str, dict]]) -> bool
        # Atomic + idempotent: if idem_key was already committed return False and change nothing.
        # Each ledger row: {"sku", "delta": int, "reason": "SALE"|"DELIVERY"|"ADJUST", "ref_type", "ref_id", "actor"}.
        # Applies the deltas to stock (never below 0 -> raise ValueError), appends ledger rows (with idem_key, ts),
        # writes docs (collection, id, data). Emits a store event per doc and per ledger row.
    async def events(self, after: int = 0) -> list[dict]            # [{"seq", "kind", "data", "ts"}] for the dashboard
    async def add_event(self, kind: str, data: dict) -> None
def make_store(shop_id: str) -> Store                                 # settings.store: "memory" | "firestore"; seeds catalog.json
```
Product doc: `{"sku","name","brand","unit":"piece|pack|set","price":int rupees,"cost_price","reorder_threshold","aliases":{"en":[],"ta":[],"hi":[]},"look":str,"sam_noun":str,"active":true}`. Stock lives in `inventory/{sku}.on_hand`.
Collections: `sales`, `payments`, `deliveries`, `ledger`, `proposals`, `events`. Money is integer rupees everywhere.

## A: confirm (`aira/confirm.py`)
```python
class ConfirmBook:
    def __init__(self, ttl_s: float = 120)
    async def propose(self, ctx: ToolContext, *, kind: str, prompt: str, facts: dict,
                      committer: Callable[[], Awaitable[dict]]) -> dict
        # stores a pending proposal (session_id, ttl), sends contracts.Confirm(id, kind, prompt) via ctx.send,
        # returns {"status":"ok", **facts, "needs_confirm": True, "confirm_id": id, "say_en": prompt}
    async def resolve(self, session_id: str, confirm_id: str, answer: str) -> dict
        # per-proposal asyncio.Lock; other session -> {"status":"error","message":"unknown confirmation"};
        # committed -> {"status":"ok","already_done":True, **stored_result}; expired/"no" -> rejected;
        # only answer == "yes" runs committer once; if committer raises, proposal stays pending.
    def reject_all(self, session_id: str) -> None                   # on Stop
    def pending(self, session_id: str) -> list[dict]
```

## A: shop tools (`aira/tools/shop.py`, `TOOLS: list[Tool]`)
`price_query(product?)`, `stock_query(product?)`, `sale_start(items[{product,qty}])`, `sale_add(sale_id, items)`,
`sale_pay(sale_id, method: "cash"|"upi")` (needs_confirm; cash -> `ctx.request_frame("cash")` -> `vision.cash.read_cash`;
change computed in code; UPI -> owner confirms), `confirm_action(confirm_id, answer)`, `day_summary()`.
Totals/change are computed in Python; results carry the numbers the agent may speak.

## B: agent (`aira/agent.py`)
```python
class Agent:
    def __init__(self, ctx: ToolContext, tools: list[Tool], store: Store)
    async def turn(self, user_text: str) -> str        # runs Muse Spark tool loop (or MockAgent in mock mode);
        # returns final text to speak; appends every tool result to ctx.results; runs safety.check and self-corrects once.
```
`aira/safety.py`: `def check(spoken: str, results: list[dict], catalog: list[dict]) -> list[str]` (violations).
`aira/prompt.py`: `def system_prompt(products: list[dict], lang: str) -> str`.
`aira/mock_agent.py`: rule-based intent -> tool calls for offline demo/tests (English + a few Tamil/Hindi words).

## C: vision (`aira/vision/`)
```python
@dataclass class SamCount: count: int; boxes: list[contracts.Box]; ok: bool; note: str = ""
async def count_items(frame: FrameData, noun: str, min_conf: float = 0.5) -> SamCount          # vision/sam.py
@dataclass class CashReading: agreed: bool; total: int | None; notes: dict[int, int]; hint: str | None
async def read_cash(frame: FrameData) -> CashReading                                          # vision/cash.py
@dataclass class InvoiceReading: agreed: bool; supplier: str | None; lines: list[dict]; total: int | None; disagreements: list[str]
async def read_invoice(frame: FrameData) -> InvoiceReading                                    # vision/invoice.py
async def look(frame: FrameData, question: str) -> str                                        # vision/look.py
```
Mock hooks: each module exposes `MOCK_RESULT` (module-level) that tests may override.
Vision tools (`aira/tools/vision_tools.py`, `TOOLS`): `camera_ask(question)`, `delivery_check(product, expected?)` (needs_confirm; SAM count + optional invoice -> discrepancy -> propose stock DELIVERY ledger via ctx.confirms + ctx.store.commit; sends contracts.Count), `invoice_read()`.

## D: voice (`aira/voice.py`)
```python
class Transcriber:
    def __init__(self, on_final: Callable[[str], Awaitable[None]], on_partial: Callable[[str], Awaitable[None]] | None = None,
                 keywords: list[str] = (), languages: list[str] | None = None)
    async def start(self) -> None       # connect wss realtime (ENDPOINTING), handshake with key in first frame, wait ack; no-op in mock
    async def push(self, pcm24: bytes) -> None   # queue audio; a pacer sends 80 ms chunks in real time, pads silence
    async def close(self) -> None       # endStream, drain until close 1000
```
Reconnects on 1011 / before the 60-min cap. In mock mode does nothing (phone sends `Text` instead).

## E: server (`aira/main.py`, `aira/session.py`, `aira/events.py`)
FastAPI app `aira.main:app`: `GET /health`, `WS /ws` (phone), `GET /api/snapshot` (products, stock, recent sales, deliveries, last count with boxes), `GET /api/events?after=n` (JSON poll) and `GET /api/stream` (SSE) for the dashboard, `GET /api/frame/{id}` (last count frame JPEG for the overlay). Session = one phone: hello->ready, audio->Transcriber, final transcript or Text -> Agent.turn -> Transcript(aira)+Say, Frame -> resolves pending request_frame futures, ConfirmAnswer -> ConfirmBook.resolve, Stop -> cancel turn + reject_all. CORS open for the dashboard origin. `app_key` checked if set.
