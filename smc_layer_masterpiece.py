# smc_layer_masterpiece.py
# ------------------------------------------------------------
# SMC Layer Protocol (Money Atlas) - All-in-One Masterpiece
# Genesis-compatible Multi-Agent Engine + External LLM/Agent Interface
#
# Goal:
#   - Infer "Layer 1-5" price bands & entry windows from OHLCV
#   - Provide confidence + evidence
#   - Designed for future extraction as Genesis Protocol extension
#
# Dependencies: standard library only
# (Optional) If you want speed: replace some routines with numpy/pandas later.
# ------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple, Protocol, Callable, Union
import csv
import math
import statistics
from datetime import datetime
import asyncio


# ============================================================
# 0) Core Data Models (Genesis-friendly)
# ============================================================

@dataclass
class Candle:
    ts: int  # unix seconds
    o: float
    h: float
    l: float
    c: float
    v: float

@dataclass
class LayerBand:
    layer: int                     # 1..5
    price_low: float
    price_high: float
    entry_start_ts: Optional[int]  # estimated accumulation start
    entry_end_ts: Optional[int]    # estimated accumulation end
    cost_basis: Optional[float]    # proxy: AVWAP/HVN centroid
    state: str                     # accumulating/holding/distributing/broken/unknown
    confidence: float              # 0..1
    evidence: List[str] = field(default_factory=list)

@dataclass
class SMCLayerMap:
    symbol: str
    timeframe: str
    start_ts: int
    end_ts: int
    layers: List[LayerBand]
    notes: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class GenesisContext:
    # Genesis Protocol mapping: Will -> Belief -> Behavior -> Structure -> Outcome
    symbol: str
    timeframe: str
    candles: List[Candle]
    params: Dict[str, Any] = field(default_factory=dict)
    traces: Dict[str, Any] = field(default_factory=dict)  # debug/inspection


# ============================================================
# 1) External Agent / LLM Interface (pluggable)
# ============================================================

@dataclass
class AgentTask:
    """A neutral task envelope for internal/external agents."""
    name: str
    instruction: str
    input: Dict[str, Any]
    schema_hint: Dict[str, Any] = field(default_factory=dict)  # optional JSON-schema-like hint

@dataclass
class AgentResult:
    name: str
    output: Dict[str, Any]
    confidence: float = 0.5
    evidence: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

class ExternalAgentAdapter(Protocol):
    """
    Implement this to connect LLM / remote agents.
    You can route tasks to:
      - an LLM function-calling tool
      - a microservice agent swarm
      - another runtime (Node/Go/etc.)
    """
    async def run_task(self, task: AgentTask) -> AgentResult:
        ...

class AgentRegistry:
    """Register optional external adapters by task name prefix or exact match."""
    def __init__(self) -> None:
        self._routes: List[Tuple[str, ExternalAgentAdapter]] = []

    def register(self, task_name_prefix: str, adapter: ExternalAgentAdapter) -> None:
        self._routes.append((task_name_prefix, adapter))

    def resolve(self, task_name: str) -> Optional[ExternalAgentAdapter]:
        # longest prefix match
        best: Optional[Tuple[str, ExternalAgentAdapter]] = None
        for prefix, ad in self._routes:
            if task_name.startswith(prefix):
                if best is None or len(prefix) > len(best[0]):
                    best = (prefix, ad)
        return best[1] if best else None


# ============================================================
# 2) Utility: loading data
# ============================================================

def load_ohlcv_csv(path: str,
                  ts_col: str = "ts",
                  o: str = "open",
                  h: str = "high",
                  l: str = "low",
                  c: str = "close",
                  v: str = "volume",
                  ts_is_ms: bool = False) -> List[Candle]:
    candles: List[Candle] = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_val = int(float(row[ts_col]))
            if ts_is_ms:
                ts_val //= 1000
            candles.append(Candle(
                ts=ts_val,
                o=float(row[o]),
                h=float(row[h]),
                l=float(row[l]),
                c=float(row[c]),
                v=float(row[v]),
            ))
    candles.sort(key=lambda x: x.ts)
    return candles


# ============================================================
# 3) Core Quant Building Blocks (no numpy)
# ============================================================

def sma(values: List[float], window: int) -> List[Optional[float]]:
    if window <= 0:
        raise ValueError("window must be > 0")
    out: List[Optional[float]] = [None] * len(values)
    s = 0.0
    q: List[float] = []
    for i, x in enumerate(values):
        q.append(x)
        s += x
        if len(q) > window:
            s -= q.pop(0)
        if len(q) == window:
            out[i] = s / window
    return out

def atr(candles: List[Candle], window: int = 14) -> List[Optional[float]]:
    tr: List[float] = []
    prev_c = candles[0].c if candles else 0.0
    for cd in candles:
        tr_val = max(cd.h - cd.l, abs(cd.h - prev_c), abs(cd.l - prev_c))
        tr.append(tr_val)
        prev_c = cd.c
    return sma(tr, window)

def rolling_std(values: List[float], window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if i + 1 >= window:
            segment = values[i + 1 - window:i + 1]
            out[i] = statistics.pstdev(segment)
    return out

def percentile(values: List[float], p: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    k = (len(xs) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return xs[int(k)]
    return xs[f] + (xs[c] - xs[f]) * (k - f)

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ============================================================
# 4) Feature Extraction: Pivot, Structure, Volume-at-Price proxy, AVWAP
# ============================================================

def find_swings(candles: List[Candle], left: int = 3, right: int = 3) -> Dict[str, List[int]]:
    """
    Simple swing high/low detection.
    Returns indices of swing highs and lows.
    """
    highs: List[int] = []
    lows: List[int] = []
    n = len(candles)
    for i in range(left, n - right):
        hi = candles[i].h
        lo = candles[i].l
        is_hi = all(hi >= candles[j].h for j in range(i - left, i + right + 1))
        is_lo = all(lo <= candles[j].l for j in range(i - left, i + right + 1))
        if is_hi:
            highs.append(i)
        if is_lo:
            lows.append(i)
    return {"highs": highs, "lows": lows}

def anchored_vwap(candles: List[Candle], anchor_idx: int, price_field: str = "c") -> List[Optional[float]]:
    """
    AVWAP from anchor to end. Uses typical price (h+l+c)/3 weighted by volume.
    """
    out: List[Optional[float]] = [None] * len(candles)
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(anchor_idx, len(candles)):
        cd = candles[i]
        tp = (cd.h + cd.l + cd.c) / 3.0
        cum_pv += tp * cd.v
        cum_v += cd.v
        out[i] = (cum_pv / cum_v) if cum_v > 0 else None
    return out

def volume_nodes_proxy(candles: List[Candle], bins: int = 60) -> List[Tuple[float, float, float]]:
    """
    Approx volume-at-price using candle typical price; distribute full volume into a bin.
    Returns list of (bin_low, bin_high, vol).
    """
    if not candles:
        return []
    prices = [(cd.h + cd.l + cd.c) / 3.0 for cd in candles]
    lo = min(prices)
    hi = max(prices)
    if hi <= lo:
        return [(lo, hi, sum(cd.v for cd in candles))]

    bin_size = (hi - lo) / bins
    vols = [0.0] * bins
    for cd in candles:
        tp = (cd.h + cd.l + cd.c) / 3.0
        idx = int((tp - lo) / bin_size)
        idx = max(0, min(bins - 1, idx))
        vols[idx] += cd.v

    nodes: List[Tuple[float, float, float]] = []
    for i, v in enumerate(vols):
        b0 = lo + i * bin_size
        b1 = lo + (i + 1) * bin_size
        nodes.append((b0, b1, v))
    return nodes

def top_hvn(nodes: List[Tuple[float, float, float]], k: int = 5) -> List[Tuple[float, float, float]]:
    return sorted(nodes, key=lambda x: x[2], reverse=True)[:k]


# ============================================================
# 5) Internal Agents (can be swapped with external via registry)
# ============================================================

class BaseAgent:
    name: str

    async def run(self, ctx: GenesisContext) -> AgentResult:
        raise NotImplementedError

class StructureAgent(BaseAgent):
    name = "internal.structure"

    async def run(self, ctx: GenesisContext) -> AgentResult:
        swings = find_swings(ctx.candles,
                             left=int(ctx.params.get("swing_left", 3)),
                             right=int(ctx.params.get("swing_right", 3)))
        # Determine a macro "base" and "impulse" boundary:
        # Heuristic: base ends at first major BOS = close breaks above last swing high after a swing low.
        closes = [c.c for c in ctx.candles]
        swing_highs = swings["highs"]
        swing_lows = swings["lows"]

        base_end = None
        if swing_lows and swing_highs:
            # pick earliest significant swing low and then break above nearest swing high
            for li in swing_lows:
                # find a swing high after li
                hi_candidates = [hi for hi in swing_highs if hi > li]
                if not hi_candidates:
                    continue
                hi0 = hi_candidates[0]
                level = ctx.candles[hi0].h
                # bos: close above that level later
                for j in range(hi0 + 1, len(closes)):
                    if closes[j] > level:
                        base_end = j
                        break
                if base_end is not None:
                    break

        if base_end is None:
            base_end = max(0, int(len(ctx.candles) * 0.35))

        out = {
            "swings": swings,
            "base_end_idx": base_end,
            "base_start_idx": 0,
            "impulse_start_idx": base_end,
            "impulse_end_idx": len(ctx.candles) - 1
        }
        evidence = [
            f"swings highs={len(swings['highs'])}, lows={len(swings['lows'])}",
            f"base_end_idx={base_end}"
        ]
        return AgentResult(name=self.name, output=out, confidence=0.65, evidence=evidence)

class VolumeAgent(BaseAgent):
    name = "internal.volume"

    async def run(self, ctx: GenesisContext) -> AgentResult:
        nodes = volume_nodes_proxy(ctx.candles, bins=int(ctx.params.get("vap_bins", 60)))
        hvn = top_hvn(nodes, k=int(ctx.params.get("hvn_topk", 6)))
        out = {"vap_nodes": nodes, "hvn": hvn}
        evidence = [f"vap bins={len(nodes)}", f"hvn_top={[(round(a,4),round(b,4),round(v,2)) for a,b,v in hvn[:3]]}"]
        return AgentResult(name=self.name, output=out, confidence=0.7, evidence=evidence)

class CostBasisAgent(BaseAgent):
    name = "internal.cost_basis"

    async def run(self, ctx: GenesisContext) -> AgentResult:
        swings = ctx.traces.get("structure", {}).get("swings")  # may exist
        if not swings:
            swings = find_swings(ctx.candles, 3, 3)
        swing_lows = swings["lows"]
        # choose a few anchors (earliest base low, mid-cycle low, late-cycle low)
        anchors = []
        if swing_lows:
            anchors.append(swing_lows[0])
            anchors.append(swing_lows[len(swing_lows)//2])
            anchors.append(swing_lows[-1])
            anchors = sorted(set(anchors))
        else:
            anchors = [0, len(ctx.candles)//2]

        avwaps = {}
        for a in anchors:
            av = anchored_vwap(ctx.candles, a)
            avwaps[str(a)] = av

        out = {"anchors": anchors, "avwaps": avwaps}
        evidence = [f"anchors={anchors}", "avwap=typical_price*volume"]
        return AgentResult(name=self.name, output=out, confidence=0.66, evidence=evidence)

class RegimeAgent(BaseAgent):
    name = "internal.regime"

    async def run(self, ctx: GenesisContext) -> AgentResult:
        closes = [c.c for c in ctx.candles]
        rstd = rolling_std(closes, window=int(ctx.params.get("vol_window", 20)))
        atrv = atr(ctx.candles, window=int(ctx.params.get("atr_window", 14)))
        # regime proxy: compression when std and atr both below 30th percentile
        std_vals = [x for x in rstd if x is not None]
        atr_vals = [x for x in atrv if x is not None]
        std_p30 = percentile(std_vals, 0.30) if std_vals else 0.0
        atr_p30 = percentile(atr_vals, 0.30) if atr_vals else 0.0

        compression = []
        for i in range(len(closes)):
            if rstd[i] is None or atrv[i] is None:
                compression.append(False)
            else:
                compression.append((rstd[i] <= std_p30) and (atrv[i] <= atr_p30))

        out = {"compression_mask": compression, "std_p30": std_p30, "atr_p30": atr_p30}
        evidence = [f"std_p30={std_p30:.6f}", f"atr_p30={atr_p30:.6f}"]
        return AgentResult(name=self.name, output=out, confidence=0.6, evidence=evidence)

class AccumDistAgent(BaseAgent):
    name = "internal.accum_dist"

    async def run(self, ctx: GenesisContext) -> AgentResult:
        # Simple absorption proxy:
        # - Up move with rising volume but narrowing range -> possible distribution
        # - Down move with rising volume but failure to break prior low -> absorption
        candles = ctx.candles
        ranges = [cd.h - cd.l for cd in candles]
        vols = [cd.v for cd in candles]
        r_sma = sma(ranges, window=int(ctx.params.get("range_window", 14)))
        v_sma = sma(vols, window=int(ctx.params.get("vol_sma_window", 14)))

        dist_flags = [False] * len(candles)
        abs_flags = [False] * len(candles)

        for i in range(len(candles)):
            if r_sma[i] is None or v_sma[i] is None:
                continue
            # distribution: close up vs prev close, vol above avg, range below avg
            if i > 0 and candles[i].c > candles[i-1].c and vols[i] > v_sma[i] and ranges[i] < r_sma[i]:
                dist_flags[i] = True
            # absorption: close down, vol above avg, range below avg
            if i > 0 and candles[i].c < candles[i-1].c and vols[i] > v_sma[i] and ranges[i] < r_sma[i]:
                abs_flags[i] = True

        out = {"distribution_flags": dist_flags, "absorption_flags": abs_flags}
        evidence = ["dist: up close + high vol + narrow range", "abs: down close + high vol + narrow range"]
        return AgentResult(name=self.name, output=out, confidence=0.55, evidence=evidence)

class VerifierAgent(BaseAgent):
    name = "internal.verifier"

    async def run(self, ctx: GenesisContext) -> AgentResult:
        # This agent doesn't decide layers; it scores consistency.
        # Consistency checks:
        # - base_end exists
        # - hvn exists
        # - avwap anchors exist
        structure = ctx.traces.get("structure", {})
        volume = ctx.traces.get("volume", {})
        costb = ctx.traces.get("cost_basis", {})

        ok = 0
        need = 3
        ev = []
        if "base_end_idx" in structure:
            ok += 1
            ev.append("structure: base_end_idx present")
        if volume.get("hvn"):
            ok += 1
            ev.append("volume: hvn present")
        if costb.get("anchors"):
            ok += 1
            ev.append("cost_basis: anchors present")

        conf = ok / need
        return AgentResult(name=self.name, output={"checks_ok": ok, "checks_need": need}, confidence=conf, evidence=ev)


# ============================================================
# 6) Layer Resolver (the heart): Build Layer bands + entry windows
# ============================================================

def _segment_by_indices(candles: List[Candle], i0: int, i1: int) -> List[Candle]:
    i0 = max(0, i0)
    i1 = min(len(candles)-1, i1)
    if i1 < i0:
        return []
    return candles[i0:i1+1]

def _weighted_centroid_price(candles: List[Candle]) -> Optional[float]:
    if not candles:
        return None
    num = 0.0
    den = 0.0
    for cd in candles:
        tp = (cd.h + cd.l + cd.c) / 3.0
        num += tp * cd.v
        den += cd.v
    return (num / den) if den > 0 else None

def _find_longest_true_window(mask: List[bool], start_idx: int, end_idx: int) -> Tuple[Optional[int], Optional[int], int]:
    best = (None, None, 0)
    cur_s = None
    cur_len = 0
    for i in range(start_idx, end_idx + 1):
        if mask[i]:
            if cur_s is None:
                cur_s = i
                cur_len = 1
            else:
                cur_len += 1
        else:
            if cur_s is not None and cur_len > best[2]:
                best = (cur_s, i - 1, cur_len)
            cur_s = None
            cur_len = 0
    if cur_s is not None and cur_len > best[2]:
        best = (cur_s, end_idx, cur_len)
    return best

def resolve_layers(ctx: GenesisContext) -> List[LayerBand]:
    candles = ctx.candles
    structure = ctx.traces["structure"]
    volume = ctx.traces["volume"]
    costb = ctx.traces["cost_basis"]
    regime = ctx.traces["regime"]
    accd = ctx.traces["accum_dist"]

    base_end = int(structure["base_end_idx"])
    n = len(candles)

    # Define rough cycle zones (can be improved later):
    base_zone = (0, base_end)
    early_zone = (base_end, int(n * 0.55))
    mid_zone = (int(n * 0.45), int(n * 0.75))
    late_zone = (int(n * 0.70), n - 1)
    peak_zone = (int(n * 0.85), n - 1)

    # HVN candidates: map to a "price band"
    hvn = volume.get("hvn", [])
    hvn_sorted = sorted(hvn, key=lambda x: x[2], reverse=True)

    def band_from_zone(z: Tuple[int,int], widen: float = 0.0) -> Tuple[float,float,Optional[float],List[str]]:
        seg = _segment_by_indices(candles, z[0], z[1])
        if not seg:
            return (float("nan"), float("nan"), None, ["empty zone"])
        lo = min(cd.l for cd in seg)
        hi = max(cd.h for cd in seg)
        if widen > 0:
            span = (hi - lo)
            lo -= span * widen
            hi += span * widen
        cb = _weighted_centroid_price(seg)
        return (lo, hi, cb, [f"zone={z[0]}..{z[1]}", f"centroid={cb:.6f}" if cb else "centroid=None"])

    compression = regime["compression_mask"]

    # Entry windows: longest compression window inside each zone
    def entry_window(z: Tuple[int,int]) -> Tuple[Optional[int], Optional[int], float, List[str]]:
        s, e, ln = _find_longest_true_window(compression, z[0], z[1])
        if s is None:
            # fallback: last 20% of zone
            ss = z[0] + int((z[1]-z[0]) * 0.6)
            ee = z[1]
            return (candles[ss].ts, candles[ee].ts, 0.35, [f"fallback window idx={ss}..{ee}"])
        # confidence scaled by window length
        conf = clamp(ln / max(10, (z[1]-z[0]+1)), 0.35, 0.85)
        return (candles[s].ts, candles[e].ts, conf, [f"compression window idx={s}..{e} len={ln}"])

    # Layer 1: base zone + strongest HVN in base
    l1_lo, l1_hi, l1_cb, l1_ev = band_from_zone(base_zone, widen=0.05)
    l1_s, l1_e, l1_wconf, l1_wev = entry_window(base_zone)

    # # Layer 2: early zone
    l2_lo, l2_hi, l2_cb, l2_ev = band_from_zone(early_zone, widen=0.04)
    l2_s, l2_e, l2_wconf, l2_wev = entry_window(early_zone)

    # Layer 3: mid zone (decision)
    l3_lo, l3_hi, l3_cb, l3_ev = band_from_zone(mid_zone, widen=0.03)
    l3_s, l3_e, l3_wconf, l3_wev = entry_window(mid_zone)

    # Layer 4: late zone
    l4_lo, l4_hi, l4_cb, l4_ev = band_from_zone(late_zone, widen=0.02)
    l4_s, l4_e, l4_wconf, l4_wev = entry_window(late_zone)

    # Layer 5: peak zone
    l5_lo, l5_hi, l5_cb, l5_ev = band_from_zone(peak_zone, widen=0.01)
    l5_s, l5_e, l5_wconf, l5_wev = entry_window(peak_zone)

    # State inference (simple heuristic):
    dist_flags = accd["distribution_flags"]
    abs_flags = accd["absorption_flags"]

    def state_for_zone(z: Tuple[int,int]) -> Tuple[str,float,List[str]]:
        d = sum(1 for i in range(z[0], z[1]+1) if dist_flags[i])
        a = sum(1 for i in range(z[0], z[1]+1) if abs_flags[i])
        total = max(1, z[1]-z[0]+1)
        d_rate = d / total
        a_rate = a / total
        ev = [f"dist_rate={d_rate:.3f}", f"abs_rate={a_rate:.3f}"]
        if d_rate > 0.10 and d_rate > a_rate:
            return ("distributing", clamp(0.55 + d_rate, 0.55, 0.85), ev)
        if a_rate > 0.10 and a_rate > d_rate:
            return ("accumulating", clamp(0.55 + a_rate, 0.55, 0.85), ev)
        return ("holding", 0.55, ev)

    l1_state, l1_sconf, l1_sev = state_for_zone(base_zone)
    l2_state, l2_sconf, l2_sev = state_for_zone(early_zone)
    l3_state, l3_sconf, l3_sev = state_for_zone(mid_zone)
    l4_state, l4_sconf, l4_sev = state_for_zone(late_zone)
    l5_state, l5_sconf, l5_sev = state_for_zone(peak_zone)

    # Final confidence: combine window confidence + state confidence + verifier confidence
    verifier_conf = float(ctx.traces.get("verifier", {}).get("_confidence", 0.6))

    def combine_conf(a: float, b: float, c: float) -> float:
        return clamp(0.45*a + 0.35*b + 0.20*c, 0.0, 1.0)

    layers = [
        LayerBand(
            layer=1,
            price_low=l1_lo, price_high=l1_hi,
            entry_start_ts=l1_s, entry_end_ts=l1_e,
            cost_basis=l1_cb,
            state=l1_state,
            confidence=combine_conf(l1_wconf, l1_sconf, verifier_conf),
            evidence=(l1_ev + l1_wev + l1_sev + [f"verifier={verifier_conf:.2f}"])
        ),
        LayerBand(
            layer=2,
            price_low=l2_lo, price_high=l2_hi,
            entry_start_ts=l2_s, entry_end_ts=l2_e,
            cost_basis=l2_cb,
            state=l2_state,
            confidence=combine_conf(l2_wconf, l2_sconf, verifier_conf),
            evidence=(l2_ev + l2_wev + l2_sev + [f"verifier={verifier_conf:.2f}"])
        ),
        LayerBand(
            layer=3,
            price_low=l3_lo, price_high=l3_hi,
            entry_start_ts=l3_s, entry_end_ts=l3_e,
            cost_basis=l3_cb,
            state=l3_state,
            confidence=combine_conf(l3_wconf, l3_sconf, verifier_conf),
            evidence=(l3_ev + l3_wev + l3_sev + [f"verifier={verifier_conf:.2f}"])
        ),
        LayerBand(
            layer=4,
            price_low=l4_lo, price_high=l4_hi,
            entry_start_ts=l4_s, entry_end_ts=l4_e,
            cost_basis=l4_cb,
            state=l4_state,
            confidence=combine_conf(l4_wconf, l4_sconf, verifier_conf),
            evidence=(l4_ev + l4_wev + l4_sev + [f"verifier={verifier_conf:.2f}"])
        ),
        LayerBand(
            layer=5,
            price_low=l5_lo, price_high=l5_hi,
            entry_start_ts=l5_s, entry_end_ts=l5_e,
            cost_basis=l5_cb,
            state=l5_state,
            confidence=combine_conf(l5_wconf, l5_sconf, verifier_conf),
            evidence=(l5_ev + l5_wev + l5_sev + [f"verifier={verifier_conf:.2f}"])
        ),
    ]

    # Optional: refine with HVN hints (snap cost_basis toward nearest HVN band centroid)
    # (kept minimal in v1; deeper logic can be private signature later)
    if hvn_sorted:
        for lb in layers:
            # find HVN that overlaps layer price band
            overlaps = [n for n in hvn_sorted if not (n[1] < lb.price_low or n[0] > lb.price_high)]
            if overlaps:
                b0, b1, vol = overlaps[0]
                hvn_centroid = (b0 + b1) / 2.0
                if lb.cost_basis is not None:
                    lb.cost_basis = (lb.cost_basis * 0.7) + (hvn_centroid * 0.3)
                lb.evidence.append(f"hvn_hint centroid={hvn_centroid:.6f} vol={vol:.2f}")

    return layers
  # ============================================================
# 7) Orchestrator (Multi-Agent + External hooks)
# ============================================================

class SMCLayerEngine:
    def __init__(self, registry: Optional[AgentRegistry] = None) -> None:
        self.registry = registry or AgentRegistry()

        # Default internal agents (can be overridden externally by registering matching prefix)
        self.internal_agents: List[BaseAgent] = [
            StructureAgent(),
            VolumeAgent(),
            CostBasisAgent(),
            RegimeAgent(),
            AccumDistAgent(),
            VerifierAgent(),
        ]

    async def _run_agent(self, agent: BaseAgent, ctx: GenesisContext) -> AgentResult:
        # If an external adapter is registered for this agent's name, use it
        adapter = self.registry.resolve(agent.name)
        if adapter is None:
            return await agent.run(ctx)

        # External task schema: keep stable for LLM tool calling
        task = AgentTask(
            name=agent.name,
            instruction=(
                "You are a specialized analysis agent within Money Atlas SMC Layer Protocol.\n"
                "Return output strictly as JSON-compatible dict.\n"
                "Prefer numeric indices, price levels, and concise evidence."
            ),
            input={
                "symbol": ctx.symbol,
                "timeframe": ctx.timeframe,
                "params": ctx.params,
                "candles_head": [asdict(c) for c in ctx.candles[:200]],
                "candles_tail": [asdict(c) for c in ctx.candles[-200:]],
                "traces_so_far": ctx.traces,  # may be large; in real LLM use summarize
            },
            schema_hint={
                "type": "object",
                "properties": {
                    "output": {"type": "object"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["output"]
            }
        )
        return await adapter.run_task(task)

    async def analyze(self, symbol: str, timeframe: str, candles: List[Candle],
                      params: Optional[Dict[str, Any]] = None) -> SMCLayerMap:
        if not candles or len(candles) < 100:
            raise ValueError("Need at least ~100 candles for meaningful layer inference.")

        ctx = GenesisContext(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            params=params or {},
            traces={}
        )

        # Run agents sequentially (determinism). You can parallelize later.
        for agent in self.internal_agents:
            res = await self._run_agent(agent, ctx)
            # store trace
            key = agent.name.split(".")[-1]
            ctx.traces[key] = res.output
            # also keep confidence in trace for verifier
            ctx.traces[key]["_confidence"] = res.confidence
            ctx.traces[key]["_evidence"] = res.evidence
            if res.warnings:
                ctx.traces[key]["_warnings"] = res.warnings

            # allow later agents to read early outputs conveniently
            if key == "structure":
                # share swings directly for cost-basis agent
                ctx.traces["structure"] = res.output

        # Post: layer resolution
        layers = resolve_layers(ctx)

        sm = SMCLayerMap(
            symbol=symbol,
            timeframe=timeframe,
            start_ts=candles[0].ts,
            end_ts=candles[-1].ts,
            layers=layers,
            notes=[
                "This is a cost-basis proxy inference, not true holder cost basis.",
                "Confidence is ensemble of regime window + acc/dist signals + verifier checks.",
            ],
            meta={"params": ctx.params}
        )
        return sm


#============================================================
# 8) Helper: Pretty print & timestamp formatting
# ============================================================

def ts_to_str(ts: Optional[int]) -> str:
    if ts is None:
        return "n/a"
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")

def format_layer_map(lm: SMCLayerMap) -> str:
    lines = []
    lines.append(f"SMC Layer Map | {lm.symbol} | {lm.timeframe}")
    lines.append(f"Range: {ts_to_str(lm.start_ts)} -> {ts_to_str(lm.end_ts)}")
    lines.append("-" * 72)
    for lb in lm.layers:
        lines.append(
            f"Layer {lb.layer}: {lb.price_low:.4f} - {lb.price_high:.4f} | "
            f"Entry: {ts_to_str(lb.entry_start_ts)} -> {ts_to_str(lb.entry_end_ts)} | "
            f"CostBasis~ {lb.cost_basis:.4f if lb.cost_basis is not None else float('nan')} | "
            f"State: {lb.state} | Conf: {lb.confidence:.2f}"
        )
        # show top evidence lines
        for ev in lb.evidence[:4]:
            lines.append(f"  - {ev}")
    if lm.notes:
        lines.append("-" * 72)
        for n in lm.notes:
            lines.append(f"Note: {n}")
    return "\n".join(lines)


# ============================================================
# 9) Demo main (local run)
# ============================================================

async def _demo():
    # Example:
    # candles = load_ohlcv_csv("XAGUSD_4H.csv", ts_col="ts", ts_is_ms=False)
    # engine = SMCLayerEngine()
    # lm = await engine.analyze(symbol="XAGUSD", timeframe="4H", candles=candles, params={"vap_bins": 80})
    # print(format_layer_map(lm))
    pass

if __name__ == "__main__":
    # To run demo:
    #   1) Prepare CSV with columns: ts, open, high, low, close, volume
    #   2) Uncomment in _demo and provide path
    asyncio.run(_demo())
