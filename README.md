# rotacion_cartera

Rotación trimestral de CEDEARs con machine learning sobre el **subyacente** (acción en USD), con contexto
fundamental (SEC EDGAR). El sistema es semi-automático: cada semana publica el ranking, la cartera objetivo y un semáforo
fundamental por activo; las órdenes se ejecutan a mano en el broker (IOL).

## Qué hace el modelo

| Pieza | Definición |
|---|---|
| Universo | 30 CEDEARs líquidos en BYMA con historia larga (ver `make_signal.py`). |
| E4 | Ensamble LightGBM (Huber) + Ridge que predice el retorno a 63 días hábiles (apertura→apertura) **relativo a la mediana del universo**. Features: momentum, volatilidad, distancia a medias, RSI, fuerza relativa vs SPY, ranks cross-seccionales, z-scores temporales y régimen (VIX, tasa 10 años, amplitud). |
| Sorpresa de earnings | Rank cross-seccional de la sorpresa % del último reporte (point-in-time: cuenta desde el día hábil siguiente). |
| Score | `0,75 · rank(E4) + 0,25 · rank(sorpresa)` |
| Cartera | Top 5, 20% cada uno, siempre invertido. Rebalanceo el último día hábil de enero, abril, julio y octubre; ejecución a la apertura siguiente. Buffer: lo que ya está en cartera se mantiene mientras siga en el top 7. |
| Semáforo | Contexto, **no entra en el score**: calidad EDGAR (accruals, apalancamiento, FCF yield, crecimiento y márgenes), revisión del EPS estimado a 30 días, rentabilidad, precio objetivo de consenso y aviso de earnings próximos. |

## Resultados (walk-forward 2012–2026, neto de costos IOL ~0,94% por lado)

| Estrategia | CAGR | Sharpe | MaxDD | t vs B&H |
|---|---|---|---|---|
| **E4 + sorpresa, top 5** | **48,1%** | **1,32** | −37% | 3,5 |
| E4 + sorpresa, top 8 | 39,6% | 1,29 | −32% | 3,2 |
| E4 + sorpresa + compuesto EDGAR, top 5 | 48,0% | 1,31 | −34% | 3,4 |
| B&H equiponderado de los 30 | 25,7% | 1,26 | −31% | |
| SPY | 14,3% | 0,89 | −34% | |

Le gana al B&H en 12 de 14 años y en ambas mitades (2012–18 y 2019–26).

**Advertencias honestas**
- El universo son los líquidos de **hoy**: queda sesgo de supervivencia, aunque el B&H de comparación usa el mismo universo.
- Se probaron ~90 variantes en total; el t ≈ 3,5 es prometedor, no una garantía.
- Volatilidad ~34% anual, mayor que el B&H (~20%). Drawdowns de −35/−40% son esperables.
- Recomendado: paper trading de al menos un trimestre antes de operar con dinero.

## Qué NO funcionó (y por qué está documentado)

| Probado | Resultado |
|---|---|
| Swing con take profit / stop loss (triple barrier) | AUC honesto 0,51–0,53; los costos de IOL (~1,9% ida y vuelta) se comen todo. |
| Rotación de 21 ETFs (momentum, dual momentum, GTAA) | No le gana a SPY neto de costos. |
| Timing con VIX/VIX3M, RSI(2) de Connors, turn-of-the-month | No le ganan al B&H ni sin costos; con costos mueren. |
| Vol-targeting, core-satellite, dual momentum sobre acciones | Menos retorno que el B&H. |
| Meter 8 features de earnings dentro del modelo | Empeora (sobreajuste); funciona mejor como ajuste simple al score. |

Detalle de cada experimento en `experiments/` y sus resultados en `results/`.

## Cómo se usa

1. Cada viernes el workflow `Datos + señal semanal` baja precios, earnings y EDGAR, y publica `signals/latest.md`.
2. En la semana del rebalanceo, mirá `signals/latest.md`: vendé lo que salió del top 7, comprá lo que entra y re-equilibrá a 20%.
3. Actualizá `portfolio.json` con lo que tenés comprado (así el buffer funciona).

Manual: `pip install -r requirements.txt yfinance && python make_signal.py`.

## Recordatorio de rebalanceo

El workflow `Recordatorio de rebalanceo` corre los días hábiles a las 10:00 (Buenos Aires) y solo actúa la **víspera** del rebalanceo: abre un issue asignado al dueño del repo (GitHub lo manda por mail) con la cartera objetivo y qué comprar o vender contra `portfolio.json`. Si además cargás los secrets `MAIL_USERNAME` y `MAIL_PASSWORD` (contraseña de aplicación de Gmail) y opcionalmente `MAIL_TO`, también manda un mail directo. Con los secrets `WHATSAPP_PHONE` y `WHATSAPP_APIKEY` (CallMeBot) manda además un WhatsApp corto. El día del rebalanceo, `Datos + señal semanal` corre igual aunque no sea viernes, para que la señal quede al cierre de ese día.

## Web (Vercel)

La carpeta `web/` es un sitio estático con el estilo de la suite Analiza: cartera objetivo, ranking con semáforo, backtest contra buy & hold y seguimiento de cada señal publicada. Lee `web/data/dashboard.json`, que el workflow regenera cada viernes con `scripts/export_web.py`. En Vercel: importar el repo con **Root Directory = `web`**, sin framework ni build.

## Datos

- `data/prices/`: OHLCV diario ajustado (yfinance, corriendo en GitHub Actions).
- `data/earnings/`: fechas, estimaciones y sorpresas de EPS desde ~2002 (yfinance).
- `data/edgar/`: hechos XBRL de SEC EDGAR con fecha de presentación (requiere el secret `SEC_UA` = "Nombre email").
- `data/fundamentals.csv` y `data/fund_detail/`: snapshot actual (valuación, consenso, revisiones), solo para el semáforo.

## Estructura

```
make_signal.py          señal vigente + semáforo
tml/                    features, walk-forward, backtest, EDGAR
experiments/            todos los experimentos (v2 → 30 activos + fundamentals)
scripts/                descargas de datos
results/                resultados de los experimentos
signals/                señales publicadas
web/                    sitio estático (Vercel)
```

Esto no es asesoramiento financiero.
