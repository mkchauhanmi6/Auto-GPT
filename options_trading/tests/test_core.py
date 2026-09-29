import math
from datetime import date, datetime

import pytest

from optrade.data.scripmaster import parse_scrip_master
from optrade.indicators import ema, realized_vol, rsi
from optrade.instruments import get_spec, rule_based_expiries
from optrade.models import Leg, OptionContract, OptionType, Side
from optrade.payoff import analyse
from optrade.pricing import bs_greeks, bs_price, implied_vol, time_to_expiry
from optrade.timeutil import IST, is_market_open


def test_put_call_parity():
    s, k, t, v, r = 25000, 25100, 7 / 365, 0.14, 0.065
    c = bs_price(s, k, t, v, OptionType.CE, r)
    p = bs_price(s, k, t, v, OptionType.PE, r)
    assert c - p == pytest.approx(s - k * math.exp(-r * t), abs=1e-6)


def test_implied_vol_roundtrip():
    price = bs_price(25000, 25200, 10 / 365, 0.17, OptionType.CE)
    assert implied_vol(price, 25000, 25200, 10 / 365, OptionType.CE) == pytest.approx(0.17, abs=1e-3)


def test_greeks_signs():
    call = bs_greeks(25000, 25000, 7 / 365, 0.14, OptionType.CE)
    put = bs_greeks(25000, 25000, 7 / 365, 0.14, OptionType.PE)
    assert 0.4 < call.delta < 0.6 and -0.6 < put.delta < -0.4
    assert call.theta < 0 and call.gamma > 0 and call.vega > 0


def test_time_to_expiry_zero_after_close():
    assert time_to_expiry(date(2026, 10, 6), datetime(2026, 10, 6, 15, 31, tzinfo=IST)) == 0
    assert time_to_expiry(date(2026, 10, 6), datetime(2026, 10, 5, 15, 30, tzinfo=IST)) == pytest.approx(1 / 365)


def test_indicators():
    assert ema([1, 1, 1], 5) == [1, 1, 1]
    up = [float(i) for i in range(1, 40)]
    assert rsi(up)[-1] == 100.0
    assert realized_vol([100.0] * 10, 252) == 0.0


def test_expiry_rules_and_holidays():
    nifty = rule_based_expiries(get_spec("NIFTY"), date(2026, 9, 29), 3)
    assert nifty == [date(2026, 9, 29), date(2026, 10, 6), date(2026, 10, 13)]
    bank = rule_based_expiries(get_spec("BANKNIFTY"), date(2026, 9, 30), 2)
    assert bank == [date(2026, 10, 27), date(2026, 11, 24)]
    # holiday on the Tuesday moves expiry to Monday
    shifted = rule_based_expiries(get_spec("NIFTY"), date(2026, 10, 14), 1, holidays=[date(2026, 10, 20)])
    assert shifted == [date(2026, 10, 19)]


def test_market_hours():
    assert is_market_open(datetime(2026, 9, 29, 9, 15, tzinfo=IST))
    assert not is_market_open(datetime(2026, 9, 29, 15, 30, tzinfo=IST))
    assert not is_market_open(datetime(2026, 10, 3, 11, 0, tzinfo=IST))  # Saturday


def _leg(side, opt, strike, price, lots=1, lot=65):
    return Leg(OptionContract("NIFTY", date(2026, 10, 6), strike, opt), side, lots, lot, price)


def test_payoff_bull_call_spread():
    legs = [_leg(Side.BUY, OptionType.CE, 25000, 150), _leg(Side.SELL, OptionType.CE, 25200, 70)]
    s = analyse(legs)
    assert s.max_loss == pytest.approx(80 * 65)
    assert s.max_profit == pytest.approx(120 * 65)
    assert s.breakevens == [25080]


def test_payoff_long_call_unlimited_and_condor():
    assert analyse([_leg(Side.BUY, OptionType.CE, 25000, 100)]).max_profit is None
    condor = [
        _leg(Side.BUY, OptionType.PE, 24400, 10),
        _leg(Side.SELL, OptionType.PE, 24600, 30),
        _leg(Side.SELL, OptionType.CE, 25400, 30),
        _leg(Side.BUY, OptionType.CE, 25600, 10),
    ]
    s = analyse(condor)
    assert s.max_profit == pytest.approx(40 * 65)
    assert s.max_loss == pytest.approx(160 * 65)
    assert s.breakevens == [24560, 25440]


def test_parse_scrip_master():
    csv_text = (
        "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_EXPIRY_CODE,"
        "SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,"
        "SEM_OPTION_TYPE,SEM_TICK_SIZE,SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME\n"
        "NSE,D,35000,OPTIDX,0,BANKNIFTY-Sep2026-72600-CE,30.0,BANKNIFTY 29 SEP 72600 CALL,"
        "2026-09-29 14:30:00,72600.00000,CE,5.0000,M,OP,,\n"
        "NSE,I,13,INDEX,0,NIFTY,1.0,Nifty 50,0001-01-01,,XX,0.0500,,INDEX,X,NIFTY\n"
    )
    assert parse_scrip_master(csv_text) == [["BANKNIFTY", "2026-09-29", 72600.0, "CE", "35000", 30]]
