from __future__ import annotations

import pickle
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dtaidistance import dtw
from scipy.cluster.hierarchy import dendrogram, fcluster, linkage
from scipy.integrate import trapezoid
from scipy.interpolate import interp1d
from scipy.spatial.distance import pdist, squareform
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.preprocessing import StandardScaler


STD_COLOR_RAW = "0.75"
STD_COLOR_MEAN = "tab:blue"
STD_COLOR_REP = "tab:orange"
STD_CMAP = "viridis"


ProgressCallback = Callable[[float, str], None]


def _emit_progress(progress_callback: ProgressCallback | None, percent: float, message: str) -> None:
    if progress_callback is None:
        return
    progress_callback(max(0.0, min(100.0, float(percent))), message)


def build_cycles_raw_data(
    input_path: Path,
    threshold: float,
    min_duration_minutes: float,
    min_off_minutes: float,
    tail_samples: int,
    max_idle_gap_minutes: float | None = 5.0,   
) -> list[dict[str, Any]]:
    df = pd.read_csv(
        input_path,
        sep="\t",
        usecols=["time", "value"],
        parse_dates=["time"],
    )
    df = df.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
    if len(df) == 0:
        return []

    values = df["value"].to_numpy(dtype=float)
    times = df["time"].to_numpy(dtype="datetime64[ns]")
    above = values > threshold
    if not np.any(above):
        return []

    cycles_raw: list[dict[str, Any]] = []
    cycle_id = 0
    n = len(values)
    max_idle_gap_seconds = None
    if max_idle_gap_minutes is not None and float(max_idle_gap_minutes) > 0:
        max_idle_gap_seconds = float(max_idle_gap_minutes) * 60.0

    def _close_cycle(start_idx_local: int, end_idx_local: int, tail_base_idx: int):
        nonlocal cycle_id
        if end_idx_local < start_idx_local:
            return
        start_time_local = pd.Timestamp(times[start_idx_local])
        end_time_local = pd.Timestamp(times[end_idx_local])
        duration_minutes_local = (end_time_local - start_time_local).total_seconds() / 60.0
        if duration_minutes_local < min_duration_minutes:
            return

        tail_end_local = min(int(tail_base_idx) + tail_samples - 1, n - 1)
        cycle_data_local = [
            {"time": pd.Timestamp(times[j]), "value": float(values[j])}
            for j in range(start_idx_local, tail_end_local + 1)
        ]
        if len(cycle_data_local) < 3:
            return

        cycles_raw.append(
            {
                "cycle_id": cycle_id,
                "start_time": start_time_local,
                "end_time": end_time_local,
                "duration_minutes": duration_minutes_local,
                "data": cycle_data_local,
            }
        )
        cycle_id += 1

    in_cycle = False
    start_idx = -1
    last_above_idx = -1
    off_start_idx = -1

    for i in range(n):
        if in_cycle and i > 0 and max_idle_gap_seconds is not None:
            gap_seconds = (pd.Timestamp(times[i]) - pd.Timestamp(times[i - 1])).total_seconds()
            if gap_seconds >= max_idle_gap_seconds:
                # Long gap with no valid readings: force cycle boundary.
                _close_cycle(start_idx, last_above_idx, i - 1)
                in_cycle = False
                start_idx = -1
                last_above_idx = -1
                off_start_idx = -1

        if above[i]:
            if not in_cycle:
                in_cycle = True
                start_idx = i
            last_above_idx = i
            off_start_idx = -1
            continue

        if not in_cycle:
            continue

        if off_start_idx < 0:
            off_start_idx = i

        off_minutes = (pd.Timestamp(times[i]) - pd.Timestamp(times[off_start_idx])).total_seconds() / 60.0
        if off_minutes < min_off_minutes:
            continue

        _close_cycle(start_idx, last_above_idx, off_start_idx)

        in_cycle = False
        start_idx = -1
        last_above_idx = -1
        off_start_idx = -1

    if in_cycle and last_above_idx >= start_idx:
        _close_cycle(start_idx, last_above_idx, last_above_idx + 1)

    return cycles_raw


def extract_cycle_df(cycle: dict[str, Any]) -> pd.DataFrame:
    df = pd.DataFrame(cycle["data"]).copy()
    df = df.dropna(subset=["time", "value"]).copy()
    df["time"] = pd.to_datetime(df["time"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"]).sort_values("time").reset_index(drop=True)
    return df


def compute_energy_kwh(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    t_sec = (df["time"] - df["time"].iloc[0]).dt.total_seconds().values
    power_w = df["value"].values.astype(float)
    energy_wh = trapezoid(power_w, t_sec / 3600.0)
    return float(energy_wh / 1000.0)


def trim_cycle_for_shape(df: pd.DataFrame, max_minutes: float) -> pd.DataFrame:
    if len(df) == 0:
        return df
    if max_minutes >= 9999:
        return df
    cutoff_time = df["time"].iloc[0] + pd.Timedelta(minutes=max_minutes)
    trimmed = df[df["time"] <= cutoff_time].copy()
    if len(trimmed) < 2:
        return df.iloc[: min(2, len(df))].copy()
    return trimmed


def resample_curve(df: pd.DataFrame, target_len: int) -> np.ndarray:
    if len(df) < 2:
        return np.full(target_len, np.nan)

    t_sec = (df["time"] - df["time"].iloc[0]).dt.total_seconds().values
    y = df["value"].values.astype(float)

    duration = t_sec.max() - t_sec.min()
    if duration <= 0:
        return np.full(target_len, y[0] if len(y) > 0 else np.nan)

    t_norm = (t_sec - t_sec.min()) / duration
    target_t = np.linspace(0, 1, target_len)
    f = interp1d(t_norm, y, kind="linear", bounds_error=False, fill_value="extrapolate")
    return f(target_t)


def normalize_curve(y: np.ndarray, mode: str = "zscore") -> np.ndarray:
    y = np.asarray(y, dtype=float)
    if mode == "zscore":
        mu = np.mean(y)
        sigma = np.std(y)
        if sigma < 1e-12:
            return y - mu
        return (y - mu) / sigma
    if mode == "minmax":
        ymin = np.min(y)
        ymax = np.max(y)
        if abs(ymax - ymin) < 1e-12:
            return np.zeros_like(y)
        return (y - ymin) / (ymax - ymin)
    return y


def normalize_distance_matrix(D: np.ndarray) -> np.ndarray:
    D = np.asarray(D, dtype=float)
    max_val = np.max(D)
    if max_val < 1e-12:
        return D
    return D / max_val


def _normalize_metric(values: list[float], higher_is_better: bool) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    vmin = float(np.min(arr))
    vmax = float(np.max(arr))
    if abs(vmax - vmin) < 1e-12:
        out = np.full_like(arr, 0.5)
    else:
        out = (arr - vmin) / (vmax - vmin)
    if not higher_is_better:
        out = 1.0 - out
    return out


def _compute_k_scores(
    Z: np.ndarray,
    D_hybrid: np.ndarray,
    global_scaled: np.ndarray,
    k_cfg: dict[str, Any],
    n_cycles: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    k_min = 2
    k_max = min(int(k_cfg["search_max"]), n_cycles - 1)
    k_range = list(range(k_min, k_max + 1))

    merge_heights = Z[:, 2]
    last = merge_heights[-k_max:]
    accel = np.diff(last, 2)
    if accel.size == 0:
        # Handle empty accel: fallback to k_max or another default
        k_accel = k_max
    else:
        k_accel = k_max - int(np.argmax(accel))
        k_accel = max(k_min, min(k_accel, k_max))

    labels_by_k: dict[int, np.ndarray] = {}
    sil_scores: list[float] = []
    db_scores: list[float] = []
    ch_scores: list[float] = []

    for k in k_range:
        lbl = fcluster(Z, t=k, criterion="maxclust") - 1
        labels_by_k[k] = lbl
        sil_scores.append(float(silhouette_score(D_hybrid, lbl, metric="precomputed")))
        db_scores.append(float(davies_bouldin_score(global_scaled, lbl)))
        ch_scores.append(float(calinski_harabasz_score(global_scaled, lbl)))

    k_sil = k_range[int(np.argmax(sil_scores))]
    k_db = k_range[int(np.argmin(db_scores))]
    k_ch = k_range[int(np.argmax(ch_scores))]

    sil_norm = _normalize_metric(sil_scores, higher_is_better=True)
    db_norm = _normalize_metric(db_scores, higher_is_better=False)
    ch_norm = _normalize_metric(ch_scores, higher_is_better=True)

    largest_cluster_frac_scores: list[float] = []
    singleton_frac_scores: list[float] = []
    small_cluster_frac_scores: list[float] = []
    human_score_values: list[float] = []

    for k in k_range:
        lbl = labels_by_k[k]
        _, counts = np.unique(lbl, return_counts=True)
        counts = counts.astype(float)

        largest_cluster_frac_scores.append(float(np.max(counts) / np.sum(counts)))
        singleton_frac_scores.append(float(np.sum(counts == 1) / len(counts)))
        small_cluster_frac_scores.append(float(np.sum(counts < int(k_cfg["human_min_cluster_size"])) / len(counts)))

    for i in range(len(k_range)):
        balance_score = 1.0 - largest_cluster_frac_scores[i]
        human_score = (
            0.35 * float(sil_norm[i])
            + 0.20 * float(db_norm[i])
            + 0.15 * float(ch_norm[i])
            + 0.30 * balance_score
            - 0.25 * singleton_frac_scores[i]
            - 0.15 * small_cluster_frac_scores[i]
        )
        human_score_values.append(float(human_score))

    eligible_indices = [
        i for i, k in enumerate(k_range)
        if k >= int(k_cfg["human_k_min"])
        and largest_cluster_frac_scores[i] <= float(k_cfg["human_max_largest_cluster_frac"])
        and singleton_frac_scores[i] <= float(k_cfg["human_max_singleton_frac"])
    ]

    if eligible_indices:
        best_idx_human = max(eligible_indices, key=lambda i: human_score_values[i])
    else:
        fallback = [i for i, k in enumerate(k_range) if k >= int(k_cfg["human_k_min"])]
        if not fallback:
            fallback = list(range(len(k_range)))
        best_idx_human = max(fallback, key=lambda i: human_score_values[i])

    k_human = k_range[best_idx_human]
    votes = Counter([k_accel, k_sil, k_db, k_ch])
    k_vote = votes.most_common(1)[0][0]

    scores_df = pd.DataFrame(
        {
            "k": k_range,
            "silhouette": sil_scores,
            "davies_bouldin": db_scores,
            "calinski_harabasz": ch_scores,
            "largest_cluster_frac": largest_cluster_frac_scores,
            "singleton_frac": singleton_frac_scores,
            "small_cluster_frac": small_cluster_frac_scores,
            "human_score": human_score_values,
            "eligible_human_rule": [i in set(eligible_indices) for i in range(len(k_range))],
        }
    )

    picks = {
        "k_accel": int(k_accel),
        "k_sil": int(k_sil),
        "k_db": int(k_db),
        "k_ch": int(k_ch),
        "k_human": int(k_human),
        "k_vote": int(k_vote),
    }
    return scores_df, picks


def _run_and_save(
    k: int,
    mode: str,
    Z: np.ndarray,
    D_hybrid: np.ndarray,
    meta_df: pd.DataFrame,
    curve_features: np.ndarray,
    output_dir: Path,
    progress_callback: ProgressCallback | None = None,
    progress_start: float = 0.0,
    progress_end: float = 100.0,
) -> None:
    def _local_progress(fraction: float, message: str) -> None:
        percent = progress_start + (progress_end - progress_start) * max(0.0, min(1.0, fraction))
        _emit_progress(progress_callback, percent, message)

    _local_progress(0.0, f"Clustering k={k}: assigning cycles")
    out_dir = output_dir / f"results_k{k}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if mode == "exact":
        model = AgglomerativeClustering(n_clusters=k, metric="precomputed", linkage="average")
        lbl = model.fit_predict(D_hybrid)
    else:
        lbl = fcluster(Z, t=k, criterion="maxclust") - 1

    local_meta = meta_df.copy()
    local_meta["cluster"] = lbl

    summary = local_meta.groupby("cluster").agg(
        n_cycles=("cycle_id", "count"),
        duration_mean=("duration_minutes", "mean"),
        duration_std=("duration_minutes", "std"),
        max_power_mean=("max_power", "mean"),
        max_power_std=("max_power", "std"),
        mean_power_mean=("mean_power", "mean"),
        mean_power_std=("mean_power", "std"),
        energy_mean_kwh=("energy_kwh", "mean"),
        energy_std_kwh=("energy_kwh", "std"),
        peak_time_mean=("time_of_peak_norm", "mean"),
    )

    reps: list[dict[str, Any]] = []
    for c in sorted(np.unique(lbl)):
        idx = np.where(lbl == c)[0]
        D_sub = D_hybrid[np.ix_(idx, idx)]
        # Pick a representative that is both centrally connected (hybrid distance)
        # and shape-close to the cluster mean curve to avoid visually odd outliers.
        centrality = D_sub.mean(axis=1)
        cluster_curves = curve_features[idx]
        cluster_mean_curve = np.mean(cluster_curves, axis=0)
        shape_dist = np.linalg.norm(cluster_curves - cluster_mean_curve, axis=1)

        c_std = float(np.std(centrality))
        s_std = float(np.std(shape_dist))
        centrality_n = (centrality - float(np.mean(centrality))) / (c_std if c_std > 1e-12 else 1.0)
        shape_dist_n = (shape_dist - float(np.mean(shape_dist))) / (s_std if s_std > 1e-12 else 1.0)
        rep_score = 0.5 * centrality_n + 0.5 * shape_dist_n
        best_idx = idx[int(np.argmin(rep_score))]
        reps.append(
            {
                "cluster": int(c),
                "cycle_id": local_meta.iloc[best_idx]["cycle_id"],
                "duration_minutes": local_meta.iloc[best_idx]["duration_minutes"],
                "max_power": local_meta.iloc[best_idx]["max_power"],
                "mean_power": local_meta.iloc[best_idx]["mean_power"],
                "energy_kwh": local_meta.iloc[best_idx]["energy_kwh"],
            }
        )

    rep_df = pd.DataFrame(reps)
    _local_progress(0.30, f"Clustering k={k}: writing CSV files")
    local_meta.to_csv(out_dir / "clusters.csv", index=False)
    summary.to_csv(out_dir / "cluster_summary.csv")
    rep_df.to_csv(out_dir / "cluster_representatives.csv", index=False)

    _local_progress(0.45, f"Clustering k={k}: saving heatmap")
    # Heatmap ordinata per cluster
    order = np.argsort(lbl)
    D_sorted = D_hybrid[order][:, order]
    fig, ax = plt.subplots(figsize=(10, 8))
    im = ax.imshow(D_sorted, aspect="auto", cmap=STD_CMAP)
    fig.colorbar(im, ax=ax, label="Hybrid distance")
    ax.set_title(f"Hybrid distance matrix - k={k} ({mode})")
    ax.set_xlabel("Ordered cycles")
    ax.set_ylabel("Ordered cycles")
    fig.savefig(out_dir / "heatmap_sorted.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    time_axis = np.linspace(0, 1, curve_features.shape[1])
    clusters = sorted(np.unique(lbl))
    for idx_c, c in enumerate(clusters):
        _local_progress(
            0.55 + 0.40 * (idx_c / max(1, len(clusters))),
            f"Clustering k={k}: saving cluster {int(c)} chart",
        )
        rep_cycle_id = rep_df.loc[rep_df["cluster"] == c, "cycle_id"].values[0]
        rep_idx = local_meta.index[local_meta["cycle_id"] == rep_cycle_id][0]
        idx = np.where(lbl == c)[0]
        cluster_curves = curve_features[idx]

        fig, ax = plt.subplots(figsize=(11, 5))
        first_raw = True
        for cv in cluster_curves:
            if first_raw:
                ax.plot(time_axis, cv, alpha=0.20, color=STD_COLOR_RAW, label="Raw cycles")
                first_raw = False
            else:
                ax.plot(time_axis, cv, alpha=0.20, color=STD_COLOR_RAW)

        ax.plot(time_axis, np.mean(cluster_curves, axis=0), linewidth=3, color=STD_COLOR_MEAN, label="Cluster mean")
        ax.plot(time_axis, curve_features[rep_idx], linewidth=2.5, color=STD_COLOR_REP, label="Representative")
        ax.set_title(f"Cluster {c} - overview (k={k}, n={len(idx)})")
        ax.set_xlabel("Normalized time")
        ax.set_ylabel("Normalized consumption")
        ax.grid(True)
        ax.legend()
        fig.savefig(out_dir / f"cluster_{c}_overview.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
    _local_progress(1.0, f"Clustering k={k}: complete")


def run_cycle_pipeline(
    input_path: Path,
    output_dir: Path,
    pkl_path: Path,
    chart_title_prefix: str,
    params: dict[str, Any],
    exact_k: int | None,
    progress_callback: ProgressCallback | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _emit_progress(progress_callback, 0, "Preparing cycle pipeline")

    c = params["cycle"]
    f = params["features"]
    h = params["hybrid"]
    k_cfg = params["k"]

    if bool(c.get("force_rebuild_pkl", False)) or not pkl_path.exists():
        if not input_path.exists():
            raise FileNotFoundError(f"Source file not found: {input_path}")
        _emit_progress(progress_callback, 4, "Extracting cycles from source data")
        cycles_to_save = build_cycles_raw_data(
            input_path=input_path,
            threshold=float(c["threshold_watts"]),
            min_duration_minutes=float(c["min_duration_minutes"]),
            min_off_minutes=float(c["min_off_minutes"]),
            tail_samples=int(c["tail_samples"]),
            max_idle_gap_minutes=float(c.get("max_idle_gap_minutes", 5.0)),
        )
        with open(pkl_path, "wb") as fp:
            pickle.dump(cycles_to_save, fp)
        _emit_progress(progress_callback, 10, f"Saved {len(cycles_to_save)} extracted cycles")

    _emit_progress(progress_callback, 12, "Loading extracted cycles")
    with open(pkl_path, "rb") as fp:
        cycles: list[dict[str, Any]] = pickle.load(fp)

    max_cycles = c.get("max_cycles_to_analyze")
    if max_cycles and len(cycles) > int(max_cycles):
        rng = np.random.default_rng(42)
        pick = rng.choice(len(cycles), size=int(max_cycles), replace=False)
        cycles = [cycles[int(i)] for i in sorted(pick)]

    if len(cycles) < 3:
        raise RuntimeError("You need at least 3 cycles to perform clustering analysis. Found only: {}".format(len(cycles)))

    target_len = int(f["target_len"])
    norm_mode = str(f["curve_norm_mode"])
    max_analysis_minutes = float(c["max_analysis_minutes"])

    curve_features: list[np.ndarray] = []
    meta_rows: list[dict[str, Any]] = []

    for idx_cycle, cycle in enumerate(cycles):
        if idx_cycle == 0 or idx_cycle == len(cycles) - 1 or idx_cycle % max(1, len(cycles) // 20) == 0:
            _emit_progress(
                progress_callback,
                15 + 15 * (idx_cycle / max(1, len(cycles))),
                f"Computing cycle features {idx_cycle + 1}/{len(cycles)}",
            )
        df = extract_cycle_df(cycle)
        df_shape = trim_cycle_for_shape(df, max_minutes=max_analysis_minutes)
        y_resampled = resample_curve(df_shape, target_len=target_len)
        y_curve = normalize_curve(y_resampled, mode=norm_mode)

        duration_minutes = float(cycle.get("duration_minutes", np.nan))
        max_power = float(df["value"].max()) if len(df) > 0 else np.nan
        mean_power = float(df["value"].mean()) if len(df) > 0 else np.nan
        energy_kwh = float(compute_energy_kwh(df))

        t_sec = (df["time"] - df["time"].iloc[0]).dt.total_seconds().values if len(df) > 0 else np.array([0.0])
        if len(df) > 0 and len(t_sec) > 0 and t_sec[-1] > 0:
            peak_idx = int(np.argmax(df["value"].values))
            time_of_peak_norm = float(t_sec[peak_idx] / t_sec[-1])
        else:
            time_of_peak_norm = 0.0

        curve_features.append(y_curve)
        meta_rows.append(
            {
                "cycle_id": cycle.get("cycle_id", None),
                "start_time": cycle.get("start_time", None),
                "end_time": cycle.get("end_time", None),
                "duration_minutes": duration_minutes,
                "max_power": max_power,
                "mean_power": mean_power,
                "energy_kwh": energy_kwh,
                "time_of_peak_norm": time_of_peak_norm,
            }
        )
    _emit_progress(progress_callback, 30, "Cycle features ready")

    X_curve = np.array(curve_features, dtype=np.double)
    meta_df = pd.DataFrame(meta_rows)

    _emit_progress(progress_callback, 33, "Scaling global features")
    global_cols = ["duration_minutes", "max_power", "mean_power", "energy_kwh", "time_of_peak_norm"]
    global_scaled = StandardScaler().fit_transform(meta_df[global_cols].values.astype(float))

    _emit_progress(progress_callback, 36, "Computing DTW distance matrix")
    series_list = [np.asarray(x, dtype=np.double) for x in X_curve]

    # Lightweight validation to catch malformed series that can cause C-extension
    # conversions to overflow on some platforms (e.g. 32-bit ssize_t).
    try:
        problems: list[tuple[int, str, object]] = []
        lens = []
        stats = []
        for i, s in enumerate(series_list):
            # ensure 1-D numeric array
            if not isinstance(s, np.ndarray):
                problems.append((i, "not_ndarray", type(s)))
                continue
            if s.ndim != 1:
                problems.append((i, "ndim", int(s.ndim)))
            # extremely large sizes are suspicious on constrained platforms
            if s.size > 10_000_000:
                problems.append((i, "large_size", int(s.size)))
            # check for non-finite values
            nonfinite = int(np.sum(~np.isfinite(s)))
            if nonfinite > 0:
                problems.append((i, "nonfinite", nonfinite))
            # gather stats
            try:
                amin = float(np.nanmin(s))
                amax = float(np.nanmax(s))
                amean = float(np.nanmean(s))
            except Exception:
                amin = amax = amean = float("nan")
            lens.append(int(s.size))
            stats.append((i, str(s.dtype), tuple(s.shape), int(s.nbytes), amin, amax, amean))

        # Print a concise summary and the per-series stats for the first 20 series
        print(f"DTW series summary: n_series={len(series_list)}, sample_lens={lens[:10]}")
        for st in stats[:20]:
            print(f"series[{st[0]}]: dtype={st[1]}, shape={st[2]}, nbytes={st[3]}, min={st[4]}, max={st[5]}, mean={st[6]}")

        if problems:
            print("DTW series validation problems:", problems)
    except Exception as _ex:
        # If validation crashes, continue to call DTW and allow original error to surface
        print("DTW series validation failed:", type(_ex).__name__, _ex)

    # Prefer the fast C implementation but gracefully fall back to the
    # pure-Python implementation if the C-extension raises errors on this platform.
    try:
        D_dtw_condensed = dtw.distance_matrix_fast(series_list, compact=True)
    except OverflowError as oe:
        print("DTW C-extension OverflowError, falling back to Python implementation:", oe)
        D_dtw_condensed = dtw.distance_matrix(series_list, compact=True, use_c=False)
    except Exception as ex:
        # Catch other possible C-extension issues (e.g., ValueError from conversion)
        print("DTW C-extension failed, falling back to Python implementation:", type(ex).__name__, ex)
        D_dtw_condensed = dtw.distance_matrix(series_list, compact=True, use_c=False)
    D_dtw = squareform(D_dtw_condensed)

    _emit_progress(progress_callback, 48, "Computing global distance matrix")
    D_global_condensed = pdist(global_scaled, metric="euclidean")
    D_global = squareform(D_global_condensed)

    D_dtw_norm = normalize_distance_matrix(D_dtw)
    D_global_norm = normalize_distance_matrix(D_global)

    D_hybrid = float(h["alpha"]) * D_dtw_norm + float(h["beta"]) * D_global_norm
    D_hybrid_condensed = squareform(D_hybrid, checks=False)

    _emit_progress(progress_callback, 52, "Saving distance heatmap")
    fig = plt.figure(figsize=(10, 8))
    plt.imshow(D_hybrid, aspect="auto", cmap=STD_CMAP)
    plt.colorbar(label="Hybrid distance")
    plt.title(f"Hybrid distance matrix between {chart_title_prefix} cycles")
    plt.xlabel("Cycle index")
    plt.ylabel("Cycle index")
    fig.savefig(output_dir / "distance_heatmap.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    _emit_progress(progress_callback, 56, "Building dendrogram")
    Z = linkage(D_hybrid_condensed, method="average")

    fig = plt.figure(figsize=(16, 6))
    dendrogram(Z, no_labels=True)
    plt.title("Hierarchical clustering dendrogram (DTW + global features)")
    plt.xlabel("Cycles")
    plt.ylabel("Hybrid distance")
    fig.savefig(output_dir / "dendrogram.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    _emit_progress(progress_callback, 62, "Scoring possible k values")
    scores_df, picks = _compute_k_scores(Z, D_hybrid, global_scaled, k_cfg, len(cycles))
    _emit_progress(progress_callback, 66, "Writing optimal k report")
    with open(output_dir / "optimal_k_report.txt", "w", encoding="utf-8") as fout:
        fout.write("=== Optimal k detection report ===\n\n")
        fout.write(f"Elbow acceleration  -> k = {picks['k_accel']}\n")
        fout.write(f"Silhouette          -> k = {picks['k_sil']}\n")
        fout.write(f"Davies-Bouldin      -> k = {picks['k_db']}\n")
        fout.write(f"Calinski-Harabasz   -> k = {picks['k_ch']}\n")
        fout.write(f"\nCluster suggerito (voto): {picks['k_vote']}\n")
        fout.write(f"Cluster human-friendly: {picks['k_human']}\n\n")
        fout.write(scores_df.to_string(index=False))

    if exact_k is not None:
        _run_and_save(
            exact_k,
            "exact",
            Z,
            D_hybrid,
            meta_df,
            X_curve,
            output_dir,
            progress_callback=progress_callback,
            progress_start=68,
            progress_end=100,
        )
        print(f"Eseguito clustering a k fisso esatto: k={exact_k}")
    else:
        k_to_run: list[int] = []
        for candidate in [picks["k_accel"], picks["k_sil"], picks["k_db"], picks["k_ch"], picks["k_human"]]:
            if candidate not in k_to_run:
                k_to_run.append(candidate)
        for idx_k, k in enumerate(k_to_run):
            span_start = 68 + 32 * (idx_k / max(1, len(k_to_run)))
            span_end = 68 + 32 * ((idx_k + 1) / max(1, len(k_to_run)))
            _run_and_save(
                k,
                "auto",
                Z,
                D_hybrid,
                meta_df,
                X_curve,
                output_dir,
                progress_callback=progress_callback,
                progress_start=span_start,
                progress_end=span_end,
            )
        print(f"Eseguiti cluster automatici: {k_to_run}")
    _emit_progress(progress_callback, 100, "Cycle pipeline complete")
