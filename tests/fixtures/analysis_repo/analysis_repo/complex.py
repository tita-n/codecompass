"""Complexity fixtures: one hot function, one calm function."""


def process_order(order: dict) -> float:  # noqa: C901 - deliberately complex
    """Price an order; complexity well above the default threshold of 10."""
    total = 0.0
    items = order.get("items", [])
    if not items:
        return 0.0
    for item in items:
        price = item.get("price", 0)
        qty = item.get("qty", 1)
        if price < 0 or qty <= 0:
            continue
        if item.get("taxed") and order.get("region") == "EU":
            price = price * 1.2
        elif item.get("discount"):
            price = price * 0.9 if qty > 10 else price * 0.95
        for coupon in order.get("coupons", []):
            if coupon == "HALF" and price > 100:
                price = price / 2
            elif coupon == "TEN" or coupon == "PROMO":
                price = price - 10
        try:
            total += price * qty
        except TypeError:
            continue
        total += sum([p for p in [price] if p > 0])
    match order.get("status"):
        case "new":
            total += 1
        case "paid":
            total += 2
        case _:
            total += 0
    while total > 1000:
        total = total - 100
    return total


def greet(name: str) -> str:
    """A calm function: complexity 1."""
    return f"hello {name}"
