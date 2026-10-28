"""SAM 3.1 counting via the Meta Model API Responses endpoint (PLAN 8.3).

One request per noun phrase. The answer is special-token text in `output_text` (empty = zero matches),
parsed with meta_sam_parser. Count = box records with confidence >= min_conf (None confidence counts).
"""
import asyncio
import base64
import re
from dataclasses import dataclass, field

from aira import contracts
from aira.config import get_settings
from aira.meta_client import meta_client
from aira.tools.base import FrameData

SAM_TIMEOUT_S = 30.0


@dataclass
class SamCount:
    count: int
    boxes: list[contracts.Box] = field(default_factory=list)
    ok: bool = True
    note: str = ""


def _mock_boxes(n: int) -> list[contracts.Box]:
    boxes = []
    for i in range(n):
        col, row = i % 5, i // 5
        left, top = 40 + col * 120, 60 + row * 140
        boxes.append(contracts.Box(left=left, top=top, right=left + 100, bottom=top + 110, score=0.9))
    return boxes


# Tests may override this. Returned as-is in mock mode.
MOCK_RESULT = SamCount(count=10, boxes=_mock_boxes(10), ok=True, note="mock")

_BOX_TOKEN = re.compile(r"<\|box;([^|]*)\|>")


def parse_box_tokens(text: str) -> list[tuple[contracts.Box, float | None]]:
    """Fallback parser: every `<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|>` token.

    x2/y2 are inclusive in the API; we return exclusive right/bottom like meta_sam_parser does.
    """
    out: list[tuple[contracts.Box, float | None]] = []
    for m in _BOX_TOKEN.finditer(text or ""):
        fields: dict[str, str] = {}
        for part in m.group(1).split(";"):
            k, sep, v = part.partition("=")
            if sep:
                fields[k.strip()] = v.strip()
        try:
            x1, y1, x2, y2 = (int(float(fields[k])) for k in ("x1", "y1", "x2", "y2"))
        except (KeyError, ValueError):
            continue
        conf: float | None
        try:
            conf = float(fields["c"]) if "c" in fields else None
        except ValueError:
            conf = None
        if conf is not None and not 0.0 <= conf <= 1.0:
            conf = None
        out.append((contracts.Box(left=x1, top=y1, right=x2 + 1, bottom=y2 + 1, score=conf), conf))
    return out


def _parse_with_package(text: str) -> list[tuple[contracts.Box, float | None]] | None:
    """Parse with meta_sam_parser. None if the package is unavailable or its API differs."""
    try:
        from meta_sam_parser import CompletedOutcome, image_segmentation_format

        parser = image_segmentation_format().create_parser()
        parser.push(text)
        records = parser.finish(CompletedOutcome()).result.records
    except Exception:  # noqa: BLE001 - fall back to our own token parser
        return None
    out: list[tuple[contracts.Box, float | None]] = []
    for r in records:
        if getattr(r, "kind", None) != "box":
            continue
        conf = getattr(r, "confidence", None)
        box = contracts.Box(
            left=int(r.left), top=int(r.top), right=int(r.right), bottom=int(r.bottom), score=conf
        )
        out.append((box, conf))
    return out


def parse_sam_output(text: str) -> list[tuple[contracts.Box, float | None]]:
    """All box detections in a SAM output_text (unfiltered). Empty text = zero matches."""
    if not text or not text.strip():
        return []
    parsed = _parse_with_package(text)
    # The package drops a whole line on one malformed record; if it found no boxes but box tokens
    # are present, read the box tokens directly.
    if not parsed and "<|box" in text:
        return parse_box_tokens(text)
    return parsed or []


def count_boxes(text: str, min_conf: float = 0.5) -> SamCount:
    boxes = [b for b, c in parse_sam_output(text) if c is None or c >= min_conf]
    return SamCount(count=len(boxes), boxes=boxes, ok=True)


async def count_items(frame: FrameData, noun: str, min_conf: float = 0.5) -> SamCount:
    """Count instances of ONE short noun phrase in the frame."""
    s = get_settings()
    if s.mock:
        return MOCK_RESULT
    noun = (noun or "").strip()
    if not noun:
        return SamCount(count=0, boxes=[], ok=False, note="no noun phrase")
    url = "data:image/jpeg;base64," + base64.b64encode(frame.jpeg).decode("ascii")
    try:
        resp = await asyncio.wait_for(
            meta_client().responses.create(
                model=s.model_for("sam"),
                input=[
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": noun},
                            {"type": "input_image", "image_url": url},
                        ],
                    }
                ],
                metadata={"include_confidence": "true", "mask_encoding": "one_bit"},
            ),
            SAM_TIMEOUT_S,
        )
    except TimeoutError:
        return SamCount(count=0, boxes=[], ok=False, note="timeout")
    except Exception as e:  # noqa: BLE001
        return SamCount(count=0, boxes=[], ok=False, note=f"{type(e).__name__}: {e}")
    text = getattr(resp, "output_text", "") or ""
    return count_boxes(text, min_conf)
