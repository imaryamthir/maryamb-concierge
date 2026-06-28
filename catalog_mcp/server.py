"""
Maryam B. Catalog MCP Server
============================================================================
This is a *custom* Model Context Protocol (MCP) server. It exposes the
Maryam B. product catalog to any MCP-aware client as a small set of
well-typed tools. The ADK Catalog agent connects to this server over
stdio (see ../layla/agent.py) and the tools below show up to the agent as
native ADK tools via `McpToolset`.

Why a separate MCP server instead of plain Python functions?
  - It demonstrates the "MCP Server" course concept as a real, standalone
    process with a protocol boundary (not just an in-process function).
  - The same server can be reused by other clients (Claude Desktop, Gemini
    CLI, a second agent) without copying business logic.
  - The protocol boundary is also a *security* boundary: the server decides
    exactly which fields leave the catalog. Notice `_public_view()` below —
    internal stock numbers are summarised, never dumped wholesale, so a
    compromised or confused agent cannot exfiltrate raw inventory data.

Run standalone for debugging:
    python -m catalog_mcp.server      # speaks MCP over stdio
============================================================================
"""

import json
from pathlib import Path
from typing import Any

# FastMCP is the high-level server helper from the official `mcp` package.
# `pip install mcp` provides it.
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Load the catalog once at import time. In production this would be a database
# or the live Maryam B. commerce API; a JSON file keeps the demo self-contained.
# ---------------------------------------------------------------------------
_CATALOG_PATH = Path(__file__).parent / "catalog.json"
_PRODUCTS: list[dict[str, Any]] = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))["products"]

# The MCP server instance. The name is what clients see during discovery.
mcp = FastMCP("maryamb-catalog")


def _public_view(product: dict[str, Any]) -> dict[str, Any]:
    """Return only customer-safe fields for a product.

    SECURITY: raw per-size stock counts are an internal business signal.
    Instead of returning the `stock` dict, we expose a derived, low-resolution
    `availability` map (in_stock / low_stock / sold_out). This is "least
    privilege" applied at the data layer: even if an attacker fully controls
    the agent's prompt, the tool physically cannot leak exact inventory.
    """
    availability = {}
    for size, qty in product.get("stock", {}).items():
        if qty <= 0:
            availability[size] = "sold_out"
        elif qty <= 3:
            availability[size] = "low_stock"
        else:
            availability[size] = "in_stock"

    return {
        "id": product["id"],
        "name": product["name"],
        "category": product["category"],
        "occasion": product["occasion"],
        "color": product["color"],
        "fabric": product["fabric"],
        "price_pkr": product["price_pkr"],
        "availability": availability,
        "description": product["description"],
    }


@mcp.tool()
def search_products(
    query: str = "",
    category: str = "",
    occasion: str = "",
    max_price_pkr: int = 0,
) -> list[dict[str, Any]]:
    """Search the Maryam B. catalog.

    Args:
        query: free-text matched against name, fabric, colour and description.
        category: optional exact filter, e.g. "lawn", "formal", "pret", "bridal".
        occasion: optional filter, e.g. "wedding", "casual", "party", "eid".
        max_price_pkr: optional upper price bound in PKR; 0 means no limit.

    Returns:
        A list of customer-safe product summaries (at most 8) ranked by how
        well they match the query.
    """
    q = query.lower().strip()
    cat = category.lower().strip()
    occ = occasion.lower().strip()

    results = []
    for p in _PRODUCTS:
        # Apply the structured filters first (cheap, exact).
        if cat and p["category"].lower() != cat:
            continue
        if occ and occ not in [o.lower() for o in p["occasion"]]:
            continue
        if max_price_pkr and p["price_pkr"] > max_price_pkr:
            continue

        # Then a simple relevance score over the free-text query.
        haystack = " ".join(
            [p["name"], p["fabric"], p["description"], *p["color"], *p["occasion"]]
        ).lower()
        score = sum(1 for term in q.split() if term in haystack) if q else 1
        if q and score == 0:
            continue

        results.append((score, _public_view(p)))

    results.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in results[:8]]


@mcp.tool()
def get_product(product_id: str) -> dict[str, Any]:
    """Fetch one product by its catalog ID (e.g. "MB-FRM-014").

    Returns a customer-safe product summary, or an error dict if not found.
    """
    pid = product_id.strip().upper()
    for p in _PRODUCTS:
        if p["id"].upper() == pid:
            return _public_view(p)
    return {"error": f"No product found with id '{product_id}'."}


@mcp.tool()
def check_size_availability(product_id: str, size: str) -> dict[str, Any]:
    """Check whether a specific size of a product can be ordered.

    Returns a coarse status ("in_stock" / "low_stock" / "sold_out") rather than
    an exact quantity, for the same least-privilege reason described above.
    """
    pid = product_id.strip().upper()
    sz = size.strip().upper()
    for p in _PRODUCTS:
        if p["id"].upper() == pid:
            view = _public_view(p)
            status = view["availability"].get(sz)
            if status is None:
                return {"product_id": pid, "size": sz, "status": "size_not_offered"}
            return {"product_id": pid, "size": sz, "status": status}
    return {"error": f"No product found with id '{product_id}'."}


if __name__ == "__main__":
    # Speak the MCP protocol over stdio. The ADK client launches this module
    # as a subprocess and communicates through stdin/stdout.
    mcp.run()
