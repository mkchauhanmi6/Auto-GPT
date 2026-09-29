"""Performance summary of the paper account."""

from __future__ import annotations

from collections import defaultdict

from .brokers.paper import PaperBroker
from .brokers.position import Position


def summarize(broker: PaperBroker) -> dict:
    closed = sorted(broker.closed_positions(), key=lambda p: p.exit_time or "")
    pnls = [p.realized_pnl or 0.0 for p in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for x in pnls:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    by_strategy: dict[str, list[float]] = defaultdict(list)
    for p in closed:
        by_strategy[p.strategy].append(p.realized_pnl or 0.0)
    return {
        "capital": broker.capital,
        "equity": broker.equity(),
        "realized_pnl": sum(pnls),
        "open_mtm": sum(p.last_mtm for p in broker.open_positions()),
        "open_positions": len(broker.open_positions()),
        "trades": len(closed),
        "win_rate": len(wins) / len(closed) if closed else 0.0,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "charges": sum(p.entry_charges + p.exit_charges for p in closed),
        "max_drawdown": max_dd,
        "by_strategy": {k: {"trades": len(v), "pnl": sum(v)} for k, v in by_strategy.items()},
    }


def format_summary(s: dict) -> str:
    pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "-"
    lines = [
        f"Capital      Rs {s['capital']:>12,.0f}",
        f"Equity       Rs {s['equity']:>12,.0f}",
        f"Realized P&L Rs {s['realized_pnl']:>12,.0f}  (charges Rs {s['charges']:,.0f})",
        f"Open MTM     Rs {s['open_mtm']:>12,.0f}  ({s['open_positions']} open)",
        f"Trades {s['trades']} | win rate {s['win_rate']:.0%} | avg win Rs {s['avg_win']:,.0f} | "
        f"avg loss Rs {s['avg_loss']:,.0f} | profit factor {pf} | max drawdown Rs {s['max_drawdown']:,.0f}",
    ]
    for name, v in sorted(s["by_strategy"].items()):
        lines.append(f"  {name:<18} {v['trades']:>3} trades  Rs {v['pnl']:>10,.0f}")
    return "\n".join(lines)


def format_position(p: Position) -> str:
    legs = " / ".join(
        f"{leg.side.value[0]} {leg.lots}x {leg.contract.symbol} @{leg.price:.2f}" for leg in p.legs
    )
    if p.is_open:
        tail = f"MTM Rs {p.last_mtm:,.0f} | SL {p.stop_loss:,.0f} | TGT {p.target:,.0f}"
    else:
        tail = f"P&L Rs {p.realized_pnl:,.0f} ({p.exit_reason})"
    return f"[{p.id}] {p.status:<6} {p.underlying} {p.strategy}: {legs} | {tail}"
