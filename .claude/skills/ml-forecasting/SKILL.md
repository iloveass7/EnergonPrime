---
name: ml-forecasting
description: Demand forecasting, shortage prediction, and anomaly detection rules for the fuel platform. Use for any ML modeling, feature engineering, evaluation, or forecast-serving work.
---

# ML / Forecasting

Powers Predict (demand + shortage) and Detect (anomaly/disruption). Practical engineering over novelty.

## Mandatory workflow
`data → baseline → experiment → metric → comparison → decision`. Never ship a model without beating a
documented baseline (naive/seasonal-naive for forecasting; simple threshold for detection).

## Rules
- **No data leakage**: strictly time-ordered splits, no future features, fit scalers/encoders on train only.
- Cross-validate with **rolling/expanding time-series CV**, not random k-fold.
- **Uncertainty first**: produce prediction intervals (quantile or conformal), not just point forecasts —
  the decision layer needs them. Report calibration (coverage vs. nominal).
- Forecast metrics: MAE / RMSE / MAPE / pinball loss + coverage. Detection: precision/recall/PR-AUC, and
  cost of missed shortage vs. false alarm.
- Features from `/v1/demand-history`, supply-arrivals, events, seasonality/calendar, station/depot attrs.
- **Serving**: models load behind a stable interface `predict(context) -> {forecast, interval, confidence}`.
  Inference must be fast and must degrade gracefully (fall back to baseline if a model errors/times out).
- Version every model + its training data hash + metrics (see `deployment`/experiment tracking).
- Emit `model_latency`, `model_confidence`, `forecast_error` as metrics (see `observability`).

## Determinism
Fix seeds; record data snapshot. A rerun on the same simulator seed must reproduce results.
