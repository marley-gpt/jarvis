"""Unit-economics calculator per product test (MBB fields and KPI targets, memory M-261106-203e).

Pure functions, no database. Money inputs are USD (the supplier and the store price in USD); AUD is derived with the
config `usd_aud` rate, which is a config rate and not a bank rate, so say so in any report.
"""
from __future__ import annotations

from . import paths

FEE_PCT = 2.9      # Shopify Payments, percent of the sale (the MBB story uses 2.7; the brush calc used 2.9)
FEE_FIXED = 0.30   # per order, USD
KPI = {"aov_cac": 2.0, "gross_margin": 0.60, "profit_margin": 0.20}
MIN_ORDERS = 5     # below this the flags are results so far, not a verdict


def _r(x, n=2):
    return None if x is None else round(x, n)


def flag(ok):
    """'green' / 'red' / 'n/a' (no data to judge)."""
    return "n/a" if ok is None else "green" if ok else "red"


def calc(name, price, landed, shipping=0.0, spend_aud=None, orders=0, *, fee_pct=FEE_PCT, fee_fixed=FEE_FIXED,
         usd_aud=None, weekly_target_aud=None):
    """price/landed/shipping are USD per order. `landed` is the supplier's landed cost (Victor: shipping included),
    `shipping` is any extra shipping on top. spend_aud is the real Meta spend in AUD."""
    cfg = paths.config()
    rate = usd_aud or cfg["usd_aud"]
    target = weekly_target_aud if weekly_target_aud is not None else cfg["weekly_profit_target_aud"]
    if price <= 0:
        raise ValueError("price must be above zero")
    product_cost = landed + shipping
    fee = price * fee_pct / 100 + fee_fixed
    gross = price - product_cost - fee          # profit per order before ads (contribution)
    gross_margin = gross / price
    be_roas = price / gross if gross > 0 else None
    out = {"name": name, "usd_aud": rate, "price": price, "landed": landed, "shipping": shipping,
           "product_cost": _r(product_cost), "fee": _r(fee), "gross_profit": _r(gross),
           "contribution_margin": _r(gross_margin, 4), "break_even_cpa_usd": _r(gross),
           "break_even_cpa_aud": _r(gross * rate), "break_even_roas": _r(be_roas),
           "price_aud": _r(price * rate), "gross_profit_aud": _r(gross * rate), "orders": orders,
           "spend_aud": spend_aud, "weekly_target_aud": target,
           "orders_for_target_at_zero_ad_cost": _r(target / (gross * rate), 1) if gross > 0 else None}
    flags = {"gross_margin": flag(gross_margin >= KPI["gross_margin"])}
    if spend_aud is None or orders <= 0:
        out.update(cac_usd=None, roas=None, aov_cac=None, net_profit_per_order_usd=None, net_profit_usd=None,
                   net_profit_aud=None, profit_margin=None, gap_to_target_aud=None, sufficient=False)
        flags.update(aov_cac="n/a", roas="n/a", net_profit_per_order="n/a", profit_margin="n/a")
    else:
        spend = spend_aud / rate
        cac = spend / orders
        revenue = price * orders
        net = gross * orders - spend
        out.update(spend_usd=_r(spend), cac_usd=_r(cac), roas=_r(revenue / spend) if spend else None,
                   aov_cac=_r(price / cac) if cac else None, net_profit_per_order_usd=_r(net / orders),
                   net_profit_usd=_r(net), net_profit_aud=_r(net * rate), profit_margin=_r(net / revenue, 4),
                   gap_to_target_aud=_r(net * rate - target), sufficient=orders >= MIN_ORDERS)
        flags.update(
            aov_cac=flag(cac == 0 or price / cac >= KPI["aov_cac"]),
            roas=flag(be_roas is not None and spend > 0 and revenue / spend >= be_roas),
            net_profit_per_order=flag(net / orders >= 0),
            profit_margin=flag(net / revenue >= KPI["profit_margin"]))
    out["flags"] = flags
    return out


def lines(c):
    f, au = c["flags"], c["usd_aud"]
    dash = lambda v: "-" if v is None else v  # noqa: E731
    t = [f"Unit economics: {c['name']} (US$1 = A${au}, config rate, not a bank rate)",
         f"  Price US${c['price']:.2f} (A${c['price_aud']:.2f}) | landed US${c['landed']:.2f}"
         + (f" + shipping US${c['shipping']:.2f}" if c["shipping"] else "") + f" | fees US${c['fee']:.2f}",
         f"  Profit per order before ads US${c['gross_profit']:.2f} (A${c['gross_profit_aud']:.2f})",
         f"  Contribution margin {c['contribution_margin'] * 100:.1f}%  [{f['gross_margin']}, target 60%+]",
         f"  Break-even CPA US${c['break_even_cpa_usd']:.2f} (A${c['break_even_cpa_aud']:.2f}) | "
         f"break-even ROAS {dash(c['break_even_roas'])}"]
    if c["spend_aud"] is None or c["orders"] <= 0:
        t.append("  No ad results yet: projections only, no verdict.")
    else:
        t += [f"  Ad spend A${c['spend_aud']:.2f} (US${c['spend_usd']:.2f}), {c['orders']} order(s): "
              f"CAC US${c['cac_usd']:.2f}",
              f"  ROAS {dash(c['roas'])}  [{f['roas']}, needs {dash(c['break_even_roas'])}]",
              f"  AOV:CAC {dash(c['aov_cac'])}  [{f['aov_cac']}, target 2.0+]",
              f"  Net profit per order US${c['net_profit_per_order_usd']:.2f}  [{f['net_profit_per_order']}]",
              f"  Net profit US${c['net_profit_usd']:.2f} (A${c['net_profit_aud']:.2f}), margin "
              f"{c['profit_margin'] * 100:.1f}%  [{f['profit_margin']}, target 20%+]",
              f"  Weekly target A${c['weekly_target_aud']:.0f}: "
              f"{'ahead' if c['gap_to_target_aud'] >= 0 else 'behind'} by A${abs(c['gap_to_target_aud']):.2f}"]
        if not c["sufficient"]:
            t.append(f"  Only {c['orders']} order(s): insufficient data, flags show results so far, not a verdict.")
    if c["orders_for_target_at_zero_ad_cost"]:
        t.append(f"  A${c['weekly_target_aud']:.0f}/week needs at least {c['orders_for_target_at_zero_ad_cost']} orders "
                 "even with zero ad cost; the real number is higher once ads are paid for.")
    return t
