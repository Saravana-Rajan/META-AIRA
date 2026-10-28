"""SAM 3.1 counting: token parsing, confidence filter, request shape. Offline."""
from types import SimpleNamespace

import pytest

from aira.config import Settings
from aira.tools.base import FrameData
from aira.vision import sam

FRAME = FrameData(id="f1", jpeg=b"\xff\xd8jpeg", w=640, h=480)

MASK = "<|mask;x=0;y=0;data=2,2,!abc|>"
TEXT = (
    "<0f>0<|box;x1=10;y1=20;x2=30;y2=40;w=640;h=480;c=0.96|>" + MASK
    + ",1<|box;x1=50;y1=60;x2=70;y2=80;w=640;h=480;c=0.30|>" + MASK
    + ",5<|box;x1=100;y1=100;x2=150;y2=160;w=640;h=480|>" + MASK
)


class FakeResponses:
    def __init__(self, text="", exc=None):
        self.text, self.exc, self.calls = text, exc, []

    async def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(output_text=self.text)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(sam, "get_settings", lambda: Settings(aira_mock=False, model_api_key="k"))

    def install(text="", exc=None):
        fake = FakeResponses(text, exc)
        monkeypatch.setattr(sam, "meta_client", lambda: SimpleNamespace(responses=fake))
        return fake

    return install


def test_empty_output_is_zero():
    assert sam.parse_sam_output("") == []
    r = sam.count_boxes("   ")
    assert r.ok and r.count == 0 and r.boxes == []


def test_package_parse_and_confidence_filter():
    assert len(sam.parse_sam_output(TEXT)) == 3
    r = sam.count_boxes(TEXT, min_conf=0.5)
    # 0.96 counts, 0.30 is filtered out, a missing confidence (None) counts.
    assert r.count == 2
    first = r.boxes[0]
    assert (first.left, first.top, first.right, first.bottom) == (10, 20, 31, 41)
    assert first.score == pytest.approx(0.96)
    assert r.boxes[1].score is None
    assert sam.count_boxes(TEXT, min_conf=0.2).count == 3


def test_fallback_parser_box_only_tokens():
    text = "<0f>0<|box;x1=1;y1=2;x2=3;y2=4;w=10;h=10;c=0.9|>,1<|box;x1=5;y1=5;x2=8;y2=9;w=10;h=10;c=0.1|>"
    # No mask tokens: the package rejects the line; our token parser still reads the boxes.
    r = sam.count_boxes(text, min_conf=0.5)
    assert r.count == 1
    assert (r.boxes[0].left, r.boxes[0].right) == (1, 4)


def test_fallback_when_package_unavailable(monkeypatch):
    monkeypatch.setattr(sam, "_parse_with_package", lambda text: None)
    assert sam.count_boxes(TEXT, min_conf=0.5).count == 2


async def test_count_items_request_shape(live):
    fake = live(TEXT)
    r = await sam.count_items(FRAME, "tennis ball", min_conf=0.5)
    assert r.ok and r.count == 2
    kw = fake.calls[0]
    assert kw["model"] == "sam-3.1"
    assert kw["metadata"] == {"include_confidence": "true", "mask_encoding": "one_bit"}
    msg = kw["input"][0]
    assert msg["type"] == "message" and msg["role"] == "user"
    assert msg["content"][0] == {"type": "input_text", "text": "tennis ball"}
    assert msg["content"][1]["type"] == "input_image"
    assert msg["content"][1]["image_url"].startswith("data:image/jpeg;base64,")


async def test_count_items_zero_matches(live):
    live("")
    r = await sam.count_items(FRAME, "cricket ball")
    assert r.ok and r.count == 0


async def test_count_items_api_error(live):
    live(exc=RuntimeError("boom"))
    r = await sam.count_items(FRAME, "cricket ball")
    assert not r.ok and r.count == 0 and "boom" in r.note


async def test_mock_mode(monkeypatch):
    monkeypatch.setattr(sam, "get_settings", lambda: Settings(aira_mock=True))
    r = await sam.count_items(FRAME, "anything")
    assert r is sam.MOCK_RESULT and r.count == 10 and len(r.boxes) == 10
