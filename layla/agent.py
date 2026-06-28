"""
Layla — Maryam B. AI Styling & Shopping Concierge (ADK multi-agent system)
============================================================================
This file wires the whole system together. It defines four agents:

    layla_concierge (root / coordinator)
        ├── stylist_agent   — pure-LLM styling advice
        ├── catalog_agent   — backed by the custom Catalog MCP server
        └── order_agent     — cart/checkout function tools, PII-aware

Course concepts demonstrated here:
  • Multi-agent system (ADK)  — coordinator with `sub_agents` delegation.
  • MCP server                — `catalog_agent` consumes the custom MCP server
                                in ../catalog_mcp/server.py via `McpToolset`.
  • Security features         — `before_model_callback` (input guardrail) and
                                `before_tool_callback` (tool guardrail).

ADK auto-discovers the variable named `root_agent` in this module, so
`adk web` / `adk run` / `adk deploy` all "just work" from the project root.
============================================================================
"""

import os

from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from . import prompts, tools
from .guardrails import input_guardrail, tool_guardrail

# A current Gemini flash model — fast and inexpensive, ideal for routing and
# tool use. Swap to "gemini-flash-latest" or a newer flash if you prefer.
MODEL = os.environ.get("LAYLA_MODEL", "gemini-2.5-flash")

# Absolute path to this package, used to launch the MCP server as a subprocess
# from the correct working directory regardless of where ADK is invoked.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Catalog MCP toolset
# ---------------------------------------------------------------------------
# This connects the catalog agent to our custom MCP server over stdio. ADK
# launches `python -m catalog_mcp.server` as a child process and exposes its
# three tools (search_products, get_product, check_size_availability) to the
# agent automatically. This is the "MCP Server" course concept, end to end.
catalog_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="python",
            args=["-m", "catalog_mcp.server"],
            cwd=_PROJECT_ROOT,
        ),
        timeout=30,  # seconds; raise if your server cold-starts slowly
    ),
)


# ---------------------------------------------------------------------------
# Specialist sub-agents
# ---------------------------------------------------------------------------
stylist_agent = Agent(
    name="stylist_agent",
    model=MODEL,
    description="Gives styling, colour, fabric and occasion advice for Maryam B. wear.",
    instruction=prompts.STYLIST_INSTRUCTION,
)

catalog_agent = Agent(
    name="catalog_agent",
    model=MODEL,
    description="Finds Maryam B. products, prices and availability via the catalog MCP server.",
    instruction=prompts.CATALOG_INSTRUCTION,
    tools=[catalog_toolset],
    # The tool guardrail also guards MCP tool calls (defence in depth):
    before_tool_callback=tool_guardrail,
)

order_agent = Agent(
    name="order_agent",
    model=MODEL,
    description="Manages the cart, checkout and order status. Handles PII carefully.",
    instruction=prompts.ORDER_INSTRUCTION,
    tools=[
        tools.add_to_cart,
        tools.view_cart,
        tools.place_order,
        tools.get_order_status,
    ],
    # SECURITY: every order/cart tool call is screened for confused-deputy and
    # exfiltration attempts before it runs.
    before_tool_callback=tool_guardrail,
)


# ---------------------------------------------------------------------------
# Root coordinator
# ---------------------------------------------------------------------------
# `sub_agents` makes this a hierarchical multi-agent system: the coordinator
# decides which specialist should handle each turn and transfers control to it.
root_agent = Agent(
    name="layla_concierge",
    model=MODEL,
    description="Layla, the Maryam B. personal styling and shopping concierge.",
    instruction=prompts.COORDINATOR_INSTRUCTION,
    sub_agents=[stylist_agent, catalog_agent, order_agent],
    # SECURITY: screen every inbound user turn for prompt injection / jailbreak
    # before the model is even called.
    before_model_callback=input_guardrail,
)
