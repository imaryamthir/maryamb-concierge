"""
Local function tools for the Order agent
============================================================================
These are ordinary ADK FunctionTools (plain Python functions). They model the
cart / checkout side of the concierge, which is where personal data lives.

Design note for the security story:
  `place_order` takes `delivery_channel` as a SEPARATE, explicit argument
  rather than letting the model bury a destination inside free text. That
  single design choice is what makes the `tool_guardrail` in guardrails.py
  effective — the guardrail can validate the destination against an allow-list
  before the order is ever placed. Security is a property of the *interface*,
  not just the prompt.

State is kept in a module-level dict for the demo. In production this would be
a per-session store (ADK session state) or a real orders service.
============================================================================
"""

from typing import Any

# Simple in-memory cart keyed by a session/user id. Demo-only.
_CARTS: dict[str, list[dict[str, Any]]] = {}


def add_to_cart(user_id: str, product_id: str, size: str, quantity: int = 1) -> dict[str, Any]:
    """Add an item to the shopper's cart.

    Args:
        user_id: identifier for the current shopper's cart.
        product_id: catalog ID, e.g. "MB-FRM-014".
        size: requested size, e.g. "M".
        quantity: number of units (defaults to 1).
    """
    cart = _CARTS.setdefault(user_id, [])
    cart.append({"product_id": product_id.upper(), "size": size.upper(), "quantity": max(1, quantity)})
    return {"status": "ok", "cart_size": len(cart), "cart": cart}


def view_cart(user_id: str) -> dict[str, Any]:
    """Return the current contents of the shopper's cart."""
    return {"status": "ok", "cart": _CARTS.get(user_id, [])}


def place_order(user_id: str, delivery_channel: str = "saved_address") -> dict[str, Any]:
    """Place the order currently in the cart.

    Args:
        user_id: identifier for the current shopper.
        delivery_channel: WHERE the order ships. Must be one of the channels
            the security policy allows ("saved_address", "in_app",
            "store_pickup"). The `tool_guardrail` validates this argument
            *before* this function runs, which is the confused-deputy defence.
    """
    cart = _CARTS.get(user_id, [])
    if not cart:
        return {"status": "empty_cart", "message": "There is nothing to order yet."}

    # NOTE: we deliberately do NOT echo the customer's address/phone here.
    # The order is dispatched to the channel on file; PII never round-trips
    # through the model. The guardrail has already vetted `delivery_channel`.
    order_id = f"MB-ORD-{abs(hash((user_id, len(cart)))) % 100000:05d}"
    _CARTS[user_id] = []  # clear the cart after ordering
    return {
        "status": "confirmed",
        "order_id": order_id,
        "items": cart,
        "delivery_channel": delivery_channel,
        "message": "Order confirmed. Details were sent to your saved contact on file.",
    }


def get_order_status(order_id: str) -> dict[str, Any]:
    """Return a (mock) fulfilment status for an order id."""
    # Deterministic mock so demos are reproducible.
    states = ["packing", "dispatched", "out_for_delivery", "delivered"]
    state = states[abs(hash(order_id)) % len(states)]
    return {"order_id": order_id, "status": state}
