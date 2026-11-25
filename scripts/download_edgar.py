"""Descarga de fundamentals históricos point-in-time desde SEC EDGAR (companyfacts XBRL).

Guarda data_ml/edgar/<TICKER>.csv en formato largo: tag, unit, end, start, val, fy, fp, form, filed.
`filed` = fecha en que el dato se hizo público -> permite armar features sin look-ahead.
Cubre us-gaap (10-K/10-Q) e ifrs-full (20-F de emisores extranjeros: TSM, BABA, ASML, PBR, VIST, GLOB).
"""
import os
import time

import pandas as pd
import requests

UA = {"User-Agent": os.environ.get("SEC_UA") or "AppFinanciera Research github-actions@users.noreply.github.com",
      "Accept-Encoding": "gzip, deflate"}
TICKERS = ["NVDA", "GOOGL", "AMD", "MELI", "VIST", "META", "MSFT", "AMZN", "AVGO", "MCD", "TSM", "ORCL", "TSLA",
           "MRNA", "GLOB", "AAPL", "KO", "BABA", "INTC", "PEP", "PBR", "V", "IBM", "WMT", "JPM", "ASML", "MRVL", "UNH"]
TAGS = {
    "us-gaap": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense", "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss",
                "EarningsPerShareDiluted", "Assets", "Liabilities", "StockholdersEquity", "LongTermDebt",
                "LongTermDebtNoncurrent", "CashAndCashEquivalentsAtCarryingValue",
                "NetCashProvidedByUsedInOperatingActivities", "PaymentsToAcquirePropertyPlantAndEquipment",
                "ResearchAndDevelopmentExpense", "CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "ifrs-full": ["Revenue", "GrossProfit", "ProfitLossFromOperatingActivities", "ProfitLoss",
                  "ProfitLossAttributableToOwnersOfParent", "DilutedEarningsLossPerShare", "Assets", "Liabilities",
                  "Equity", "CashAndCashEquivalents", "CashFlowsFromUsedInOperatingActivities",
                  "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "dei": ["EntityCommonStockSharesOutstanding"],
}
OUT = "data/edgar"


def get(url):
    for i in range(4):
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 * (i + 1))
    r.raise_for_status()


def main():
    os.makedirs(OUT, exist_ok=True)
    m = get("https://www.sec.gov/files/company_tickers.json")
    cik = {v["ticker"].upper(): int(v["cik_str"]) for v in m.values()}
    for t in TICKERS:
        c = cik.get(t)
        if c is None:
            print(t, "sin CIK")
            continue
        try:
            js = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c:010d}.json")
        except Exception as e:
            print(t, "error", e)
            continue
        rows = []
        for tax, tags in TAGS.items():
            facts = js.get("facts", {}).get(tax, {})
            for tag in tags:
                for unit, vals in facts.get(tag, {}).get("units", {}).items():
                    for v in vals:
                        rows.append({"tag": tag, "unit": unit, "end": v.get("end"), "start": v.get("start"),
                                     "val": v.get("val"), "fy": v.get("fy"), "fp": v.get("fp"),
                                     "form": v.get("form"), "filed": v.get("filed")})
        df = pd.DataFrame(rows)
        df.to_csv(f"{OUT}/{t}.csv", index=False)
        print(t, len(df), "filas", df["filed"].min() if len(df) else "")
        time.sleep(0.3)   # límite SEC: 10 req/s


if __name__ == "__main__":
    main()
