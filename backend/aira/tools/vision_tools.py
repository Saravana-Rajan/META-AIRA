"""Camera tools: generic look, two-reader invoice read, and SAM-counted delivery check."""

from aira import contracts
from aira.tools.base import Tool, ToolContext, emit_verification, err, no_results, obj, ok
from aira.vision import invoice as invoice_mod
from aira.vision import look as look_mod
from aira.vision import sam as sam_mod

NO_FRAME = "I could not get a picture from the camera. Please point the phone and try again."


def _rs(n: int | float) -> str:
    return f"{int(n)} rupees"


# ---- camera_ask ----------------------------------------------------------------
async def camera_ask(ctx: ToolContext, question: str) -> dict:
    frame = await ctx.request_frame("ask")
    if frame is None:
        return err(NO_FRAME, retryable=True)
    answer = await look_mod.look(frame, question)
    # Not verified: the agent must not speak numbers from this answer as facts.
    return ok(say_en=answer, answer=answer, verified=False, frame_id=frame.id)


# ---- invoice_read --------------------------------------------------------------
async def invoice_read(ctx: ToolContext) -> dict:
    frame = await ctx.request_frame("invoice")
    if frame is None:
        return err(NO_FRAME, retryable=True)
    r = await invoice_mod.read_invoice(frame)
    await emit_verification(
        ctx,
        "invoice",
        agreed=r.agreed,
        total=r.total if r.agreed else None,
        detail=r.supplier or "",
        note="; ".join(r.disagreements),
    )
    if not r.agreed:
        return no_results(
            "I could not read this invoice reliably, so I will not use it. "
            "Please hold it flat, in good light, and show it again.",
            agreed=False,
            disagreements=r.disagreements,
            frame_id=frame.id,
        )
    parts = [
        f"{x['qty']} {x['name']} at {_rs(x['unit_price'])}, line total {_rs(x['line_total'])}"
        for x in r.lines
    ]
    say = f"Invoice from {r.supplier}: " + "; ".join(parts) + f". Grand total {_rs(r.total or 0)}."
    return ok(
        say_en=say,
        agreed=True,
        supplier=r.supplier,
        lines=r.lines,
        total=r.total,
        frame_id=frame.id,
    )


# ---- delivery_check ------------------------------------------------------------
async def delivery_check(ctx: ToolContext, product: str, expected: int | None = None) -> dict:
    p = await ctx.store.find_product(product or "")
    if p is None:
        return no_results(f"I don't know a product called {product}.", product=product)
    sku, name = p["sku"], p.get("name") or p["sku"]
    if expected is not None and (type(expected) is not int or expected < 0):
        return err("expected must be a whole number of 0 or more")

    frame = await ctx.request_frame("count")
    if frame is None:
        return err(NO_FRAME, retryable=True)
    sc = await sam_mod.count_items(frame, p.get("sam_noun") or name)
    if not sc.ok:
        return err("The counting model did not answer. Please show the items again.", retryable=True, sku=sku)
    counted = sc.count
    await ctx.send(
        contracts.Count(sku=sku, counted=counted, expected=expected, boxes=sc.boxes, frame_id=frame.id)
    )
    if counted == 0:
        return no_results(
            f"I could not see any {name}. Spread them out in good light and try again.",
            sku=sku,
            counted=0,
            expected=expected,
        )

    difference = None if expected is None else counted - expected
    match = expected is None or difference == 0
    if expected is None:
        compare = f"I counted {counted} {name}."
    elif difference == 0:
        compare = f"I counted {counted} {name}, matching the {expected} expected."
    elif difference is not None and difference < 0:
        compare = f"I counted {counted} {name}, but {expected} were expected: {-difference} short."
    else:
        compare = f"I counted {counted} {name}, but {expected} were expected: {difference} extra."
    prompt = f"{compare} Add {counted} {name} to stock? Say yes or no."

    # Capture now: ctx.call_id changes with every tool call, and the committer runs later on "yes".
    idem = ctx.idem_key()
    delivery_id = f"dlv_{idem}"
    facts = {
        "sku": sku,
        "product": name,
        "counted": counted,
        "expected": expected,
        "difference": difference,
        "match": match,
        "delivery_id": delivery_id,
        "frame_id": frame.id,
    }

    async def committer() -> dict:
        # Only the SAM-counted number enters stock.
        done = await ctx.store.commit(
            idem,
            ledger=[
                {
                    "sku": sku,
                    "delta": counted,
                    "reason": "DELIVERY",
                    "ref_type": "delivery",
                    "ref_id": delivery_id,
                    "actor": "owner",
                }
            ],
            docs=[
                (
                    "deliveries",
                    delivery_id,
                    {
                        "id": delivery_id,
                        "sku": sku,
                        "product": name,
                        "counted": counted,
                        "expected": expected,
                        "difference": difference,
                        "frame_id": frame.id,
                        "boxes": [b.model_dump() for b in sc.boxes],
                        "session_id": ctx.session_id,
                    },
                )
            ],
        )
        on_hand = await ctx.store.stock(sku)
        return ok(
            say_en=f"Added {counted} {name} to stock. You now have {on_hand}.",
            **facts,
            committed=True,
            already_done=not done,
            on_hand=on_hand,
        )

    return await ctx.confirms.propose(ctx, kind="delivery", prompt=prompt, facts=facts, committer=committer)


TOOLS: list[Tool] = [
    Tool(
        name="camera_ask",
        description=(
            "Look through the phone camera and answer a question about what is visible "
            "(product, label, colour, where something is). Never use it to count items or read money."
        ),
        parameters=obj(
            {"question": {"type": "string", "description": "The owner's question about the camera view"}},
            ["question"],
        ),
        handler=camera_ask,
    ),
    Tool(
        name="invoice_read",
        description=(
            "Read the supplier invoice in front of the camera with two independent readers. "
            "Numbers are returned only if both readers agree and the bill's arithmetic checks out."
        ),
        parameters=obj({}, []),
        handler=invoice_read,
    ),
    Tool(
        name="delivery_check",
        description=(
            "Count a delivered product in front of the camera with SAM 3.1, compare with the expected "
            "quantity (from the owner or the invoice), and propose adding the counted number to stock. "
            "Needs the owner's yes."
        ),
        parameters=obj(
            {
                "product": {"type": "string", "description": "Product name as the owner said it"},
                "expected": {
                    "type": ["integer", "null"],
                    "description": "Expected quantity, or null if not known",
                },
            },
            ["product", "expected"],
        ),
        handler=delivery_check,
        needs_confirm=True,
    ),
]
