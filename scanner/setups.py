import pandas as pd
from dataclasses import dataclass


@dataclass
class Setup:
    ticker: str
    contract: str
    setup_type: str
    strike: float
    expiration: str
    dte: int
    opt_type: str
    volume: int
    open_interest: int
    iv: float
    last_price: float
    spot_price: float | None
    detail: str


_REQUIRED_COLS = {
    "ticker", "contractSymbol", "volume", "openInterest",
    "impliedVolatility", "lastPrice", "strike", "expiration", "dte", "type",
}


def find_unusual_volume(
    chain: pd.DataFrame,
    min_volume: int,
    min_ratio: float,
) -> list[Setup]:
    """Flag contracts where volume significantly exceeds open interest."""
    if chain.empty or not _REQUIRED_COLS.issubset(chain.columns):
        return []

    hits = chain[
        (chain["volume"] >= min_volume)
        & (chain["openInterest"] > 0)
        & (chain["volume"] / chain["openInterest"] >= min_ratio)
    ]

    setups = []
    for _, row in hits.iterrows():
        ratio = row["volume"] / row["openInterest"]
        setups.append(Setup(
            ticker=row["ticker"],
            contract=row["contractSymbol"],
            setup_type="unusual_volume",
            strike=float(row["strike"]),
            expiration=row["expiration"],
            dte=int(row["dte"]),
            opt_type=row["type"],
            volume=int(row["volume"]),
            open_interest=int(row["openInterest"]),
            iv=round(float(row["impliedVolatility"]) * 100, 1),
            last_price=float(row["lastPrice"]),
            spot_price=None,
            detail=f"Vol/OI {ratio:.1f}x  ({int(row['volume'])} vol / {int(row['openInterest'])} OI)",
        ))
    return setups
