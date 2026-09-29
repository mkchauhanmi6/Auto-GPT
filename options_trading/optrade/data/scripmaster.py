"""NSE index-option contract list from Dhan's public scrip master (no login needed).

Gives the *real* expiry dates (holiday-adjusted), lot sizes and Dhan security IDs.
The 25 MB CSV is downloaded at most once per day and reduced to a small JSON cache.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import urllib.request
from datetime import date
from pathlib import Path
from typing import Optional

from ..models import OptionContract, OptionType

log = logging.getLogger(__name__)

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


class ScripMaster:
    def __init__(self, cache_dir: Path, url: str = SCRIP_MASTER_URL, today: Optional[date] = None):
        self.cache_dir = Path(cache_dir)
        self.url = url
        self.today = today or date.today()
        # key: (underlying, expiry iso, strike, CE/PE) -> (security_id, lot_size)
        self._rows: dict[tuple[str, str, float, str], tuple[str, int]] | None = None

    @property
    def cache_file(self) -> Path:
        return self.cache_dir / f"nse_index_options_{self.today.isoformat()}.json"

    def _load(self) -> dict[tuple[str, str, float, str], tuple[str, int]]:
        if self._rows is not None:
            return self._rows
        if self.cache_file.exists():
            raw = json.loads(self.cache_file.read_text())
        else:
            raw = self._download()
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(raw))
        self._rows = {
            (r[0], r[1], float(r[2]), r[3]): (str(r[4]), int(r[5])) for r in raw
        }
        return self._rows

    def _download(self) -> list[list]:
        log.info("Downloading Dhan scrip master (~25 MB) ...")
        req = urllib.request.Request(self.url, headers={"User-Agent": "optrade/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        return parse_scrip_master(text)

    # ---- lookups ----------------------------------------------------------
    def expiries(self, underlying: str) -> list[date]:
        u = underlying.upper()
        exp = {date.fromisoformat(k[1]) for k in self._load() if k[0] == u}
        return sorted(e for e in exp if e >= self.today)

    def lot_size(self, underlying: str) -> Optional[int]:
        u = underlying.upper()
        for k, (_, lot) in self._load().items():
            if k[0] == u:
                return lot
        return None

    def strikes(self, underlying: str, expiry: date) -> list[float]:
        u, e = underlying.upper(), expiry.isoformat()
        return sorted({k[2] for k in self._load() if k[0] == u and k[1] == e})

    def security_id(self, contract: OptionContract) -> Optional[str]:
        key = (
            contract.underlying.upper(),
            contract.expiry.isoformat(),
            float(contract.strike),
            contract.option_type.value,
        )
        hit = self._load().get(key)
        return hit[0] if hit else None


def parse_scrip_master(text: str) -> list[list]:
    """Extract NSE index options as [underlying, expiry, strike, CE/PE, security_id, lot]."""
    out: list[list] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if row.get("SEM_EXM_EXCH_ID") != "NSE" or row.get("SEM_INSTRUMENT_NAME") != "OPTIDX":
            continue
        opt = row.get("SEM_OPTION_TYPE", "")
        if opt not in (OptionType.CE.value, OptionType.PE.value):
            continue
        # e.g. "NIFTY-Oct2026-25000-CE"
        underlying = row["SEM_TRADING_SYMBOL"].split("-")[0].upper()
        try:
            out.append(
                [
                    underlying,
                    row["SEM_EXPIRY_DATE"][:10],
                    float(row["SEM_STRIKE_PRICE"]),
                    opt,
                    row["SEM_SMST_SECURITY_ID"],
                    int(float(row["SEM_LOT_UNITS"])),
                ]
            )
        except (KeyError, ValueError):
            continue
    return out
