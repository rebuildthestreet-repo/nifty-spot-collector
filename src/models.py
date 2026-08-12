from dataclasses import dataclass


@dataclass
class Bar:
    timestamp: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    oi: float = 0.0
