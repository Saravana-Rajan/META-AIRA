"""Generic "what is in front of the camera" answers via reader A (Muse Spark vision)."""
from aira.config import get_settings
from aira.tools.base import FrameData
from aira.vision.readers import ask_image

MOCK_RESULT = "I can see a cricket bat and a box of tennis balls on the counter."

FALLBACK = "Sorry, I could not see that clearly. Please hold the camera steady and ask again."

PROMPT = (
    "You are the eyes of a blind shopkeeper in India. Answer the question about this camera photo "
    "in one or two short spoken sentences, plain words, no markdown.\n"
    "Rules:\n"
    "- Never identify people or guess who someone is; you may only say a person is present.\n"
    "- Never judge whether money is genuine or fake.\n"
    "- Do not state counts, prices, totals or other amounts; if asked, say the count or invoice check "
    "should be used instead. Product names and colours are fine.\n"
    "- If the photo is too dark, blurred or the answer is not visible, say so plainly.\n"
    "Question: "
)


async def look(frame: FrameData, question: str) -> str:
    if get_settings().mock:
        return MOCK_RESULT
    q = (question or "").strip() or "What is in front of the camera?"
    r = await ask_image("A", frame, PROMPT + q)
    return r.text if r.ok and r.text else FALLBACK
