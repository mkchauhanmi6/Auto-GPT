import sys
import types
from datetime import date, datetime

import pytest

from optrade.brokers.dhan_sandbox import DhanSandboxMirror
from optrade.brokers.paper import InsufficientMargin, PaperBroker
from optrade.brokers.sensibull import SensibullTickets, signal_ticket
from optrade.cli import main
from optrade.config import CostConfig, RiskConfig, StrategyConfig
from optrade.costs import order_charges
from optrade.data.simulated import BARS_PER_DAY, SimulatedMarketData
from optrade.engine import TradingEngine
from optrade.models import Leg, OptionContract, OptionType, Side, Signal, SignalAction
from optrade.strategy import MarketView, SignalGenerator
from optrade.timeutil import IST


@pytest.fixture
def sim():
    return SimulatedMarketData(["NIFTY", "BANKNIFTY"], seed=3)


def _signal(sim, legs, stop=-2000.0, target=3000.0, max_loss=10_000.0):
    return Signal(
        action=SignalAction.ENTER,
        underlying="NIFTY",
        strategy="Test",
        spot=sim.spot("NIFTY"),
        legs=legs,
        stop_loss=stop,
        target=target,
        max_loss=max_loss,
        created_at=sim.now(),
    )


def _atm_call(sim, lots=1):
    expiry = sim.expiries("NIFTY")[1]
    strike = round(sim.spot("NIFTY") / 50) * 50
    return Leg(OptionContract("NIFTY", expiry, strike, OptionType.CE), Side.BUY, lots, 65)


def test_charges_include_stt_on_sell_only():
    cfg = CostConfig()
    buy = order_charges(Side.BUY, 100, 65, cfg)
    sell = order_charges(Side.SELL, 100, 65, cfg)
    assert sell - buy == pytest.approx(6500 * 0.001 - 6500 * 0.00003, abs=0.02)


def test_generator_emits_valid_signals_on_sim(sim):
    gen = SignalGenerator(sim, StrategyConfig(), RiskConfig())
    seen = set()
    for _ in range(400):
        sig = gen.generate("NIFTY")
        if sig.is_trade:
            seen.add(sig.strategy)
            assert sig.max_loss and sig.max_loss > 0
            assert sig.stop_loss < 0 < sig.target
            assert sig.max_loss <= RiskConfig().capital * 0.05 + 1
            assert all(leg.contract.expiry > sim.now().date() for leg in sig.legs)
            assert "Sensibull" in signal_ticket(sig)
        if not sim.advance():
            break
    assert seen, "expected at least one trade signal"


def _view(bias, strength, regime):
    return MarketView("NIFTY", 25000, 0, bias, strength, 0.15, 0.12, 1.25, regime)


@pytest.mark.parametrize(
    "bias,strength,regime,expected",
    [
        ("BULLISH", "STRONG", "CHEAP", "Long CE"),
        ("BULLISH", "MILD", "FAIR", "Bull Call Spread"),
        ("BULLISH", "STRONG", "RICH", "Bull Put Spread"),
        ("BEARISH", "STRONG", "FAIR", "Long PE"),
        ("BEARISH", "MILD", "CHEAP", "Bear Put Spread"),
        ("BEARISH", "MILD", "RICH", "Bear Call Spread"),
        ("NEUTRAL", "", "RICH", "Iron Condor"),
    ],
)
def test_decision_matrix(sim, bias, strength, regime, expected):
    gen = SignalGenerator(sim, StrategyConfig(), RiskConfig())
    assert gen._choose_structure(_view(bias, strength, regime))[0] == expected


def test_neutral_cheap_is_no_trade(sim):
    gen = SignalGenerator(sim, StrategyConfig(), RiskConfig())
    assert gen._choose_structure(_view("NEUTRAL", "", "CHEAP")) is None


def test_paper_open_close_roundtrip(sim, tmp_path):
    broker = PaperBroker(tmp_path / "acct.json", sim, CostConfig(), 500_000)
    pos = broker.open(_signal(sim, [_atm_call(sim)]))
    assert pos.entry_charges > 0 and pos.legs[0].price > 0
    closed = broker.close(pos, "MANUAL")
    # immediate round trip loses the spread + slippage + charges
    assert closed.realized_pnl < 0
    reloaded = PaperBroker(tmp_path / "acct.json", sim, CostConfig(), 1)
    assert reloaded.capital == 500_000 and reloaded.positions[0].status == "CLOSED"


def test_margin_check(sim, tmp_path):
    broker = PaperBroker(tmp_path / "acct.json", sim, CostConfig(), 5_000)
    with pytest.raises(InsufficientMargin):
        broker.open(_signal(sim, [_atm_call(sim)], max_loss=10_000))


def test_expired_leg_settles_at_intrinsic(sim, tmp_path):
    broker = PaperBroker(tmp_path / "acct.json", sim, CostConfig(), 500_000)
    past = OptionContract("NIFTY", date(2025, 1, 7), 1000, OptionType.CE)  # deep ITM, long expired
    pos = broker.open(_signal(sim, [Leg(_atm_call(sim).contract, Side.BUY, 1, 65)]))
    pos.legs[0] = Leg(past, Side.BUY, 1, 65, 0.0)
    broker.mark(pos)
    assert pos.last_prices[0] == pytest.approx(sim.spot("NIFTY") - 1000)


def _engine(sim, tmp_path, **risk):
    broker = PaperBroker(tmp_path / "acct.json", sim, CostConfig(), 500_000)
    rc = RiskConfig(**risk)
    return TradingEngine(sim, broker, SignalGenerator(sim, StrategyConfig(), rc), rc, echo=lambda *_: None)


def test_engine_stop_loss_and_target(sim, tmp_path):
    eng = _engine(sim, tmp_path)
    pos = eng.broker.open(_signal(sim, [_atm_call(sim)], stop=-1.0, target=1e9))
    now = datetime.combine(sim.now().date(), datetime.min.time(), IST).replace(hour=11)
    assert eng.exit_reason(pos, -5.0, now) == "STOP_LOSS"
    assert eng.exit_reason(pos, 2e9, now) == "TARGET"
    assert eng.exit_reason(pos, 0.0, now) is None
    assert eng.exit_reason(pos, 0.0, now.replace(hour=15, minute=16)) == "SQUARE_OFF"


def test_engine_entry_gates(sim, tmp_path):
    eng = _engine(sim, tmp_path, max_open_positions=1)
    day = sim.now().date()
    late = datetime(day.year, day.month, day.day, 14, 50, tzinfo=IST)
    assert "no new entries" in eng.entry_block_reason("NIFTY", late)
    eng.broker.open(_signal(sim, [_atm_call(sim)]))
    noon = late.replace(hour=12)
    assert eng.entry_block_reason("NIFTY", noon) == "max open positions reached"


def test_engine_full_day_flat_at_close(sim, tmp_path):
    eng = _engine(sim, tmp_path)
    for _ in range(BARS_PER_DAY * 3):
        eng.tick(["NIFTY", "BANKNIFTY"])
        if not sim.advance():
            break
        t = sim.now().astimezone(IST).time()
        if t.hour == 15 and t.minute >= 20:
            assert not eng.broker.open_positions(), "intraday positions must be squared off by 15:15"
    assert eng.broker.positions, "expected the engine to trade on simulated data"


class FakeScripMaster:
    def security_id(self, contract):
        return "12345"


def test_dhan_refuses_live_host():
    with pytest.raises(ValueError):
        DhanSandboxMirror(FakeScripMaster(), base_url="https://api.dhan.co/v2", client_id="x", access_token="y")


def test_dhan_orders_buy_first_then_close_shorts_first(sim, tmp_path):
    m = DhanSandboxMirror(FakeScripMaster(), client_id="100", access_token="tok")
    sent = []
    m._request = lambda method, path, body=None: sent.append(body) or {"orderId": str(len(sent))}
    expiry = sim.expiries("NIFTY")[1]
    legs = [
        Leg(OptionContract("NIFTY", expiry, 25200, OptionType.CE), Side.SELL, 1, 65, 50),
        Leg(OptionContract("NIFTY", expiry, 25000, OptionType.CE), Side.BUY, 1, 65, 120),
    ]
    broker = PaperBroker(tmp_path / "a.json", sim, CostConfig(), 500_000)
    pos = broker.open(_signal(sim, legs))
    m.on_entry(pos, None)
    assert [b["transactionType"] for b in sent] == ["BUY", "SELL"]
    assert sent[0]["exchangeSegment"] == "NSE_FNO" and sent[0]["quantity"] == 65
    sent.clear()
    m.on_exit(pos)
    assert [b["transactionType"] for b in sent] == ["BUY", "SELL"]  # buy back short, then sell long
    assert pos.mirror_refs["dhan_sandbox"]["exit"][0]["response"] == {"orderId": "1"}


def test_sensibull_ticket_file(sim, tmp_path):
    out = []
    tickets = SensibullTickets(tmp_path / "t.txt", echo=out.append)
    broker = PaperBroker(tmp_path / "a.json", sim, CostConfig(), 500_000)
    sig = _signal(sim, [_atm_call(sim)])
    pos = broker.open(sig)
    tickets.on_entry(pos, sig)
    broker.close(pos, "TARGET")
    tickets.on_exit(pos)
    text = (tmp_path / "t.txt").read_text()
    assert "SIGNAL" in text and "EXIT" in text and "BUY" in text


def test_cli_simulate_and_signal(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "simulate", "--days", "3"]) == 0
    assert "Trades" in capsys.readouterr().out
    assert main(["--home", str(tmp_path), "signal", "--data", "sim", "-u", "NIFTY"]) == 0
    assert main(["--home", str(tmp_path), "init-config"]) == 0
    assert (tmp_path / "config.json").exists()


def test_kite_data_with_fake_client(monkeypatch):
    expiry = date(2026, 10, 6)

    class FakeKite:
        def __init__(self, api_key):
            pass

        def set_access_token(self, t):
            pass

        def instruments(self, exch):
            return [
                {"segment": "NFO-OPT", "name": "NIFTY", "expiry": expiry, "strike": 25000.0,
                 "instrument_type": t, "tradingsymbol": f"NIFTY26O0625000{t}", "lot_size": 65}
                for t in ("CE", "PE")
            ]

        def ltp(self, syms):
            return {s: {"last_price": 25010.0, "instrument_token": 256265} for s in syms}

        def quote(self, keys):
            return {k: {"last_price": 120.0, "depth": {"buy": [{"price": 119.5}], "sell": [{"price": 120.5}]},
                        "oi": 1000} for k in keys}

    monkeypatch.setitem(sys.modules, "kiteconnect", types.SimpleNamespace(KiteConnect=FakeKite))
    from optrade.data.kite import KiteMarketData

    k = KiteMarketData(api_key="a", access_token="b")
    monkeypatch.setattr(k, "now", lambda: datetime(2026, 9, 29, 11, 0, tzinfo=IST))
    assert k.expiries("NIFTY") == [expiry]
    assert k.lot_size("NIFTY") == 65
    q = k.option_quote(OptionContract("NIFTY", expiry, 25000, OptionType.CE))
    assert q.bid == 119.5 and q.ask == 120.5 and q.iv and 0.05 < q.iv < 1
    assert k.atm_iv("NIFTY") is not None
