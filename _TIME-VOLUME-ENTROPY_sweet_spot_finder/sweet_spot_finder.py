"""
BTC Timeframe Sweet Spot Finder v2.0
=====================================
Engine pro hledání optimálního timeframe pro fázovou interferenci a kvantové features.

Navrženo pro:
- Velké datasety (miliony řádků tick/1s/1m dat)
- Libovolné TF rozlišení (i neceločíselné: 11.5M, 13.7M)
- Hledání pravidelností i v chaotických mikro-TF
- Statistickou validaci (bootstrap, walk-forward)
- Export features pro BTC predictor

Použití:
    from sweet_spot_finder import SweetSpotFinder
    
    finder = SweetSpotFinder()
    finder.load_csv("btc_1m.csv")  # nebo 1s data
    results = finder.scan(tf_range=(1, 120), tf_step=0.5, validate=True)
    finder.report(results)
    finder.export_features(results, top_n=3)

Architektura:
    1. Resample engine (time/volume/tick/dollar/entropy clock)
    2. Indicator engine (RSI, StochK, MACD, Vol_Ratio, ATR + quantum features)
    3. Interference engine (phase, cos(Δφ), combined)
    4. Scoring engine (quintile spread, correlation, monotonicity, WR)
    5. Validation engine (bootstrap CI, walk-forward, overfit detection)
    6. Report & export
"""

import numpy as np
import sys
import time as time_module
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum


# ═══════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════

class ClockType(Enum):
    TIME = "time"           # standardní časové svíčky
    VOLUME = "volume"       # volume bars ("čas neexistuje")
    TICK = "tick"           # tick bars (N obchodů)
    DOLLAR = "dollar"       # dollar bars (N USD objemu)
    ENTROPY = "entropy"     # entropy bars (nový bar při nové informaci)


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float
    count: int = 1          # počet zdrojových barů
    duration_sec: float = 0 # skutečná délka v sekundách
    timestamp: float = 0


@dataclass
class ScanResult:
    tf_minutes: float
    clock: ClockType
    n_bars: int
    kurtosis: float
    
    # Interference metrics (best across SW/FWD combos)
    sk_vr_spread: float = 0.0
    sk_vr_sw: int = 0
    sk_vr_fwd: int = 0
    sk_vr_q5_ret: float = 0.0
    sk_vr_q1_ret: float = 0.0
    sk_vr_wr_q5: float = 0.0
    sk_vr_corr: float = 0.0
    sk_vr_monotonicity: float = 0.0  # NEW: monotonicity score
    
    rsi_vr_spread: float = 0.0
    mh_vr_spread: float = 0.0
    combined_spread: float = 0.0
    
    # Quantum features
    entropy_spread: float = 0.0
    fractal_spread: float = 0.0
    noise_ratio_spread: float = 0.0
    decoherence: float = 0.0        # rolling kurtosis (quantum indicator)
    
    # Validation
    bootstrap_ci_low: float = 0.0
    bootstrap_ci_high: float = 0.0
    walk_forward_spread: float = 0.0
    overfit_score: float = 0.0       # 0 = no overfit, 1 = full overfit
    
    # Composite
    composite_score: float = 0.0
    reliability_score: float = 0.0   # accounts for sample size


# ═══════════════════════════════════════════════
# CORE MATH (optimized numpy)
# ═══════════════════════════════════════════════

def fast_ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average, handles NaN"""
    result = np.full_like(arr, np.nan, dtype=np.float64)
    k = 2.0 / (period + 1)
    prev = np.nan
    for i in range(len(arr)):
        if np.isnan(arr[i]):
            continue
        if np.isnan(prev):
            prev = arr[i]
        else:
            prev = arr[i] * k + prev * (1 - k)
        result[i] = prev
    return result


def fast_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI optimized for large arrays"""
    n = len(closes)
    rsi = np.full(n, np.nan)
    deltas = np.diff(closes)
    
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    
    avg_gain = np.nan
    avg_loss = np.nan
    
    for i in range(period - 1, len(deltas)):
        if i == period - 1:
            avg_gain = np.mean(gains[i-period+1:i+1])
            avg_loss = np.mean(losses[i-period+1:i+1])
        else:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rsi[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    
    return rsi


def fast_stoch_k(rsi: np.ndarray, period: int = 14) -> np.ndarray:
    """Stochastic K of RSI"""
    n = len(rsi)
    sk = np.full(n, np.nan)
    for i in range(period, n):
        window = rsi[max(0, i-period+1):i+1]
        valid = window[~np.isnan(window)]
        if len(valid) < 2:
            continue
        mn, mx = valid.min(), valid.max()
        if mx > mn:
            sk[i] = (rsi[i] - mn) / (mx - mn) * 100
        else:
            sk[i] = 50.0
    return sk


def fast_macd(closes: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD line, signal, histogram"""
    ema12 = fast_ema(closes, 12)
    ema26 = fast_ema(closes, 26)
    macd_line = ema12 - ema26
    signal = fast_ema(macd_line, 9)
    histogram = macd_line - signal
    return macd_line, signal, histogram


def fast_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, 
             period: int = 14) -> np.ndarray:
    """Average True Range"""
    n = len(closes)
    tr = np.full(n, np.nan)
    for i in range(1, n):
        if np.isnan(highs[i]) or np.isnan(lows[i]) or np.isnan(closes[i-1]):
            continue
        tr[i] = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i-1]),
                     abs(lows[i] - closes[i-1]))
    
    atr = np.full(n, np.nan)
    for i in range(period, n):
        window = tr[i-period+1:i+1]
        valid = window[~np.isnan(window)]
        if len(valid) > 0:
            atr[i] = np.mean(valid)
    return atr


def fast_vol_ratio(volumes: np.ndarray, period: int = 20) -> np.ndarray:
    """Volume / MA(Volume)"""
    n = len(volumes)
    vr = np.full(n, np.nan)
    for i in range(period, n):
        window = volumes[i-period:i]
        valid = window[window > 0]
        if len(valid) > 0 and volumes[i] > 0:
            vr[i] = volumes[i] / np.mean(valid)
    return vr


def normalize_wave(arr: np.ndarray) -> np.ndarray:
    """Normalize to [-1, 1]"""
    valid = arr[~np.isnan(arr)]
    if len(valid) < 2:
        return arr
    mn, mx = valid.min(), valid.max()
    mid = (mx + mn) / 2
    rng = (mx - mn) / 2
    if rng == 0:
        rng = 1
    result = (arr - mid) / rng
    result[np.isnan(arr)] = np.nan
    return result


def smooth_sma(arr: np.ndarray, window: int) -> np.ndarray:
    """Simple moving average smoothing"""
    if window <= 1:
        return arr.copy()
    result = np.full_like(arr, np.nan)
    hw = window // 2
    for i in range(hw, len(arr) - hw):
        chunk = arr[max(0, i-hw):i+hw+1]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) > 0:
            result[i] = np.mean(valid)
    return result


def instant_phase(wave: np.ndarray) -> np.ndarray:
    """Instantaneous phase via atan2(signal, derivative)"""
    phase = np.full_like(wave, np.nan)
    # Central difference for derivative
    for i in range(2, len(wave) - 2):
        if np.isnan(wave[i]) or np.isnan(wave[i-1]) or np.isnan(wave[i+1]):
            continue
        dx = (wave[i+1] - wave[i-1]) / 2
        phase[i] = np.arctan2(wave[i], dx)
    return phase


def phase_interference(s1: np.ndarray, s2: np.ndarray, sw: int = 7) -> np.ndarray:
    """cos(Δφ) between two signals"""
    w1 = smooth_sma(normalize_wave(s1), sw)
    w2 = smooth_sma(normalize_wave(s2), sw)
    p1 = instant_phase(w1)
    p2 = instant_phase(w2)
    pd = p1 - p2
    pd = np.mod(pd + np.pi, 2 * np.pi) - np.pi
    return np.cos(pd)


def combined_interference_3(s1, s2, s3, sw=27):
    """Combined interference of 3 signals"""
    w1 = smooth_sma(normalize_wave(s1), sw)
    w2 = smooth_sma(normalize_wave(s2), sw)
    w3 = smooth_sma(normalize_wave(s3), sw)
    p1 = instant_phase(w1)
    p2 = instant_phase(w2)
    p3 = instant_phase(w3)
    
    i12 = np.cos(np.mod(p1 - p2 + np.pi, 2*np.pi) - np.pi)
    i13 = np.cos(np.mod(p1 - p3 + np.pi, 2*np.pi) - np.pi)
    i23 = np.cos(np.mod(p2 - p3 + np.pi, 2*np.pi) - np.pi)
    
    return i12 * i13 * i23


# ═══════════════════════════════════════════════
# QUANTUM FEATURES
# ═══════════════════════════════════════════════

def local_entropy(returns: np.ndarray, window: int = 20, bins: int = 10) -> np.ndarray:
    """Shannon entropy of local return distribution"""
    n = len(returns)
    h = np.full(n, np.nan)
    for i in range(window, n):
        chunk = returns[i-window:i]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) < bins:
            continue
        hist, _ = np.histogram(valid, bins=bins, density=True)
        hist = hist[hist > 0]
        if len(hist) > 0:
            probs = hist / hist.sum()
            h[i] = -np.sum(probs * np.log2(probs))
    return h


def fractal_dimension(returns: np.ndarray, window: int = 20) -> np.ndarray:
    """Local fractal dimension (direction change ratio)"""
    n = len(returns)
    fd = np.full(n, np.nan)
    for i in range(window + 1, n):
        chunk = returns[i-window:i]
        valid_mask = ~np.isnan(chunk[:-1]) & ~np.isnan(chunk[1:])
        if np.sum(valid_mask) < 5:
            continue
        signs = chunk[:-1][valid_mask] * chunk[1:][valid_mask]
        fd[i] = np.sum(signs < 0) / np.sum(valid_mask)
    return fd


def microstructure_noise(returns: np.ndarray, scale: int = 5,
                          window: int = 40) -> np.ndarray:
    """Variance ratio: var(micro × scale) / var(macro)"""
    n = len(returns)
    nr = np.full(n, np.nan)
    for i in range(window + scale, n):
        micro = returns[i-window:i]
        micro_valid = micro[~np.isnan(micro)]
        if len(micro_valid) < 10:
            continue
        micro_var = np.var(micro_valid) * scale
        
        # Macro returns
        macro = []
        for j in range(i - window, i - scale + 1, scale):
            chunk = returns[j:j+scale]
            valid = chunk[~np.isnan(chunk)]
            if len(valid) == scale:
                macro.append(np.sum(valid))
        
        if len(macro) >= 3:
            macro_var = np.var(macro)
            if macro_var > 0:
                nr[i] = micro_var / macro_var
    return nr


def rolling_kurtosis(returns: np.ndarray, window: int = 40) -> np.ndarray:
    """Rolling kurtosis — decoherence indicator"""
    n = len(returns)
    kurt = np.full(n, np.nan)
    for i in range(window, n):
        chunk = returns[i-window:i]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) < 20:
            continue
        m = np.mean(valid)
        s = np.std(valid)
        if s > 0:
            kurt[i] = np.mean(((valid - m) / s) ** 4) - 3
    return kurt


# ═══════════════════════════════════════════════
# SCORING ENGINE
# ═══════════════════════════════════════════════

def quintile_analysis(signal: np.ndarray, forward_ret: np.ndarray,
                       n_quantiles: int = 5) -> dict:
    """Full quintile/decile analysis"""
    mask = ~np.isnan(signal) & ~np.isnan(forward_ret)
    if np.sum(mask) < n_quantiles * 10:
        return None
    
    s = signal[mask]
    r = forward_ret[mask]
    
    sorted_idx = np.argsort(s)
    s_sorted = s[sorted_idx]
    r_sorted = r[sorted_idx]
    
    q_size = len(s_sorted) // n_quantiles
    if q_size < 5:
        return None
    
    quantiles = []
    for qi in range(n_quantiles):
        start = qi * q_size
        end = (qi + 1) * q_size if qi < n_quantiles - 1 else len(s_sorted)
        chunk_s = s_sorted[start:end]
        chunk_r = r_sorted[start:end]
        quantiles.append({
            'avg_signal': float(np.mean(chunk_s)),
            'avg_return': float(np.mean(chunk_r)),
            'wr_up': float(np.sum(chunk_r > 0) / len(chunk_r) * 100),
            'wr_down': float(np.sum(chunk_r < 0) / len(chunk_r) * 100),
            'n': len(chunk_r),
            'std_return': float(np.std(chunk_r)),
        })
    
    spread = quantiles[-1]['avg_return'] - quantiles[0]['avg_return']
    
    # Monotonicity score: Spearman correlation of quintile returns
    q_returns = [q['avg_return'] for q in quantiles]
    ranks_ret = np.argsort(np.argsort(q_returns))
    ranks_q = np.arange(n_quantiles)
    n_q = n_quantiles
    monotonicity = 1 - 6 * np.sum((ranks_ret - ranks_q) ** 2) / (n_q * (n_q**2 - 1))
    
    # Pearson correlation
    corr_val = np.corrcoef(s, r)[0, 1] if len(s) > 10 else 0.0
    
    return {
        'quantiles': quantiles,
        'spread': spread,
        'monotonicity': monotonicity,
        'correlation': corr_val,
        'n_total': len(s),
    }


def forward_returns(closes: np.ndarray, fwd: int) -> np.ndarray:
    """Compute forward returns"""
    n = len(closes)
    ret = np.full(n, np.nan)
    for i in range(n - fwd):
        if closes[i] > 0 and not np.isnan(closes[i]) and not np.isnan(closes[min(i+fwd, n-1)]):
            ret[i] = (closes[min(i+fwd, n-1)] - closes[i]) / closes[i] * 100
    return ret


# ═══════════════════════════════════════════════
# VALIDATION ENGINE
# ═══════════════════════════════════════════════

def bootstrap_ci(signal: np.ndarray, forward_ret: np.ndarray,
                  n_boot: int = 200, ci: float = 0.95) -> Tuple[float, float]:
    """Bootstrap confidence interval for quintile spread"""
    mask = ~np.isnan(signal) & ~np.isnan(forward_ret)
    s = signal[mask]
    r = forward_ret[mask]
    n = len(s)
    
    if n < 50:
        return (0.0, 0.0)
    
    spreads = []
    rng = np.random.RandomState(42)
    for _ in range(n_boot):
        idx = rng.randint(0, n, size=n)
        s_boot = s[idx]
        r_boot = r[idx]
        sorted_idx = np.argsort(s_boot)
        q = len(sorted_idx) // 5
        if q < 3:
            continue
        top = r_boot[sorted_idx[-q:]]
        bot = r_boot[sorted_idx[:q]]
        spreads.append(np.mean(top) - np.mean(bot))
    
    if not spreads:
        return (0.0, 0.0)
    
    alpha = (1 - ci) / 2
    lo = np.percentile(spreads, alpha * 100)
    hi = np.percentile(spreads, (1 - alpha) * 100)
    return (lo, hi)


def walk_forward_test(signal: np.ndarray, forward_ret: np.ndarray,
                       n_folds: int = 3) -> float:
    """Walk-forward validation spread"""
    mask = ~np.isnan(signal) & ~np.isnan(forward_ret)
    s = signal[mask]
    r = forward_ret[mask]
    n = len(s)
    
    if n < n_folds * 30:
        return 0.0
    
    fold_size = n // n_folds
    spreads = []
    
    for fold in range(n_folds):
        start = fold * fold_size
        end = min((fold + 1) * fold_size, n)
        s_fold = s[start:end]
        r_fold = r[start:end]
        
        sorted_idx = np.argsort(s_fold)
        q = len(sorted_idx) // 5
        if q < 3:
            continue
        top = r_fold[sorted_idx[-q:]]
        bot = r_fold[sorted_idx[:q]]
        spreads.append(np.mean(top) - np.mean(bot))
    
    return np.mean(spreads) if spreads else 0.0


def overfit_score(in_sample_spread: float, walk_forward_spread: float) -> float:
    """0 = no overfit, 1 = complete overfit"""
    if abs(in_sample_spread) < 1e-8:
        return 1.0
    ratio = walk_forward_spread / in_sample_spread
    # If walk-forward has same sign and ≥50% magnitude → low overfit
    if ratio >= 0.5:
        return max(0, 1 - ratio)
    elif ratio > 0:
        return 0.5
    else:
        return 1.0  # sign flipped → overfit


# ═══════════════════════════════════════════════
# RESAMPLING ENGINE
# ═══════════════════════════════════════════════

def resample_time(closes, highs, lows, opens, volumes, timestamps,
                   period_minutes: float) -> List[Bar]:
    """Resample to arbitrary time period (supports fractional minutes)"""
    n = len(closes)
    period_seconds = period_minutes * 60
    
    # If source is 1M data, period in bars = period_minutes
    # For fractional minutes with 1M source, we need to interpolate
    # Simple approach: use ceil/floor alternating
    
    bars = []
    i = 0
    accumulated = 0.0
    
    while i < n:
        # How many source bars for this target bar
        accumulated += period_minutes
        n_source = int(accumulated)
        accumulated -= n_source
        
        if n_source < 1:
            n_source = 1
        
        end = min(i + n_source, n)
        if end <= i:
            break
        
        chunk_c = closes[i:end]
        chunk_h = highs[i:end]
        chunk_l = lows[i:end]
        chunk_o = opens[i:end]
        chunk_v = volumes[i:end]
        
        valid_c = chunk_c[~np.isnan(chunk_c)]
        valid_h = chunk_h[~np.isnan(chunk_h)]
        valid_l = chunk_l[~np.isnan(chunk_l)]
        valid_o = chunk_o[~np.isnan(chunk_o)]
        valid_v = chunk_v[~np.isnan(chunk_v)]
        
        if len(valid_c) > 0 and len(valid_h) > 0:
            bars.append(Bar(
                open=valid_o[0] if len(valid_o) > 0 else valid_c[0],
                high=np.max(valid_h),
                low=np.min(valid_l),
                close=valid_c[-1],
                volume=np.sum(valid_v) if len(valid_v) > 0 else 0,
                count=end - i,
                timestamp=timestamps[i] if timestamps is not None and i < len(timestamps) else 0,
            ))
        
        i = end
    
    return bars


def resample_volume(closes, highs, lows, opens, volumes, timestamps,
                     vol_per_bar: float) -> List[Bar]:
    """Resample by volume — implements 'time doesn't exist'"""
    n = len(closes)
    bars = []
    
    bar_open = np.nan
    bar_high = -np.inf
    bar_low = np.inf
    bar_close = np.nan
    bar_vol = 0.0
    bar_count = 0
    bar_start = 0
    
    for i in range(n):
        if np.isnan(closes[i]) or np.isnan(volumes[i]):
            continue
        
        if np.isnan(bar_open):
            bar_open = opens[i] if not np.isnan(opens[i]) else closes[i]
            bar_start = i
        
        bar_high = max(bar_high, highs[i] if not np.isnan(highs[i]) else closes[i])
        bar_low = min(bar_low, lows[i] if not np.isnan(lows[i]) else closes[i])
        bar_close = closes[i]
        bar_vol += volumes[i]
        bar_count += 1
        
        if bar_vol >= vol_per_bar:
            bars.append(Bar(
                open=bar_open, high=bar_high, low=bar_low,
                close=bar_close, volume=bar_vol, count=bar_count,
                timestamp=timestamps[bar_start] if timestamps is not None else 0,
            ))
            bar_open = np.nan
            bar_high = -np.inf
            bar_low = np.inf
            bar_vol = 0.0
            bar_count = 0
    
    return bars


def resample_entropy(closes, highs, lows, opens, volumes, timestamps,
                      entropy_threshold: float = 2.0,
                      window: int = 10) -> List[Bar]:
    """Entropy clock — new bar when local entropy exceeds threshold"""
    n = len(closes)
    returns = np.diff(closes) / closes[:-1]
    returns = np.concatenate([[0], returns])
    
    bars = []
    bar_open = np.nan
    bar_high = -np.inf
    bar_low = np.inf
    bar_close = np.nan
    bar_vol = 0.0
    bar_count = 0
    bar_start = 0
    ret_buffer = []
    
    for i in range(n):
        if np.isnan(closes[i]):
            continue
        
        if np.isnan(bar_open):
            bar_open = opens[i] if not np.isnan(opens[i]) else closes[i]
            bar_start = i
        
        bar_high = max(bar_high, highs[i] if not np.isnan(highs[i]) else closes[i])
        bar_low = min(bar_low, lows[i] if not np.isnan(lows[i]) else closes[i])
        bar_close = closes[i]
        bar_vol += volumes[i] if not np.isnan(volumes[i]) else 0
        bar_count += 1
        
        if not np.isnan(returns[i]):
            ret_buffer.append(returns[i])
        
        # Check entropy
        if len(ret_buffer) >= window:
            hist, _ = np.histogram(ret_buffer[-window:], bins=5, density=True)
            hist = hist[hist > 0]
            probs = hist / hist.sum()
            h = -np.sum(probs * np.log2(probs))
            
            if h >= entropy_threshold and bar_count >= 3:
                bars.append(Bar(
                    open=bar_open, high=bar_high, low=bar_low,
                    close=bar_close, volume=bar_vol, count=bar_count,
                    timestamp=timestamps[bar_start] if timestamps is not None else 0,
                ))
                bar_open = np.nan
                bar_high = -np.inf
                bar_low = np.inf
                bar_vol = 0.0
                bar_count = 0
                ret_buffer = []
    
    return bars


# ═══════════════════════════════════════════════
# MAIN SCANNER
# ═══════════════════════════════════════════════

class SweetSpotFinder:
    """
    Main engine for finding optimal timeframe.
    
    Usage:
        finder = SweetSpotFinder()
        finder.load_arrays(close, high, low, open, volume, timestamps)
        results = finder.scan(
            tf_range=(2, 60),
            tf_step=1.0,
            clocks=[ClockType.TIME, ClockType.VOLUME],
            sw_list=[15, 21, 27],
            fwd_list=[10, 20],
            validate=True,
        )
        finder.report(results)
    """
    
    def __init__(self, source_tf_minutes: float = 1.0):
        self.source_tf = source_tf_minutes
        self.closes = None
        self.highs = None
        self.lows = None
        self.opens = None
        self.volumes = None
        self.timestamps = None
    
    def load_arrays(self, close, high, low, opn, volume, timestamps=None):
        """Load from numpy arrays"""
        self.closes = np.asarray(close, dtype=np.float64)
        self.highs = np.asarray(high, dtype=np.float64)
        self.lows = np.asarray(low, dtype=np.float64)
        self.opens = np.asarray(opn, dtype=np.float64)
        self.volumes = np.asarray(volume, dtype=np.float64)
        self.timestamps = timestamps
        print(f"Loaded {len(self.closes)} bars ({self.source_tf}M source)")
    
    def load_csv(self, path: str):
        """Load from CSV file"""
        import csv
        rows = []
        with open(path, 'r') as f:
            reader = csv.DictReader(f)
            for r in reader:
                row = {}
                for k, v in r.items():
                    k = k.strip()
                    try:
                        row[k] = float(v.strip())
                    except:
                        row[k] = v.strip()
                rows.append(row)
        
        def g(r, k):
            v = r.get(k)
            if v is None or v == '':
                return np.nan
            try:
                return float(v)
            except:
                return np.nan
        
        self.closes = np.array([g(r, 'close') for r in rows])
        self.highs = np.array([g(r, 'high') for r in rows])
        self.lows = np.array([g(r, 'low') for r in rows])
        self.opens = np.array([g(r, 'open') for r in rows])
        self.volumes = np.array([g(r, 'volume') for r in rows])
        self.timestamps = None
        print(f"Loaded {len(self.closes)} bars from {path}")
    
    def _bars_to_arrays(self, bars: List[Bar]):
        """Convert Bar list to arrays"""
        return (
            np.array([b.close for b in bars]),
            np.array([b.high for b in bars]),
            np.array([b.low for b in bars]),
            np.array([b.open for b in bars]),
            np.array([b.volume for b in bars]),
        )
    
    def _analyze_tf(self, closes, highs, lows, opens, volumes,
                     sw_list, fwd_list, validate) -> dict:
        """Full analysis for one timeframe"""
        n = len(closes)
        
        # Returns
        returns = np.full(n, np.nan)
        returns[1:] = np.diff(closes) / closes[:-1] * 100
        
        valid_ret = returns[~np.isnan(returns)]
        if len(valid_ret) < 30:
            return None
        
        # Kurtosis
        m = np.mean(valid_ret)
        s = np.std(valid_ret)
        kurtosis = np.mean(((valid_ret - m) / s) ** 4) - 3 if s > 0 else 0
        
        # Indicators
        rsi = fast_rsi(closes)
        sk = fast_stoch_k(rsi)
        macd_line, macd_sig, macd_hist = fast_macd(closes)
        vol_ratio = fast_vol_ratio(volumes)
        atr = fast_atr(highs, lows, closes)
        
        # Quantum features
        loc_entropy = local_entropy(returns, window=20)
        frac_dim = fractal_dimension(returns, window=20)
        noise_r = microstructure_noise(returns)
        decoherence = rolling_kurtosis(returns, window=40)
        
        result = {
            'n_bars': n,
            'kurtosis': kurtosis,
            'decoherence': float(np.nanmean(decoherence)) if np.any(~np.isnan(decoherence)) else 0,
        }
        
        # Test all interference pairs × SW × FWD
        best = {}
        
        for pair_name, s1, s2 in [
            ('sk_vr', sk, vol_ratio),
            ('rsi_vr', rsi, vol_ratio),
            ('mh_vr', macd_hist, vol_ratio),
        ]:
            best_spread = 0
            best_config = {}
            
            for sw in sw_list:
                is_score = phase_interference(s1, s2, sw)
                
                for fwd in fwd_list:
                    fwd_ret = forward_returns(closes, fwd)
                    qa = quintile_analysis(is_score, fwd_ret, n_quantiles=5)
                    if qa is None:
                        continue
                    
                    if abs(qa['spread']) > abs(best_spread):
                        best_spread = qa['spread']
                        best_config = {
                            'spread': qa['spread'],
                            'sw': sw,
                            'fwd': fwd,
                            'q5_ret': qa['quantiles'][-1]['avg_return'],
                            'q1_ret': qa['quantiles'][0]['avg_return'],
                            'wr_q5': qa['quantiles'][-1]['wr_up'],
                            'monotonicity': qa['monotonicity'],
                            'correlation': qa['correlation'],
                        }
                        
                        # Validation
                        if validate and abs(qa['spread']) > 0.05:
                            ci = bootstrap_ci(is_score, fwd_ret, n_boot=100)
                            wf = walk_forward_test(is_score, fwd_ret)
                            of = overfit_score(qa['spread'], wf)
                            best_config['bootstrap_ci'] = ci
                            best_config['walk_forward'] = wf
                            best_config['overfit'] = of
            
            for k, v in best_config.items():
                result[f'{pair_name}_{k}'] = v
        
        # Combined interference (RSI × SK × MACD)
        best_comb_spread = 0
        for sw in [21, 27]:
            comb = combined_interference_3(rsi, sk, macd_line, sw)
            for fwd in fwd_list:
                fwd_ret = forward_returns(closes, fwd)
                qa = quintile_analysis(comb, fwd_ret, 5)
                if qa and abs(qa['spread']) > abs(best_comb_spread):
                    best_comb_spread = qa['spread']
        result['combined_spread'] = best_comb_spread
        
        # Quantum feature predictive power
        for feat_name, feat_data in [
            ('entropy', loc_entropy),
            ('fractal', frac_dim),
            ('noise_ratio', noise_r),
        ]:
            best_feat_spread = 0
            for fwd in fwd_list:
                fwd_ret = forward_returns(closes, fwd)
                qa = quintile_analysis(feat_data, fwd_ret, 5)
                if qa and abs(qa['spread']) > abs(best_feat_spread):
                    best_feat_spread = qa['spread']
            result[f'{feat_name}_spread'] = best_feat_spread
        
        return result
    
    def scan(self, tf_range=(2, 60), tf_step=1.0,
             clocks=None,
             sw_list=None, fwd_list=None,
             validate=True,
             vol_bar_counts=None,
             entropy_thresholds=None,
             verbose=True) -> List[dict]:
        """
        Main scan: test all timeframes and clock types.
        
        Args:
            tf_range: (min_tf, max_tf) in minutes
            tf_step: step size in minutes (0.5 for half-minute resolution)
            clocks: list of ClockType to test
            sw_list: smooth windows to test
            fwd_list: forward return periods to test
            validate: run bootstrap + walk-forward
            vol_bar_counts: target bar counts for volume clock
            entropy_thresholds: thresholds for entropy clock
        """
        if clocks is None:
            clocks = [ClockType.TIME]
        if sw_list is None:
            sw_list = [15, 21, 27]
        if fwd_list is None:
            fwd_list = [10, 20]
        if vol_bar_counts is None:
            vol_bar_counts = [200, 500, 1000, 2000]
        if entropy_thresholds is None:
            entropy_thresholds = [1.5, 2.0, 2.3]
        
        results = []
        total_steps = 0
        
        # Count steps
        if ClockType.TIME in clocks:
            total_steps += int((tf_range[1] - tf_range[0]) / tf_step) + 1
        if ClockType.VOLUME in clocks:
            total_steps += len(vol_bar_counts)
        if ClockType.ENTROPY in clocks:
            total_steps += len(entropy_thresholds)
        
        step = 0
        t0 = time_module.time()
        
        # TIME CLOCK
        if ClockType.TIME in clocks:
            tf = tf_range[0]
            while tf <= tf_range[1]:
                step += 1
                if verbose and step % 10 == 0:
                    elapsed = time_module.time() - t0
                    eta = elapsed / step * (total_steps - step)
                    print(f"\r  [{step}/{total_steps}] {tf:.1f}M ... ETA {eta:.0f}s", 
                          end="", flush=True)
                
                bars = resample_time(
                    self.closes, self.highs, self.lows, self.opens,
                    self.volumes, self.timestamps, tf
                )
                
                if len(bars) < 80:
                    tf += tf_step
                    continue
                
                c, h, l, o, v = self._bars_to_arrays(bars)
                analysis = self._analyze_tf(c, h, l, o, v, sw_list, fwd_list, validate)
                
                if analysis:
                    analysis['tf_minutes'] = tf
                    analysis['clock'] = ClockType.TIME.value
                    results.append(analysis)
                
                tf += tf_step
        
        # VOLUME CLOCK
        if ClockType.VOLUME in clocks:
            total_vol = np.nansum(self.volumes)
            for target_bars in vol_bar_counts:
                step += 1
                vol_per_bar = total_vol / target_bars
                
                if verbose:
                    print(f"\r  [{step}/{total_steps}] Volume bars (target {target_bars}) ...", 
                          end="", flush=True)
                
                bars = resample_volume(
                    self.closes, self.highs, self.lows, self.opens,
                    self.volumes, self.timestamps, vol_per_bar
                )
                
                if len(bars) < 80:
                    continue
                
                c, h, l, o, v = self._bars_to_arrays(bars)
                avg_min = len(self.closes) / len(bars) * self.source_tf
                analysis = self._analyze_tf(c, h, l, o, v, sw_list, fwd_list, validate)
                
                if analysis:
                    analysis['tf_minutes'] = avg_min
                    analysis['clock'] = ClockType.VOLUME.value
                    analysis['target_bars'] = target_bars
                    analysis['actual_bars'] = len(bars)
                    results.append(analysis)
        
        # ENTROPY CLOCK
        if ClockType.ENTROPY in clocks:
            for threshold in entropy_thresholds:
                step += 1
                if verbose:
                    print(f"\r  [{step}/{total_steps}] Entropy clock (thresh {threshold}) ...",
                          end="", flush=True)
                
                bars = resample_entropy(
                    self.closes, self.highs, self.lows, self.opens,
                    self.volumes, self.timestamps, threshold
                )
                
                if len(bars) < 80:
                    continue
                
                c, h, l, o, v = self._bars_to_arrays(bars)
                avg_min = len(self.closes) / len(bars) * self.source_tf
                analysis = self._analyze_tf(c, h, l, o, v, sw_list, fwd_list, validate)
                
                if analysis:
                    analysis['tf_minutes'] = avg_min
                    analysis['clock'] = ClockType.ENTROPY.value
                    analysis['entropy_threshold'] = threshold
                    analysis['actual_bars'] = len(bars)
                    results.append(analysis)
        
        if verbose:
            elapsed = time_module.time() - t0
            print(f"\r  Done: {len(results)} configs tested in {elapsed:.1f}s")
        
        # Compute composite scores
        self._score_results(results)
        
        return results
    
    def _score_results(self, results: List[dict]):
        """Compute composite and reliability scores"""
        for r in results:
            # Raw signal strength
            sk = abs(r.get('sk_vr_spread', 0))
            rsi = abs(r.get('rsi_vr_spread', 0))
            mh = abs(r.get('mh_vr_spread', 0))
            comb = abs(r.get('combined_spread', 0))
            ent = abs(r.get('entropy_spread', 0))
            frac = abs(r.get('fractal_spread', 0))
            
            signal_strength = sk * 0.3 + rsi * 0.2 + mh * 0.15 + comb * 0.15 + ent * 0.1 + frac * 0.1
            
            # Reliability: penalize low bar count
            n = r.get('n_bars', 0)
            size_factor = min(1.0, n / 500)  # full reliability at 500+ bars
            
            # Monotonicity bonus
            mono = abs(r.get('sk_vr_monotonicity', 0))
            
            # Overfit penalty
            of = r.get('sk_vr_overfit', 0.5)
            overfit_factor = 1 - of * 0.5
            
            r['signal_strength'] = signal_strength
            r['reliability'] = size_factor
            r['composite_score'] = signal_strength * size_factor * overfit_factor * (0.5 + mono * 0.5)
    
    def report(self, results: List[dict], top_n: int = 20):
        """Print formatted report"""
        sorted_results = sorted(results, key=lambda r: r.get('composite_score', 0), reverse=True)
        
        print("\n" + "=" * 100)
        print("  SWEET SPOT FINDER — RESULTS")
        print("=" * 100)
        
        print(f"\n  {'#':>3s} {'Clock':>7s} {'TF':>6s} {'Bars':>5s} {'Kurt':>6s} │"
              f" {'SK×VR':>8s} {'RSI×VR':>8s} {'Comb':>8s} {'Entropy':>8s} │"
              f" {'Mono':>5s} {'Overfit':>7s} {'Score':>7s}")
        print("  " + "─" * 95)
        
        for i, r in enumerate(sorted_results[:top_n], 1):
            clock = r.get('clock', '?')[:5]
            tf = r.get('tf_minutes', 0)
            n = r.get('n_bars', 0)
            kurt = r.get('kurtosis', 0)
            sk = r.get('sk_vr_spread', 0)
            rsi = r.get('rsi_vr_spread', 0)
            comb = r.get('combined_spread', 0)
            ent = r.get('entropy_spread', 0)
            mono = r.get('sk_vr_monotonicity', 0)
            of = r.get('sk_vr_overfit', 0)
            score = r.get('composite_score', 0)
            
            print(f"  {i:3d} {clock:>7s} {tf:>5.1f}M {n:>5d} {kurt:>6.1f} │"
                  f" {sk:>+8.3f}% {rsi:>+8.3f}% {comb:>+8.3f}% {ent:>+8.3f}% │"
                  f" {mono:>+5.2f} {of:>7.2f} {score:>7.4f}")
        
        # Best by category
        print(f"\n  OPTIMAL per metric:")
        for metric, key in [
            ("StochK×VolR", "sk_vr_spread"),
            ("RSI×VolR", "rsi_vr_spread"),
            ("Combined", "combined_spread"),
            ("Entropy", "entropy_spread"),
            ("Composite", "composite_score"),
        ]:
            best = max(results, key=lambda r: abs(r.get(key, 0)))
            print(f"    {metric:>15s}: {best.get('tf_minutes', 0):>5.1f}M "
                  f"({best.get('clock', '?')}) = {best.get(key, 0):+.4f}%"
                  f" [{best.get('n_bars', 0)} bars]")
    
    def export_features(self, results: List[dict], top_n: int = 3) -> dict:
        """Export optimal configs as feature definitions for predictor"""
        sorted_results = sorted(results, key=lambda r: r.get('composite_score', 0), reverse=True)
        
        configs = []
        for r in sorted_results[:top_n]:
            configs.append({
                'tf_minutes': r.get('tf_minutes'),
                'clock': r.get('clock'),
                'sk_vr_sw': r.get('sk_vr_sw', 21),
                'sk_vr_fwd': r.get('sk_vr_fwd', 20),
                'spread': r.get('sk_vr_spread', 0),
                'composite_score': r.get('composite_score', 0),
            })
        
        return configs


# ═══════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="BTC Timeframe Sweet Spot Finder")
    parser.add_argument("csv_path", help="Path to source CSV (1M or 1S data)")
    parser.add_argument("--source-tf", type=float, default=1.0, 
                        help="Source timeframe in minutes (default: 1.0)")
    parser.add_argument("--tf-min", type=float, default=2.0)
    parser.add_argument("--tf-max", type=float, default=60.0)
    parser.add_argument("--tf-step", type=float, default=1.0)
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--volume-clock", action="store_true")
    parser.add_argument("--entropy-clock", action="store_true")
    parser.add_argument("--top", type=int, default=20)
    
    args = parser.parse_args()
    
    finder = SweetSpotFinder(source_tf_minutes=args.source_tf)
    finder.load_csv(args.csv_path)
    
    clocks = [ClockType.TIME]
    if args.volume_clock:
        clocks.append(ClockType.VOLUME)
    if args.entropy_clock:
        clocks.append(ClockType.ENTROPY)
    
    results = finder.scan(
        tf_range=(args.tf_min, args.tf_max),
        tf_step=args.tf_step,
        clocks=clocks,
        validate=not args.no_validate,
    )
    
    finder.report(results, top_n=args.top)
