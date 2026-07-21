from __future__ import annotations

import argparse
import base64
from pathlib import Path

import numpy as np
import pandas as pd


def rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    return (
        pd.Series(values)
        .rolling(window, center=True, min_periods=max(3, window // 3))
        .median()
        .to_numpy()
    )


def preprocess_lk(values: np.ndarray, trend_window: int) -> np.ndarray:
    values = values.astype(float)
    trend = rolling_median(values, trend_window)
    residual = values - trend
    fill = np.nanmedian(residual)
    residual = np.where(np.isfinite(residual), residual, fill)
    residual = residual - np.median(residual)
    mad = np.median(np.abs(residual - np.median(residual)))
    scale = mad if mad > 1e-12 else np.std(residual)
    if scale <= 1e-12:
        scale = 1.0
    residual = np.clip(residual, -4 * scale, 4 * scale)
    std = np.std(residual)
    if std <= 1e-12:
        return residual * 0.0
    return (residual - np.mean(residual)) / std


def normalized_windows(signal: np.ndarray, window: int) -> np.ndarray:
    # Sliding window view without depending on scipy.
    shape = (signal.size - window + 1, window)
    strides = (signal.strides[0], signal.strides[0])
    windows = np.lib.stride_tricks.as_strided(signal, shape=shape, strides=strides).copy()
    windows -= windows.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(windows, axis=1, keepdims=True)
    norms[norms <= 1e-12] = 1.0
    return windows / norms


def load_profile(path: Path, angle_deg: float, lk_column: str) -> pd.DataFrame:
    usecols = ["pc_time", "angle_deg", "dl50_progress_mm", lk_column]
    df = pd.read_csv(path, usecols=lambda c: c in usecols)
    df = df[df["angle_deg"].round(6) == round(angle_deg, 6)].copy()
    df = df[np.isfinite(df["dl50_progress_mm"]) & np.isfinite(df[lk_column])]
    if "pc_time" in df.columns:
        df = df.sort_values("pc_time")
    else:
        df = df.sort_index()
    df = df.reset_index(drop=True)
    return df


def validate_angle(
    map_df: pd.DataFrame,
    query_df: pd.DataFrame,
    *,
    angle_deg: float,
    lk_column: str,
    window_samples: int,
    query_stride: int,
    trend_window: int,
) -> list[dict]:
    if len(map_df) < window_samples + 5 or len(query_df) < window_samples + 5:
        return []

    map_signal = preprocess_lk(map_df[lk_column].to_numpy(float), trend_window)
    query_signal = preprocess_lk(query_df[lk_column].to_numpy(float), trend_window)

    map_windows = normalized_windows(map_signal, window_samples)
    map_centers = np.arange(window_samples // 2, window_samples // 2 + len(map_windows))
    map_x_centers = map_df["dl50_progress_mm"].to_numpy(float)[map_centers]

    rows: list[dict] = []
    query_starts = range(0, len(query_signal) - window_samples + 1, query_stride)
    for qs in query_starts:
        q = query_signal[qs : qs + window_samples].copy()
        q -= q.mean()
        q_norm = np.linalg.norm(q)
        if q_norm <= 1e-12:
            continue
        q /= q_norm

        corr = map_windows @ q
        best_idx = int(np.argmax(corr))
        query_center = qs + window_samples // 2
        true_x = float(query_df["dl50_progress_mm"].iloc[query_center])
        est_x = float(map_x_centers[best_idx])

        rows.append(
            {
                "angle_deg": angle_deg,
                "window_samples": window_samples,
                "query_center_index": query_center,
                "map_center_index": int(map_centers[best_idx]),
                "true_x_mm": true_x,
                "estimated_x_mm": est_x,
                "error_mm": est_x - true_x,
                "abs_error_mm": abs(est_x - true_x),
                "correlation": float(corr[best_idx]),
            }
        )
    return rows


def write_html(output_html: Path, summary: pd.DataFrame, images: list[Path], result_csv: Path) -> None:
    image_blocks = []
    for image in images:
        data = base64.b64encode(image.read_bytes()).decode("ascii")
        image_blocks.append(
            f"<section><h2>{image.stem}</h2><img src=\"data:image/png;base64,{data}\" /></section>"
        )

    table_html = summary.to_html(index=False, float_format=lambda x: f"{x:.3f}")
    html = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>LK-Gのみを用いた軸方向位置推定の検証</title>
<style>
body {{ font-family: Arial, 'Yu Gothic', sans-serif; margin: 28px; line-height: 1.65; color: #222; }}
h1 {{ font-size: 24px; }}
h2 {{ margin-top: 28px; font-size: 18px; }}
img {{ max-width: 100%; border: 1px solid #ddd; }}
table {{ border-collapse: collapse; margin-top: 12px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: right; }}
th {{ background: #f3f3f3; }}
.note {{ background: #f7f7f7; padding: 12px 14px; border-left: 4px solid #666; }}
</style>
</head>
<body>
<h1>LK-Gのみを用いた軸方向位置推定の検証</h1>
<div class="note">
<p>この検証では、照合処理には <strong>LK-G85A の lk_out1_mm の時系列波形のみ</strong> を使用した。</p>
<p>DL50の値は照合には使わず、推定された位置と真値位置を比較するための評価ラベルとしてのみ使用した。</p>
<p>片方の測定データをLK-G波形地図、もう片方を検証対象波形として扱い、同じ角度内で波形窓の正規化相互相関が最大となる位置を推定位置とした。</p>
</div>
<h2>集計結果</h2>
{table_html}
<p>詳細CSV: {result_csv.name}</p>
{''.join(image_blocks)}
</body>
</html>
"""
    output_html.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", default="pipe154_auto_scan_m115_115.csv")
    parser.add_argument("--query", default="pipe154_rescan_resolution_move_v2.csv")
    parser.add_argument("--output-dir", default="lk_g_only_distance_validation")
    parser.add_argument("--lk-column", default="lk_out1_mm")
    parser.add_argument("--windows", default="80,120,160,220")
    parser.add_argument("--angle-step", type=int, default=5)
    parser.add_argument("--query-stride", type=int, default=20)
    parser.add_argument("--trend-window", type=int, default=81)
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    map_path = Path(args.map)
    query_path = Path(args.query)
    if not map_path.is_absolute():
        map_path = here / map_path
    if not query_path.is_absolute():
        query_path = here / query_path

    outdir = Path(args.output_dir)
    if not outdir.is_absolute():
        outdir = here / outdir
    outdir.mkdir(parents=True, exist_ok=True)

    map_angles = pd.read_csv(map_path, usecols=["angle_deg"])["angle_deg"].dropna().unique()
    query_angles = pd.read_csv(query_path, usecols=["angle_deg"])["angle_deg"].dropna().unique()
    angles = sorted(set(np.round(map_angles, 6)) & set(np.round(query_angles, 6)))
    angles = angles[:: max(1, args.angle_step)]
    windows = [int(v.strip()) for v in args.windows.split(",") if v.strip()]

    all_rows: list[dict] = []
    for angle in angles:
        map_df = load_profile(map_path, float(angle), args.lk_column)
        query_df = load_profile(query_path, float(angle), args.lk_column)
        for window in windows:
            all_rows.extend(
                validate_angle(
                    map_df,
                    query_df,
                    angle_deg=float(angle),
                    lk_column=args.lk_column,
                    window_samples=window,
                    query_stride=args.query_stride,
                    trend_window=args.trend_window,
                )
            )

    results = pd.DataFrame(all_rows)
    result_csv = outdir / "lk_g_only_distance_validation_results.csv"
    results.to_csv(result_csv, index=False, encoding="utf-8-sig")

    summary = (
        results.groupby("window_samples")
        .agg(
            n=("abs_error_mm", "count"),
            mean_abs_error_mm=("abs_error_mm", "mean"),
            median_abs_error_mm=("abs_error_mm", "median"),
            p80_abs_error_mm=("abs_error_mm", lambda s: float(np.percentile(s, 80))),
            p90_abs_error_mm=("abs_error_mm", lambda s: float(np.percentile(s, 90))),
            within_10mm=("abs_error_mm", lambda s: float(np.mean(s <= 10))),
            within_20mm=("abs_error_mm", lambda s: float(np.mean(s <= 20))),
            median_correlation=("correlation", "median"),
        )
        .reset_index()
    )
    summary_csv = outdir / "lk_g_only_distance_validation_summary.csv"
    summary.to_csv(summary_csv, index=False, encoding="utf-8-sig")

    import matplotlib.pyplot as plt

    images: list[Path] = []
    for window in windows:
        sub = results[results["window_samples"] == window]
        if sub.empty:
            continue
        fig, axes = plt.subplots(2, 2, figsize=(13, 9))
        axes[0, 0].scatter(sub["true_x_mm"], sub["estimated_x_mm"], s=8, alpha=0.45)
        lim_min = min(sub["true_x_mm"].min(), sub["estimated_x_mm"].min())
        lim_max = max(sub["true_x_mm"].max(), sub["estimated_x_mm"].max())
        axes[0, 0].plot([lim_min, lim_max], [lim_min, lim_max], "k--", lw=1)
        axes[0, 0].set_title(f"Estimated position vs true position (window={window} samples)")
        axes[0, 0].set_xlabel("True position from DL50 [mm]")
        axes[0, 0].set_ylabel("Estimated position from LK-G waveform [mm]")

        axes[0, 1].scatter(sub["true_x_mm"], sub["error_mm"], s=8, alpha=0.45)
        axes[0, 1].axhline(0, color="k", lw=1)
        axes[0, 1].axhline(20, color="r", lw=0.8, ls="--")
        axes[0, 1].axhline(-20, color="r", lw=0.8, ls="--")
        axes[0, 1].set_title("Error along pipe axis")
        axes[0, 1].set_xlabel("True position from DL50 [mm]")
        axes[0, 1].set_ylabel("Estimated - true [mm]")

        axes[1, 0].hist(sub["error_mm"], bins=60, color="#4c78a8", alpha=0.85)
        axes[1, 0].axvline(0, color="k", lw=1)
        axes[1, 0].axvline(20, color="r", lw=0.8, ls="--")
        axes[1, 0].axvline(-20, color="r", lw=0.8, ls="--")
        axes[1, 0].set_title("Error histogram")
        axes[1, 0].set_xlabel("Estimated - true [mm]")
        axes[1, 0].set_ylabel("Count")

        angle_summary = sub.groupby("angle_deg")["abs_error_mm"].median().reset_index()
        axes[1, 1].plot(angle_summary["angle_deg"], angle_summary["abs_error_mm"], marker="o")
        axes[1, 1].axhline(20, color="r", lw=0.8, ls="--")
        axes[1, 1].set_title("Median absolute error by angle")
        axes[1, 1].set_xlabel("Angle [deg]")
        axes[1, 1].set_ylabel("Median abs error [mm]")

        fig.tight_layout()
        image = outdir / f"lk_g_only_error_window_{window}.png"
        fig.savefig(image, dpi=160)
        plt.close(fig)
        images.append(image)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(summary["window_samples"], summary["median_abs_error_mm"], marker="o", label="median")
    ax.plot(summary["window_samples"], summary["p90_abs_error_mm"], marker="o", label="p90")
    ax.axhline(20, color="r", ls="--", lw=1, label="20 mm")
    ax.set_xlabel("LK-G waveform window length [samples]")
    ax.set_ylabel("Absolute error [mm]")
    ax.set_title("LK-G-only axial localization error")
    ax.legend()
    fig.tight_layout()
    image = outdir / "lk_g_only_error_summary.png"
    fig.savefig(image, dpi=160)
    plt.close(fig)
    images.insert(0, image)

    html = outdir / "lk_g_only_distance_validation.html"
    write_html(html, summary, images, result_csv)

    print(summary.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"results: {result_csv}")
    print(f"summary: {summary_csv}")
    print(f"html: {html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
