"""Tool registry: every function the agent can call."""
from aira.tools import shop, vision_tools
from aira.tools.base import Tool

ALL_TOOLS: list[Tool] = [*shop.TOOLS, *vision_tools.TOOLS]
BY_NAME: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}
