from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Config:
    # --- datos ---
    start: str = "2005-01-01"
    end: str | None = None                 # None = hoy
    # Universo operable: top 15 CEDEARs por volumen en BYMA (Supabase App-Financiera, cedear_ccl_diario,
    # promedio nov-2026), excluyendo los de historia corta (SNDK, NU, NBIS, IBIT) y SPY (benchmark).
    universe: tuple = ("NVDA", "GOOGL", "AMD", "QQQ", "MELI", "VIST", "META", "MSFT",
                       "AMZN", "GLD", "AVGO", "MCD", "TSM", "ORCL", "TSLA")
    extra_tickers: tuple = ("SPY", "AAPL")   # AAPL solo para la demo de leakage (tu setup original)
    benchmark: str = "SPY"
    context_tickers: tuple = ("^VIX", "^TNX")
    cache_dir: str = "data_cache"
    min_history_days: int = 300            # historia mínima para que un activo entre al panel
    min_dollar_vol: float = 20e6           # filtro de liquidez del subyacente (USD/día, media 60d)

    # --- etiquetado triple-barrier ---
    horizon: int = 15        # barrera vertical (días hábiles)
    tp_atr: float = 2.0      # take profit = entrada + tp_atr * ATR14
    sl_atr: float = 1.0      # stop loss  = entrada - sl_atr * ATR14

    # --- walk-forward ---
    first_test_year: int = 2012
    retrain_months: int = 3          # frecuencia de reentrenamiento
    val_months: int = 12             # validación interna (early stopping + umbral), purgada
    embargo_days: int = 5            # embargo extra además del purging por t1

    # --- modelo ---
    lgb_params: dict = field(default_factory=lambda: dict(
        objective="binary", learning_rate=0.03, num_leaves=15, min_child_samples=500,
        feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, lambda_l2=10.0,
        n_estimators=600, verbose=-1))

    # --- backtest ---
    max_positions: int = 6
    signal_quantile: float = 0.90     # umbral = cuantil de las probabilidades en validación
    # Costos por lado (fracción). IOL: comisión ~0,5% + derechos de mercado ~0,08% + IVA 21% s/comisión,
    # más medio spread estimado del CEDEAR. Ajustar a tu tarifa real.
    commission: float = 0.005
    market_fees: float = 0.0008
    vat_on_commission: float = 0.21
    half_spread: float = 0.0025
    idle_mode: str = "spy"            # "spy": capital ocioso en SPY y se rota | "cash": ocioso en USD

    @property
    def cost_per_side(self) -> float:
        return self.commission * (1 + self.vat_on_commission) + self.market_fees + self.half_spread
