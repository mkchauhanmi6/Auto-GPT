"""Command line interface: ``python -m optrade <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .brokers.paper import PaperBroker
from .brokers.sensibull import SensibullTickets, signal_ticket
from .config import Settings
from .data import ScripMaster, SimulatedMarketData, make_data_source
from .engine import TradingEngine
from .notify import telegram_notifier
from .report import format_position, format_summary, summarize
from .strategy import SignalGenerator

DISCLAIMER = (
    "Paper trading only. Signals are rules-based and educational, not investment advice; "
    "options trading carries substantial risk of loss."
)


def _broker(settings: Settings, data, state_name: str = "paper_account.json") -> PaperBroker:
    return PaperBroker(settings.home / state_name, data, settings.costs, settings.risk.capital)


def cmd_signal(args, settings: Settings) -> int:
    data = make_data_source(args.data, settings.home, args.underlying)
    gen = SignalGenerator(data, settings.strategy, settings.risk)
    for u in args.underlying:
        sig = gen.generate(u)
        if args.json:
            print(json.dumps(sig.to_dict(), indent=2, default=str))
        else:
            print(signal_ticket(sig, settings.risk.square_off_time if settings.risk.intraday else None))
            if not sig.is_trade and len(sig.rationale) > 1:
                print("  details: " + "; ".join(sig.rationale[1:]))
            print()
    print(DISCLAIMER)
    return 0


def _mirrors(args, settings: Settings) -> list:
    mirrors = [
        SensibullTickets(
            settings.home / "sensibull_tickets.txt",
            square_off=settings.risk.square_off_time if settings.risk.intraday else None,
            notify=telegram_notifier(),
            show_no_trade=not args.quiet,
        )
    ]
    if args.dhan_sandbox:
        from .brokers.dhan_sandbox import DhanSandboxMirror

        mirrors.append(
            DhanSandboxMirror(
                ScripMaster(settings.home / "cache"),
                base_url=settings.dhan.base_url,
                product_type=settings.dhan.product_type,
            )
        )
    return mirrors


def cmd_run(args, settings: Settings) -> int:
    data = make_data_source(args.data, settings.home, args.underlying)
    broker = _broker(settings, data)
    engine = TradingEngine(
        data,
        broker,
        SignalGenerator(data, settings.strategy, settings.risk),
        settings.risk,
        mirrors=_mirrors(args, settings),
        journal_file=settings.home / "journal.jsonl",
        ignore_market_hours=args.ignore_market_hours,
    )
    print(DISCLAIMER)
    try:
        engine.run([u.upper() for u in args.underlying], every_seconds=args.every, max_ticks=args.ticks)
    except KeyboardInterrupt:
        print("\nStopped.")
    print(format_summary(summarize(broker)))
    return 0


def cmd_simulate(args, settings: Settings) -> int:
    """Fast-forward the full loop over synthetic market days (a sanity check, not a backtest)."""
    underlyings = [u.upper() for u in args.underlying]
    data = SimulatedMarketData(underlyings, history_days=10, future_days=args.days, seed=args.seed)
    broker = PaperBroker(settings.home / "sim" / "paper_account.json", data, settings.costs, settings.risk.capital)
    broker.reset(settings.risk.capital)
    lines: list[str] = []
    echo = print if args.verbose_trades else lines.append
    engine = TradingEngine(
        data,
        broker,
        SignalGenerator(data, settings.strategy, settings.risk),
        settings.risk,
        mirrors=[SensibullTickets(settings.home / "sim" / "tickets.txt", echo=echo, show_no_trade=False)],
        journal_file=settings.home / "sim" / "journal.jsonl",
        echo=echo,
    )
    step = max(1, settings.strategy.candle_minutes // 5)
    while True:
        engine.tick(underlyings)
        if not data.advance(step):
            break
    engine.manage_exits(data.now())
    for p in broker.positions:
        print(format_position(p))
    print()
    print(format_summary(summarize(broker)))
    print("\nSimulated data - use this to check the machinery, not to judge the strategy.")
    return 0


def cmd_positions(args, settings: Settings) -> int:
    broker = PaperBroker(settings.home / "paper_account.json", None, settings.costs, settings.risk.capital)  # type: ignore[arg-type]
    positions = broker.positions if args.all else broker.open_positions()
    if not positions:
        print("No positions." if args.all else "No open positions.")
    for p in positions:
        print(format_position(p))
    return 0


def cmd_report(args, settings: Settings) -> int:
    broker = PaperBroker(settings.home / "paper_account.json", None, settings.costs, settings.risk.capital)  # type: ignore[arg-type]
    print(format_summary(summarize(broker)))
    return 0


def cmd_close(args, settings: Settings) -> int:
    data = make_data_source(args.data, settings.home, ["NIFTY"])
    broker = _broker(settings, data)
    targets = broker.open_positions() if args.id == "all" else [broker.get(args.id)]
    mirrors = _mirrors(args, settings)
    for p in targets:
        broker.close(p, "MANUAL")
        for m in mirrors:
            m.on_exit(p)
        broker.save()
    return 0


def cmd_reset(args, settings: Settings) -> int:
    capital = args.capital or settings.risk.capital
    broker = PaperBroker(settings.home / "paper_account.json", None, settings.costs, capital)  # type: ignore[arg-type]
    broker.reset(capital)
    print(f"Paper account reset with capital Rs {capital:,.0f}")
    return 0


def cmd_init_config(args, settings: Settings) -> int:
    if settings.config_file.exists() and not args.force:
        print(f"{settings.config_file} already exists (use --force to overwrite)")
        return 1
    settings.save()
    print(f"Wrote {settings.config_file} - edit it to change capital, risk and strategy settings.")
    return 0


def cmd_dhan_check(args, settings: Settings) -> int:
    from .brokers.dhan_sandbox import DhanSandboxMirror

    m = DhanSandboxMirror(ScripMaster(settings.home / "cache"), base_url=settings.dhan.base_url)
    print(json.dumps(m.funds(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="optrade", description="Options signals + automatic paper trading (NSE)")
    p.add_argument("--home", type=Path, help="state/config directory (default ~/.optrade or $OPTRADE_HOME)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp, data=True, underlying=True):
        if data:
            sp.add_argument("--data", default="yahoo", choices=["yahoo", "kite", "sim"], help="market data source")
        if underlying:
            sp.add_argument("-u", "--underlying", nargs="+", default=["NIFTY"], help="NIFTY BANKNIFTY FINNIFTY MIDCPNIFTY")

    sp = sub.add_parser("signal", help="print the current signal(s) - no orders")
    add_common(sp)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_signal)

    sp = sub.add_parser("run", help="auto paper-trade signals in a loop during market hours")
    add_common(sp)
    sp.add_argument("--every", type=int, default=300, help="seconds between checks (default 300)")
    sp.add_argument("--ticks", type=int, help="stop after N checks")
    sp.add_argument("--dhan-sandbox", action="store_true", help="also place every order in the Dhan sandbox")
    sp.add_argument("--ignore-market-hours", action="store_true", help="trade outside 09:15-15:30 (testing)")
    sp.add_argument("--quiet", action="store_true", help="don't print NO TRADE lines")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("simulate", help="fast-forward the engine on synthetic data")
    add_common(sp, data=False)
    sp.add_argument("--days", type=int, default=10)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--verbose-trades", action="store_true", help="print every ticket")
    sp.set_defaults(func=cmd_simulate)

    sp = sub.add_parser("positions", help="list paper positions")
    sp.add_argument("--all", action="store_true", help="include closed positions")
    sp.set_defaults(func=cmd_positions)

    sp = sub.add_parser("report", help="paper account performance")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("close", help="close a paper position (or 'all') at market")
    sp.add_argument("id")
    add_common(sp, underlying=False)
    sp.add_argument("--dhan-sandbox", action="store_true")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_close)

    sp = sub.add_parser("reset", help="wipe the paper account")
    sp.add_argument("--capital", type=float)
    sp.set_defaults(func=cmd_reset)

    sp = sub.add_parser("init-config", help="write an editable config.json with the defaults")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init_config)

    sp = sub.add_parser("dhan-check", help="verify Dhan sandbox credentials (shows sandbox funds)")
    sp.set_defaults(func=cmd_dhan_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.load(args.home)
    try:
        return args.func(args, settings)
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
