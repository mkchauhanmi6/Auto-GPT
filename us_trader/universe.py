"""Stock universe: ~100 of the largest, most liquid US companies, with GICS-style sectors."""
STOCKS = {
    "Tech": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "AMD", "CSCO", "ACN", "IBM", "INTU", "QCOM",
             "TXN", "AMAT", "NOW", "MU", "LRCX", "ADI", "KLAC", "PANW", "INTC"],
    "Communication": ["GOOGL", "META", "NFLX", "DIS", "TMUS", "VZ", "T", "CMCSA"],
    "ConsumerDisc": ["AMZN", "TSLA", "HD", "MCD", "LOW", "BKNG", "NKE", "SBUX", "TJX", "CMG"],
    "ConsumerStaples": ["WMT", "COST", "PG", "KO", "PEP", "PM", "MO", "MDLZ", "CL"],
    "Health": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "ISRG", "DHR", "AMGN", "PFE", "GILD", "BMY",
               "VRTX", "MDT", "SYK"],
    "Financials": ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "SPGI", "BLK", "C", "SCHW", "PGR", "CB"],
    "Industrials": ["GE", "CAT", "RTX", "UNP", "HON", "BA", "DE", "LMT", "UPS", "ETN"],
    "Energy": ["XOM", "CVX", "COP"],
    "Utilities": ["NEE", "SO", "DUK"],
    "RealEstate": ["PLD", "AMT"],
    "Materials": ["LIN"],
}
SECTOR = {s: sec for sec, syms in STOCKS.items() for s in syms}
