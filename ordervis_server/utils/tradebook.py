
import lib.pywangcai_orderbook as wc
import time
from time import perf_counter as _perf_counter
import threading
import pandas as pd
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Callable, Union, List
from threading import Lock
import os

from ordervis_server.package import backend_logger

# 连续竞价开始时间（引擎 market_data 在该时间前无效）
CONTINUOUS_AUCTION_START_MS = 9 * 3600000 + 30 * 60000  # 09:30:00.000
AFTERNOON_START_MS = 13 * 3600000                       # 13:00:00.000
CLOSE_MS = 15 * 3600000                                 # 15:00:00.000

# 定向状态预热参数（见 _prewarm_states_worker）：网格粒度捕获 ≥10ms 的
# 盘口状态（002156 密集时段状态间隔可低至 20–30ms，25ms 网格会漏掉一批，
# 请求采样网格相位一偏就踩中漏网状态——曾致连跳尖刺 4s）；前向按状态数
# 预算自适应；后向覆盖「窗口宽度 + 常见回看步长」；每次真实构建后让出
# GIL 的时间；唤醒后先静默等待（连跳期间 worker 不启动，避免与请求抢
# GIL 重复构建）。快照 LRU 上限 1024：实测 002156 极端高频日单快照深度
# 内存约 67KB，1024 条上限约 68MB，且能同时容纳预热范围与多个窗口的
# 请求工作集（256 时两三个窗口就互相挤掉）。
PREWARM_GRID_STEP_MS = 10
PREWARM_FORWARD_STATES = 96
PREWARM_BACKWARD_MS = 4000
PREWARM_BUILD_THROTTLE_S = 0.01
PREWARM_DEBOUNCE_S = 0.3
SNAPSHOT_CACHE_LIMIT = 1024


def _time_to_ms(time_str: str) -> int:
    """'HH:MM:SS.mmm' -> 毫秒数"""
    if '.' not in time_str:
        time_str += '.000'
    h, m, rest = time_str.split(':')
    s, ms = rest.split('.')
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def _ms_to_time(ms_value: int) -> str:
    """毫秒数 -> 'HH:MM:SS.mmm'"""
    h, ms_value = divmod(ms_value, 3600000)
    m, ms_value = divmod(ms_value, 60000)
    s, ms = divmod(ms_value, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


# 默认数据目录固定到服务端目录，不依赖启动时的当前工作目录。
DEFAULT_DATA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
os.makedirs(DEFAULT_DATA_PATH, exist_ok=True)


class TradeBook:
    def __init__(self, symbol: str, date: str, data_path: str = DEFAULT_DATA_PATH, progress_callback: Optional[Callable] = None, is_ETF: bool = False):
        """初始化TradeBook"""
        self.symbol = symbol
        self.date = date
        self.data_path = data_path
        self.is_ETF = is_ETF
        self.visualizer = wc.TradingVisualizer(symbol, date, data_path, is_ETF)
        
        # 使用进度回调运行初始化
        if progress_callback:
            self.visualizer.run_with_progress(progress_callback)
        else:
            self.visualizer.run()
            
        self.last_accessed_at = datetime.now()
        self._lock = Lock()

        # 日志
        self.logger = backend_logger.Log("tradebook")
        self.log_level = backend_logger.LogLevel

        # orderid(=快照order_local_id) -> 下单时间 的轻量映射（后台懒构建）
        self._order_time_map: Optional[Dict[int, str]] = None
        self._map_building = False
        self._map_lock = Lock()

        # 逐笔数据缓存（懒加载）
        self._csord_df: Optional[pd.DataFrame] = None
        self._cstra_df: Optional[pd.DataFrame] = None
        self._tick_lock = Lock()
        self._order_price_map: Optional[Dict[int, float]] = None
        # 撤单修补记录缓存：{档位序号(0=一档, 1=二档): [records]}
        self._cancel_records_cache: Dict[int, List[Dict]] = {}
        self._trade_price_records: Optional[List[Dict]] = None
        self._order_meta_map: Optional[Dict[int, Dict[str, Any]]] = None
        self._passive_trade_records: Optional[List[Dict[str, Any]]] = None

        # 快照 LRU 缓存：query_by_time 每次要让引擎构建完整六档快照（毫秒级），
        # C2 流量图与锁定订单图对同一窗口的 60 个采样点会重复查询同一批时刻，
        # 命中缓存后第二个请求几乎零成本。
        self._snapshot_cache: "OrderedDict[str, Dict]" = OrderedDict()
        self._snapshot_cache_lock = Lock()

        # 后台构建 create_time 映射，不阻塞初始化
        threading.Thread(target=self._build_order_time_map, daemon=True).start()
        # 后台预热派生缓存（撤单修补/成交价记录），让首次跳转/首次图表加载
        # 不再付出全量构建成本
        threading.Thread(target=self._prewarm_derived_caches, daemon=True).start()

        # 定向状态预热：用户停留时刻（anchor）驱动的快照预建。查询方法
        # 完成后记录锚点并唤醒常驻 worker，generation 递增使旧扫描作废
        # （见 _prewarm_states_worker）。_prewarm_busy 仅供测试/观测。
        self._prewarm_anchor_ms: Optional[int] = None
        self._prewarm_generation = 0
        self._prewarm_wakeup = threading.Event()
        self._prewarm_busy = False
        threading.Thread(target=self._prewarm_states_worker, daemon=True).start()

    # ---------- create_time 映射（B2） ----------

    def _csord_path(self) -> str:
        return os.path.join(self.data_path, f"csord_{self.symbol}_{self.date}.csv")

    def _cstra_path(self) -> str:
        return os.path.join(self.data_path, f"cstra_{self.symbol}_{self.date}.csv")

    def _build_order_time_map(self):
        """后台构建 orderid -> datetime 映射，并抽样自检对齐命中率"""
        with self._map_lock:
            if self._map_building or self._order_time_map is not None:
                return
            self._map_building = True
        try:
            path = self._csord_path()
            if not os.path.exists(path):
                self.logger.n_log(f"create_time 映射构建跳过，文件不存在: {path}", self.log_level.WARNING)
                return
            df = pd.read_csv(path, usecols=['orderid', 'datetime'])
            mapping = dict(zip(df['orderid'].astype('int64'), df['datetime'].astype(str)))
            self._order_time_map = mapping
            self.logger.n_log(f"create_time 映射构建完成: {self.get_key()}, {len(mapping)} 条", self.log_level.INFO)
            self._selfcheck_alignment()
        except Exception as e:
            self.logger.n_log(f"create_time 映射构建失败: {self.get_key()}, 错误: {e}", self.log_level.ERROR)
        finally:
            with self._map_lock:
                self._map_building = False

    def _selfcheck_alignment(self, sample_size: int = 100):
        """抽样验证快照订单 order_local_id 在映射中的命中率，低于95%打warning
        （上交所 csord 经 sh_revert_module 重建，订单号口径可能不同）"""
        try:
            total = self.visualizer.get_total_changes()
            if total <= 0:
                return
            snap = self.visualizer.query_by_change_index(total // 2)
            local_ids = [
                int(o['order_local_id'])
                for level in snap.get('levels', {}).values()
                for o in level.get('orders', [])
            ][:sample_size]
            if not local_ids:
                return
            hit = sum(1 for lid in local_ids if lid in self._order_time_map)
            rate = hit / len(local_ids)
            if rate < 0.95:
                self.logger.n_log(
                    f"[对齐自检] {self.get_key()} order_local_id 命中率仅 {rate*100:.1f}% "
                    f"({hit}/{len(local_ids)})，create_time 可能大面积缺失，请检查该标的的 orderid 口径",
                    self.log_level.WARNING,
                )
            else:
                self.logger.n_log(f"[对齐自检] {self.get_key()} 命中率 {rate*100:.1f}%", self.log_level.INFO)
        except Exception as e:
            self.logger.n_log(f"[对齐自检] {self.get_key()} 异常: {e}", self.log_level.ERROR)

    def _enrich_snapshot_create_time(self, snapshot: Optional[Dict]) -> Optional[Dict]:
        """为快照中的每笔订单补充 create_time（映射未就绪时保持原样）"""
        if snapshot is None or self._order_time_map is None:
            return snapshot
        mapping = self._order_time_map
        for level in snapshot.get('levels', {}).values():
            for order in level.get('orders', []):
                try:
                    order['create_time'] = mapping.get(int(order.get('order_local_id')))
                except (TypeError, ValueError):
                    order['create_time'] = None
        return snapshot

    # ---------- 快照缓存与预热 ----------

    def _query_by_time_cached(self, time_str: str, from_prewarm: bool = False) -> Optional[Dict]:
        """query_by_time 的 LRU 缓存包装，以状态指纹（快照 timestamp）为键。

        任意时刻 T 的快照由「最后一个 ts<=T 的盘口变化」唯一决定，md/ts
        相同的两个时刻返回的是同一份状态。用状态时刻而非查询时刻做键，
        不同窗口、相邻跳转的采样点只要落在同一状态就共享缓存条目。
        共享安全性：所有调用方对快照只读，唯一例外是
        _enrich_snapshot_create_time 往 order dict 里补 create_time —— 该写入
        幂等（同一订单映射恒定），重复 enrich 结果一致，无需拷贝。

        from_prewarm=True 时不打构建耗时日志（预热线程批量构建会刷屏）。
        """
        # md（微秒级）先定位状态时刻；引擎越界等异常时退回按查询时刻做键
        md = self.visualizer.query_market_data(self.date, time_str)
        key = (md or {}).get('timestamp') or time_str
        with self._snapshot_cache_lock:
            snap = self._snapshot_cache.get(key)
            if snap is not None:
                self._snapshot_cache.move_to_end(key)
                return snap
        t0 = _perf_counter()
        snap = self.visualizer.query_by_time(self.date, time_str)
        if not from_prewarm:
            self.logger.n_log(
                f"[PERF] 快照构建(缓存未命中): {self.get_key()} {time_str} → "
                f"{(_perf_counter() - t0) * 1000:.1f}ms",
                self.log_level.INFO,
            )
        if snap is not None:
            # 以快照自身的 timestamp 为准（md 定位存在滞后 1 位的已知坑，
            # 键不一致只会降低命中率，不会取错状态）
            key = snap.get('timestamp') or key
            with self._snapshot_cache_lock:
                self._snapshot_cache[key] = snap
                if len(self._snapshot_cache) > SNAPSHOT_CACHE_LIMIT:
                    self._snapshot_cache.popitem(last=False)
        return snap

    def _window_market_snapshots(self, sample_times: List[str]):
        """窗口采样点的 (market_data 列表, 完整快照列表)。

        引擎 query_market_data（微秒级）能给出每个采样点所处的
        change_index/timestamp；短窗口内相邻采样点大多处于同一盘口状态
        （实测 3s 窗口 61 点只有约 5 个状态），而逐点 query_by_time
        （毫秒级）会重复构建同一份六档快照。因此先按 (change_index,
        timestamp) 把采样点分组，每组只用组内最后时刻查一次快照
        （走 LRU 缓存，与其它窗口的采样点共享）。

        已知引擎坑位：md 的 change_index 在部分时刻滞后快照 1 位。滞后
        只影响绝对值不影响分组正确性；组状态以组内最后时刻的真实快照
        为准（即各桶右缘口径），md 定位失效的极端边界最多让一根桶取到
        相邻状态的一档量。
        """
        mds = [self.visualizer.query_market_data(self.date, t) or {} for t in sample_times]
        keys = [(md.get('change_index'), md.get('timestamp')) for md in mds]
        snapshots: List[Optional[Dict]] = [None] * len(sample_times)
        i = 0
        while i < len(sample_times):
            j = i
            while j + 1 < len(sample_times) and keys[j + 1] == keys[i]:
                j += 1
            snap = self._query_by_time_cached(sample_times[j])
            for k in range(i, j + 1):
                snapshots[k] = snap
            i = j + 1
        return mds, snapshots

    def _prewarm_derived_caches(self):
        """后台预热撤单修补与成交价记录缓存，避免首次查询时同步构建。"""
        try:
            self._cancel_records_at_level(0)
            self._cancel_records_at_level(1)
            self._get_trade_price_records(0, 1)
            self.logger.n_log(f"派生缓存预热完成: {self.get_key()}", self.log_level.INFO)
        except Exception as e:
            self.logger.n_log(f"派生缓存预热失败: {self.get_key()}, 错误: {e}", self.log_level.WARNING)

    # ---------- 定向状态预热（用户停留位置驱动） ----------

    def _schedule_state_prewarm(self, time_str: str):
        """记录最新停留时刻并唤醒状态预热线程；代际递增使进行中的旧扫描作废。"""
        try:
            anchor_ms = _time_to_ms(time_str)
        except (TypeError, ValueError):
            return
        self._prewarm_anchor_ms = anchor_ms
        self._prewarm_generation += 1
        self._prewarm_wakeup.set()

    @staticmethod
    def _session_end_for_time(time_ms: int) -> int:
        """所在连续竞价时段的结束时刻（午休前 11:30 / 收盘 15:00）。"""
        return AFTERNOON_START_MS if time_ms < AFTERNOON_START_MS else CLOSE_MS

    def _pick_prewarm_horizon_ms(self, anchor_ms: int) -> int:
        """按状态数预算自适应选择前向范围。

        用微秒级的 query_market_data 探 change_index 跨度，候选里取跨度
        不超过 PREWARM_FORWARD_STATES 的最大者：稀疏标的可覆盖 30–60s
        （多数大步档位也命中），002156 这类高频标的收敛到 3s（约 44 个
        状态）。md.change_index 滞后 1 位的已知坑只让跨度估计偏差 1，
        不影响选择结果。
        """
        base = self.visualizer.query_market_data(self.date, _ms_to_time(anchor_ms)) or {}
        try:
            idx0 = int(base.get('change_index') or 0)
        except (TypeError, ValueError):
            return 3000
        for horizon_ms in (60000, 30000, 10000, 4000):
            md = self.visualizer.query_market_data(self.date, _ms_to_time(anchor_ms + horizon_ms)) or {}
            try:
                span = int(md.get('change_index') or idx0) - idx0
            except (TypeError, ValueError):
                span = 0
            if span <= PREWARM_FORWARD_STATES:
                return horizon_ms
        return 4000

    def _prewarm_states_worker(self):
        """后台把停留时刻前后的盘口状态预建进快照 LRU。

        唤醒后先静默 PREWARM_DEBOUNCE_S 再重读锚点开工：用户连跳期间
        worker 完全不启动（每步都重置静默期），避免与请求抢 GIL 重复
        构建；真正「停留」后才投机预热。顺序上先扫 [anchor-4s, anchor)
        再扫 [anchor, anchor+H]：后向与跳转刚触发的图表请求窗口重合且
        覆盖回看步长的左溢出段；前向覆盖小步/大步步进后的新窗口。网格
        点先做 md+dict 命中预检，已缓存状态零成本跳过；真实构建后
        sleep 让出 GIL——引擎调用不释放 GIL，不节流的话预热线程会按
        单次构建时长（高频标的 ~92ms）为单位卡住事件循环。范围钳制在
        所在竞价时段内，避免午休/收盘后的海量无效网格点。新跳转到来时
        代际变化使当前扫描立即作废（已建部分保留在 LRU 里）。
        """
        while True:
            self._prewarm_wakeup.wait()
            self._prewarm_wakeup.clear()
            time.sleep(PREWARM_DEBOUNCE_S)
            generation = self._prewarm_generation
            anchor_ms = self._prewarm_anchor_ms
            if anchor_ms is None:
                continue
            self._prewarm_busy = True
            try:
                session_lo = self._session_start_for_time(anchor_ms)
                session_hi = self._session_end_for_time(anchor_ms)
                try:
                    horizon_ms = self._pick_prewarm_horizon_ms(anchor_ms)
                except Exception as e:
                    self.logger.n_log(f"状态预热范围探测失败: {self.get_key()}, 错误: {e}", self.log_level.WARNING)
                    continue
                ranges = (
                    (anchor_ms - PREWARM_BACKWARD_MS, anchor_ms - PREWARM_GRID_STEP_MS),
                    (anchor_ms, anchor_ms + horizon_ms),
                )
                built, stale = 0, False
                for lo_ms, hi_ms in ranges:
                    if stale:
                        break
                    lo_ms = max(lo_ms, session_lo)
                    hi_ms = min(hi_ms, session_hi)
                    for t_ms in range(lo_ms, hi_ms + 1, PREWARM_GRID_STEP_MS):
                        if self._prewarm_generation != generation:
                            stale = True
                            break
                        time_str = _ms_to_time(t_ms)
                        md = self.visualizer.query_market_data(self.date, time_str)
                        key = (md or {}).get('timestamp') or time_str
                        with self._snapshot_cache_lock:
                            if key in self._snapshot_cache:
                                continue
                        try:
                            self._query_by_time_cached(time_str, from_prewarm=True)
                        except Exception:
                            continue  # 引擎越界等异常：跳过该点继续扫
                        built += 1
                        time.sleep(PREWARM_BUILD_THROTTLE_S)
                if built or stale:
                    self.logger.n_log(
                        f"状态预热{'中断' if stale else '完成'}: {self.get_key()}, "
                        f"anchor={_ms_to_time(anchor_ms)}, 前向={horizon_ms}ms, 新建 {built} 个状态",
                        self.log_level.INFO,
                    )
            finally:
                self._prewarm_busy = False

    # ---------- 相邻变化导航（A2） ----------

    def get_adjacent_change(self, time_str: str, direction: int = 1) -> Optional[Dict]:
        """
        获取指定时间之后(或之前)第一个有变化的快照。

        引擎坑位（实测 000027.SZ/2025-08-01）：
        - query_market_data 的 change_index 会滞后快照 1 位（14:59:59.999 处
          快照 ci=27396 而 md.ci=27395），不能作为基准；
        - query_by_time 返回的快照 timestamp 可靠（= 最后一个 ts<=T 的变化），
          但其 change_index 字段在部分时刻也会滞后 1 位（尾部返回 ci=27395
          而 query_by_change_index(27396) 才对应同一 ts）；
        - query_by_change_index 对越界 index 会钳制到端点快照。
        因此以「时间戳比较」为准：在 ci±1 两个候选中找第一个满足
        ts > ts_cur（direction=1）或 ts < ts_cur（direction=-1）的变化。

        Args:
            time_str: 'HH:MM:SS.mmm'
            direction: 1=下一个变化, -1=上一个变化
        Returns:
            完整快照 dict；没有更多变化时返回 None
        """
        self.update_access_time()
        current = self.get_snapshot_by_time(time_str)
        if not current:
            return None
        ts_cur = current.get('timestamp') or ''
        try:
            ci = int(current.get('change_index', -1))
        except (TypeError, ValueError):
            return None
        if ci < 0 or not ts_cur:
            return None

        total = self.visualizer.get_total_changes()
        forward = direction >= 0
        # 两个候选足以覆盖 change_index 滞后 1 位的情况
        candidates = [ci + 1, ci + 2] if forward else [ci, ci - 1]
        for k in candidates:
            if k < 0 or k >= total:
                continue
            s = self.visualizer.query_by_change_index(k)
            if not s:
                continue
            # 引擎越界钳制防护：返回的必须是目标变化本身
            if int(s.get('change_index', -1)) != k:
                continue
            ts = s.get('timestamp') or ''
            if not ts:
                continue
            if (forward and ts > ts_cur) or (not forward and ts < ts_cur):
                return self._enrich_snapshot_create_time(s)
        return None

    # ---------- 流量时间序列（C2） ----------

    def _cancel_records_at_level(self, level_index: int) -> List[Dict]:
        """
        修补引擎撤单计数器（恒为0）：从 cstra 撤单记录(et=2)计算指定档位撤单量。
        判定：撤单按 orderid 归属买/卖侧；该订单挂单价 == 撤单时刻的第 level_index+1 档价 → 计入。
        level_index: 0=买一/卖一, 1=买二/卖二
        Returns: 全日内记录 [{'time_ms': ..., 'side': 'bid'|'ask', 'volume': ...}, ...]，按时间排序
        """
        if level_index in self._cancel_records_cache:
            return self._cancel_records_cache[level_index]

        self._load_tick_data()
        records = []
        if self._cstra_df is None or self._csord_df is None:
            self._cancel_records_cache[level_index] = records
            return records
        try:
            # 撤单行
            et = self._cstra_df['exectype'].astype(str).str.strip("b' ")
            cancels = self._cstra_df[et == '2']
            if cancels.empty:
                self._cancel_records_cache[level_index] = records
                return records
            # orderid -> price 映射（懒构建缓存）
            if self._order_price_map is None:
                self._order_price_map = dict(zip(
                    self._csord_df['orderid'].astype('int64'), self._csord_df['price'].astype(float)
                ))
            start_str = f"{self.date} {_ms_to_time(CONTINUOUS_AUCTION_START_MS)}"
            end_str = f"{self.date} 15:00:00.000"
            win = cancels[(cancels['datetime'] > start_str) & (cancels['datetime'] <= end_str)]
            # 逐行改为纯值循环：iterrows 每行构造 Series 开销大，撤单量大时
            # 构建耗时以秒计（见跳转性能排查文档）
            bid_ids = win['bidorderid'].to_numpy()
            ask_ids = win['askorderid'].to_numpy()
            sizes = win['size'].to_numpy()
            dt_strs = win['datetime'].astype(str).to_numpy()
            for bid_oid, ask_oid, size, dt_str in zip(bid_ids, ask_ids, sizes, dt_strs):
                bid_oid = int(bid_oid)
                ask_oid = int(ask_oid)
                oid = bid_oid if bid_oid != 0 else ask_oid
                side = 1 if bid_oid != 0 else -1
                price = self._order_price_map.get(oid)
                if price is None or price == 0:
                    continue  # 市价单等无法判档
                time_part = dt_str.split(' ')[-1]
                md = self.visualizer.query_market_data(self.date, time_part)
                if not md or not md.get('market_data'):
                    continue
                mdd = md['market_data']
                best_bids = mdd.get('best_bids') or []
                best_asks = mdd.get('best_asks') or []
                best_bid = best_bids[level_index] if len(best_bids) > level_index else None
                best_ask = best_asks[level_index] if len(best_asks) > level_index else None
                price_int = round(price * 10000)
                if side == 1 and best_bid is not None and price_int == best_bid:
                    records.append({'time_ms': _time_to_ms(time_part), 'side': 'bid', 'volume': float(size)})
                elif side == -1 and best_ask is not None and price_int == best_ask:
                    records.append({'time_ms': _time_to_ms(time_part), 'side': 'ask', 'volume': float(size)})
        except Exception as e:
            self.logger.n_log(f"撤单量修补计算失败(档位{level_index + 1}): {self.get_key()}, 错误: {e}", self.log_level.ERROR)
        records = sorted(records, key=lambda r: r['time_ms'])
        self._cancel_records_cache[level_index] = records
        return records

    def _best_level_cancel_volumes(self, start_ms: int, end_ms: int) -> List[Dict]:
        """买一/卖一撤单量（level_index=0，口径见 _cancel_records_at_level）"""
        return [r for r in self._cancel_records_at_level(0) if start_ms < r['time_ms'] <= end_ms]

    def _second_level_cancel_volumes(self, start_ms: int, end_ms: int) -> List[Dict]:
        """买二/卖二撤单量（level_index=1）"""
        return [r for r in self._cancel_records_at_level(1) if start_ms < r['time_ms'] <= end_ms]

    def _get_trade_price_records(self, start_ms: int, end_ms: int) -> List[Dict]:
        """返回时间范围内的逐笔成交价，结果按时间排序并缓存。"""
        if self._trade_price_records is None:
            self._load_tick_data()
            records = []
            if self._cstra_df is not None:
                try:
                    et = self._cstra_df["exectype"].astype(str).map(self._clean_exectype)
                    trades = self._cstra_df[et == "1"]
                    # 纯值循环代替 iterrows（成交行可达数万，构建耗时会拖慢首次跳转）
                    dt_strs = trades["datetime"].astype(str).to_numpy()
                    prices = trades["price"].to_numpy()
                    for dt_str, price in zip(dt_strs, prices):
                        price = float(price)
                        if price > 0:
                            time_part = dt_str.split(" ")[-1]
                            records.append({"time_ms": _time_to_ms(time_part), "price": price})
                except Exception as e:
                    self.logger.n_log(f"成交价缓存构建失败: {self.get_key()}, 错误: {e}", self.log_level.ERROR)
            self._trade_price_records = sorted(records, key=lambda r: r["time_ms"])
        return [r for r in self._trade_price_records if start_ms < r["time_ms"] <= end_ms]

    def get_trade_flow_series(self, time_str: str, window_ms: int, points: int = 60) -> List[Dict]:
        """
        获取 [time-window, time] 窗口内买一/卖一的流量序列 + 一档盘口等待量。
        在窗口内均匀取 points+1 个采样点查 market_data（0.013ms/次），
        相邻采样点的累计量差分即每桶新增量。

        Returns: [{'start': 'HH:MM:SS.mmm', 'end': ..., 'bid_create': ..., 'bid_cancel': ...,
                   'bid_traded': ..., 'ask_create': ..., 'ask_cancel': ..., 'ask_traded': ...,
                   'bid_volume': ..., 'ask_volume': ...}, ...]
        注：
        - create/traded 用引擎计数器差分；cancel 引擎计数器恒0，用 CSV 修补（见 _best_level_cancel_volumes）。
        - bid_volume/ask_volume = 该桶右缘时刻真实买一/卖一盘口累计等待量（状态量，非差分）。
          最优价会在窗口内变化，不能用右缘档位和累计计数器反推历史一档量；因此逐采样点
          查询完整快照，保证每个图上时刻都取该时刻实际买一/卖一的 total_volume。
        """
        self.update_access_time()
        end_ms = _time_to_ms(time_str)
        start_ms = max(end_ms - int(window_ms), CONTINUOUS_AUCTION_START_MS)
        points = max(2, min(int(points), 500))
        step = (end_ms - start_ms) / points
        if step <= 0:
            return []

        sample_times = [_ms_to_time(round(start_ms + i * step)) for i in range(points + 1)]
        # 一次遍历拿全部 market_data（计数器差分用）+ 按盘口状态分组的完整快照
        snapshots, state_snapshots = self._window_market_snapshots(sample_times)

        # 撤单修补：同一份日内记录同时生成桶内瞬时量和截至桶右缘的累计量。
        all_cancel_records = self._best_level_cancel_volumes(CONTINUOUS_AUCTION_START_MS - 1, end_ms)
        bucket_cancel = [{'bid': 0.0, 'ask': 0.0} for _ in range(points)]
        for rec in all_cancel_records:
            if rec['time_ms'] <= start_ms:
                continue
            idx = int((rec['time_ms'] - start_ms) / step)
            idx = min(max(idx, 0), points - 1)
            bucket_cancel[idx][rec['side']] += rec['volume']

        cumulative_cancel = []
        running_cancel = {'bid': 0.0, 'ask': 0.0}
        cancel_index = 0
        for sample_time in sample_times[1:]:
            sample_ms = _time_to_ms(sample_time)
            while cancel_index < len(all_cancel_records) and all_cancel_records[cancel_index]['time_ms'] <= sample_ms:
                rec = all_cancel_records[cancel_index]
                running_cancel[rec['side']] += rec['volume']
                cancel_index += 1
            cumulative_cancel.append(dict(running_cancel))

        # 一档状态量必须按采样时刻读取真实快照。累计计数器只描述流量，最优价切档后
        # 无法从窗口右缘的一档反推出历史时刻的“一档”，此前的反推会产生伪 0。
        # 快照已按盘口状态分组获取（见 _window_market_snapshots），组内共享引用。
        level_snapshots = [s or {} for s in state_snapshots[1:]]

        def _level_price(bucket_index, side):
            levels = level_snapshots[bucket_index].get("levels") or {}
            level = levels.get(f"{side}1") or {}
            price = level.get("price")
            return float(price) / 10000 if price is not None else None

        trade_prices = [[] for _ in range(points)]
        for rec in self._get_trade_price_records(start_ms, end_ms):
            idx = int((rec["time_ms"] - start_ms) / step)
            idx = min(max(idx, 0), points - 1)
            if rec["price"] not in trade_prices[idx]:
                trade_prices[idx].append(rec["price"])

        def _level_volume(bucket_index, side):
            levels = level_snapshots[bucket_index].get('levels') or {}
            level = levels.get(f'{side}1') or {}
            volume = level.get('total_volume')
            return float(volume) if volume is not None else None

        series = []
        for i in range(points):
            cur = snapshots[i + 1].get('market_data') or {}
            prev = snapshots[i].get('market_data') or {}
            series.append({
                'start': sample_times[i],
                'end': sample_times[i + 1],
                'bid_create': (cur.get('bid_create_count') or 0) - (prev.get('bid_create_count') or 0),
                'bid_cancel': bucket_cancel[i]['bid'],
                'bid_cancel_cumulative': cumulative_cancel[i]['bid'],
                'bid_traded': (cur.get('bid_traded_count') or 0) - (prev.get('bid_traded_count') or 0),
                'bid_traded_cumulative': cur.get('bid_traded_count') or 0,
                'ask_create': (cur.get('ask_create_count') or 0) - (prev.get('ask_create_count') or 0),
                'ask_cancel': bucket_cancel[i]['ask'],
                'ask_cancel_cumulative': cumulative_cancel[i]['ask'],
                'ask_traded': (cur.get('ask_traded_count') or 0) - (prev.get('ask_traded_count') or 0),
                'ask_traded_cumulative': cur.get('ask_traded_count') or 0,
                # 桶右缘时刻的一档累计挂单量（盘口等待量）
                'bid_volume': _level_volume(i, 'bid'),
                'ask_volume': _level_volume(i, 'ask'),
                # 价格字段供 Q-t 图悬停提示使用：挂单看当时一档价，成交看桶内成交价。
                'bid_price': _level_price(i, 'bid'),
                'ask_price': _level_price(i, 'ask'),
                'trade_prices': trade_prices[i],
            })
        self._schedule_state_prewarm(time_str)
        return series

    @classmethod
    def create_with_progress(cls, symbol: str, date: str, data_path: str = DEFAULT_DATA_PATH, progress_callback: Optional[Callable] = None, is_ETF: bool = False):
        """
        创建TradeBook实例的工厂方法，支持进度回调
        """
        return cls(symbol, date, data_path, progress_callback, is_ETF=is_ETF)

    def get_key(self) -> str:
        """获取唯一标识key"""
        return f"{self.symbol}_{self.date}"

    def update_access_time(self):
        """更新最后访问时间"""
        with self._lock:
            self.last_accessed_at = datetime.now()

    def is_expired(self, hours: float = 12.0) -> bool:
        """检查是否过期 """
        expiry_time = self.last_accessed_at + timedelta(hours=hours)
        return datetime.now() > expiry_time

    def get_total_snapshots(self):
        """获取总快照数"""
        self.update_access_time()
        return self.visualizer.get_total_snapshots()
    
    def get_total_changes(self):
        """获取总变化数"""
        self.update_access_time()
        return self.visualizer.get_total_changes()
    
    def get_time_range(self):
        """获取时间范围"""
        self.update_access_time()
        return self.visualizer.get_time_range()
    
    def get_snapshot_by_time(self, time: str):
        """按照时间查询快照"""
        self.update_access_time()
        try:
            return self._enrich_snapshot_create_time(self._query_by_time_cached(time))
        finally:
            self._schedule_state_prewarm(time)

    def get_snapshot_by_id(self, id: int):
        """按照id查询快照"""
        self.update_access_time()
        return self._enrich_snapshot_create_time(self.visualizer.query_by_id(id))

    def get_snapshot_by_index(self, index: int):
        """按照索引查询快照"""
        self.update_access_time()
        return self._enrich_snapshot_create_time(self.visualizer.query_by_change_index(index))

    def get_market_data(self, time: str):
        """获取市场数据"""
        self.update_access_time()
        return self.visualizer.query_market_data(self.date, time)

    def get_idle_time(self) -> timedelta:
        """获取空闲时间"""
        return datetime.now() - self.last_accessed_at
    
    def get_orders_data(self):
        """读取与盘口回放一致的本地 csord 文件。"""
        local_path = self._csord_path()
        try:
            if not os.path.exists(local_path):
                self.logger.n_log(
                    f"本地订单数据不存在: {local_path}", self.log_level.ERROR
                )
                return None
            orders_df = pd.read_csv(local_path)

            if orders_df is None or len(orders_df) == 0:
                return None

            if 'datetime' not in orders_df.columns:
                if 'date' in orders_df.columns and 'time' in orders_df.columns:
                    orders_df['datetime'] = orders_df['date'] + pd.to_timedelta(orders_df['time'])
                else:
                    return None

            orders_df['datetime'] = pd.to_datetime(orders_df['datetime'], errors='coerce')
            for column in ('price', 'size', 'side', 'orderid'):
                if column in orders_df.columns:
                    orders_df[column] = pd.to_numeric(orders_df[column], errors='coerce')
            return orders_df.dropna(subset=['datetime', 'price', 'size', 'side', 'orderid'])
        except Exception as e:
            self.logger.n_log(f"读取订单数据失败: {self.get_key()}, 错误: {e}", self.log_level.ERROR)
            return None

    def find_order(self, order_time: Union[str, datetime], order_price: float, 
                   order_size: float, order_side: int, tolerance_ms: int = 100) -> Dict[str, Any]:
        """
        根据订单时间、价格、数量和方向查找订单
        
        Args:
            order_time: 订单时间 (字符串或datetime)
            order_price: 订单价格
            order_size: 订单数量
            order_side: 订单方向（1为买，-1为卖）
            tolerance_ms: 时间容差，单位为毫秒，默认100ms
            
        Returns:
            Dict包含查询结果
        """
        self.update_access_time()
        
        try:
            # 获取订单数据
            orders_df = self.get_orders_data()
            if orders_df is None or len(orders_df) == 0:
                return {
                    "success": False,
                    "orderid": None,
                    "datetime": None,
                    "message": "无法获取订单数据或数据为空"
                }

            # 转换时间；前端通常只传 HH:mm:ss.SSS，补上当前 TradeBook 日期。
            if isinstance(order_time, str):
                time_value = order_time.strip()
                target_time = pd.Timestamp(
                    f"{self.date} {time_value}" if ' ' not in time_value else time_value
                )
            else:
                target_time = pd.Timestamp(order_time)

            order_price = float(order_price)
            order_size = float(order_size)
            order_side = int(order_side)

            # 矢量化计算：价格、数量和方向的匹配
            price_match = (orders_df['price'] - order_price).abs() < 1e-6
            size_match = (orders_df['size'] - order_size).abs() < 1e-6
            side_match = orders_df['side'] == order_side
            combined_match = price_match & size_match & side_match

            # 第一步：查找时间大于等于给定时间的匹配订单
            time_forward_match = orders_df['datetime'] >= target_time
            forward_matches = orders_df[combined_match & time_forward_match]

            if len(forward_matches) > 0:
                # 选择时间最早的订单
                min_time = forward_matches['datetime'].min()
                first_match = forward_matches[forward_matches['datetime'] == min_time].iloc[0]
                return {
                    "success": True,
                    "orderid": int(first_match['orderid']),
                    "datetime": str(first_match['datetime']),
                    "price": float(first_match['price']),
                    "size": float(first_match['size']),
                    "side": int(first_match['side']),
                    "message": f"成功找到匹配的订单: ID={first_match['orderid']}"
                }

            # 第二步：向前查找容差时间内最接近下单时间的订单
            time_diff = target_time - orders_df['datetime']
            time_backward_match = (orders_df['datetime'] < target_time) & (time_diff < pd.Timedelta(f"{tolerance_ms}ms"))
            backward_matches = orders_df[combined_match & time_backward_match]

            if len(backward_matches) > 0:
                # 选择时间最接近的订单
                max_time = backward_matches['datetime'].max()
                first_match = backward_matches[backward_matches['datetime'] == max_time].iloc[0]
                return {
                    "success": True,
                    "orderid": int(first_match['orderid']),
                    "datetime": str(first_match['datetime']),
                    "price": float(first_match['price']),
                    "size": float(first_match['size']),
                    "side": int(first_match['side']),
                    "message": f"在容差时间内找到匹配的订单: ID={first_match['orderid']}"
                }

            # 未找到匹配订单
            return {
                "success": False,
                "orderid": None,
                "datetime": None,
                "message": "没有找到匹配的订单"
            }

        except Exception as e:
            return {
                "success": False,
                "orderid": None,
                "datetime": None,
                "message": f"查找订单时发生错误: {e}",
            }

    # ---------- 订单生命周期（B4） ----------

    @staticmethod
    def _clean_exectype(value) -> str:
        """cstra 的 exectype 形如 \"b'1'\"，剥离字节串包装"""
        s = str(value)
        if s.startswith("b'") and s.endswith("'"):
            s = s[2:-1]
        return s.strip()

    def _load_tick_data(self):
        """懒加载本标的的 csord/cstra CSV（预热线程与请求并发时避免重复读取）"""
        if self._csord_df is not None and self._cstra_df is not None:
            return
        with self._tick_lock:
            if self._csord_df is None and os.path.exists(self._csord_path()):
                self._csord_df = pd.read_csv(self._csord_path())
            if self._cstra_df is None and os.path.exists(self._cstra_path()):
                self._cstra_df = pd.read_csv(self._cstra_path())

    @staticmethod
    def _queue_positions_in_snapshot(snap: Optional[Dict], wanted: set) -> Dict[int, Dict]:
        """在一次快照中批量定位订单，返回队列位置与身前/身后量。

        身前/身后量用一次前缀和遍历得出（原实现对每个命中订单做两次
        切片 sum，是 O(n^2)，锁定图 60 个采样点下显著拖慢跳转）。
        """
        try:
            if not snap:
                return {}
            positions = {}
            for level_key, level in snap.get('levels', {}).items():
                orders = level.get('orders', [])
                # 前缀和：ahead[i] = 前 i 笔订单的 remaining_volume 之和
                ahead = 0
                ahead_sums = []
                for o in orders:
                    ahead_sums.append(ahead)
                    ahead += int(o.get('remaining_volume', 0) or 0)
                level_total = ahead
                for i, o in enumerate(orders):
                    try:
                        current_id = int(o.get('order_local_id', -1))
                    except (TypeError, ValueError):
                        continue
                    if current_id in wanted:
                        own = int(o.get('remaining_volume', 0) or 0)
                        positions[current_id] = {
                            'level': level_key,
                            'price': level.get('price'),
                            'position': i + 1,
                            'level_order_count': len(orders),
                            'remaining_volume': float(own),
                            'ahead_volume': ahead_sums[i],
                            'behind_volume': level_total - ahead_sums[i] - own,
                        }
            return positions
        except Exception:
            return {}

    def _queue_positions_at(self, time_str: str, order_ids: List[int]) -> Dict[int, Dict]:
        """在指定时刻的快照中定位订单，返回队列位置与身前/身后量"""
        wanted = {int(order_id) for order_id in order_ids}
        return self._queue_positions_in_snapshot(self._query_by_time_cached(time_str), wanted)

    def _queue_position_at(self, time_str: str, order_id: int) -> Optional[Dict]:
        """在指定时刻的快照中定位订单，返回队列位置与身前/身后量"""
        return self._queue_positions_at(time_str, [order_id]).get(order_id)

    def get_order_queue_series(
        self,
        time_str: str,
        window_ms: int,
        order_ids: List[int],
        points: int = 60,
    ) -> List[Dict]:
        """获取窗口内多个订单的队列位置、身前量和身后量序列。"""
        self.update_access_time()
        unique_ids = list(dict.fromkeys(int(order_id) for order_id in order_ids))
        if not unique_ids:
            return []

        end_ms = _time_to_ms(time_str)
        start_ms = max(end_ms - int(window_ms), CONTINUOUS_AUCTION_START_MS)
        points = max(2, min(int(points), 500))
        step = (end_ms - start_ms) / points
        if step <= 0:
            return []

        sample_times = [_ms_to_time(round(start_ms + i * step)) for i in range(points + 1)]
        # 按盘口状态分组获取快照（窗口内多数采样点状态相同，避免逐点全量查询）
        _, state_snapshots = self._window_market_snapshots(sample_times[1:])
        wanted = set(unique_ids)
        series = []
        for sample_time, snap in zip(sample_times[1:], state_snapshots):
            positions = self._queue_positions_in_snapshot(snap, wanted)
            series.append({
                'time': sample_time,
                'orders': {
                    str(order_id): positions.get(order_id)
                    for order_id in unique_ids
                },
            })
        self._schedule_state_prewarm(time_str)
        return series

    def _ensure_execution_prediction_cache(self):
        """构建订单元数据和被动挂单成交记录，供成交时间预测复用。"""
        self._load_tick_data()
        if self._order_meta_map is None:
            order_meta: Dict[int, Dict[str, Any]] = {}
            if self._csord_df is not None:
                # 纯值循环代替 iterrows（订单数可达数万，原构建以秒计）
                order_ids = self._csord_df['orderid'].to_numpy()
                dt_strs = self._csord_df['datetime'].astype(str).to_numpy()
                sides = self._csord_df['side'].to_numpy()
                prices = self._csord_df['price'].to_numpy()
                for order_id, dt_str, side, price in zip(order_ids, dt_strs, sides, prices):
                    try:
                        order_meta[int(order_id)] = {
                            'time_ms': _time_to_ms(dt_str.split(' ')[-1]),
                            'side': int(side),
                            'price': float(price),
                        }
                    except (TypeError, ValueError, KeyError):
                        continue
            self._order_meta_map = order_meta

        if self._passive_trade_records is not None:
            return

        records: List[Dict[str, Any]] = []
        if self._cstra_df is not None:
            exectypes = self._cstra_df['exectype'].astype(str).map(self._clean_exectype)
            trades = self._cstra_df[exectypes == '1']
            order_meta = self._order_meta_map or {}
            dt_strs = trades['datetime'].astype(str).to_numpy()
            bid_ids = trades['bidorderid'].to_numpy()
            ask_ids = trades['askorderid'].to_numpy()
            flags = trades['tradebsflag'].astype(str).to_numpy() if 'tradebsflag' in trades.columns else None
            sizes = trades['size'].to_numpy()
            prices = trades['price'].to_numpy()
            for i, dt_str in enumerate(dt_strs):
                try:
                    bid_id = int(bid_ids[i])
                    ask_id = int(ask_ids[i])
                    flag = self._clean_exectype(flags[i] if flags is not None else '0').upper()

                    # B/S 表示主动买/主动卖；部分市场（如上交所）恒为 0，
                    # 此时用双方订单创建先后判断较早进入盘口的被动侧。
                    passive_side = None
                    if flag == 'B':
                        passive_side = -1
                    elif flag == 'S':
                        passive_side = 1
                    else:
                        bid_meta = order_meta.get(bid_id)
                        ask_meta = order_meta.get(ask_id)
                        if bid_meta and ask_meta:
                            if bid_meta['time_ms'] < ask_meta['time_ms']:
                                passive_side = 1
                            elif ask_meta['time_ms'] < bid_meta['time_ms']:
                                passive_side = -1

                    if passive_side is None:
                        continue
                    volume = float(sizes[i])
                    price = float(prices[i])
                    if volume <= 0 or price <= 0:
                        continue
                    records.append({
                        'time_ms': _time_to_ms(dt_str.split(' ')[-1]),
                        'side': passive_side,
                        'price': price,
                        'volume': volume,
                    })
                except (TypeError, ValueError, KeyError):
                    continue
        self._passive_trade_records = sorted(records, key=lambda item: item['time_ms'])

    @staticmethod
    def _session_start_for_time(time_ms: int) -> int:
        afternoon_start = 13 * 3600000
        return afternoon_start if time_ms >= afternoon_start else CONTINUOUS_AUCTION_START_MS

    def _best_level_flow_sample(
        self,
        side: str,
        end_ms: int,
        window_ms: int,
    ) -> Dict[str, Any]:
        """
        买1/卖1档流量采样：消耗速度 = 该档等待量减少速度（成交+撤单合计），
        新增速度 = 该档新挂单速度。
        引擎计数器是跟随最优档的累计值（实测 Δ一档量 = Δcreate − Δtraded 与
        快照逐点一致），因此只需窗口两端各查一次 query_market_data（0.013ms/次）。
        窗口不按订单创建时刻截断：档位流量描述的是队列本身，与订单何时挂出无关。
        side: 'bid' 或 'ask'
        """
        start_ms = max(
            end_ms - window_ms,
            self._session_start_for_time(end_ms),
        )
        duration_ms = max(0, end_ms - start_ms)
        if duration_ms <= 0:
            return {
                'window_ms': window_ms,
                'actual_window_ms': 0,
                'elapsed_seconds': 0.0,
                'depleted_volume': 0.0,
                'arrived_volume': 0.0,
                'depletion_rate': 0.0,
                'arrival_rate': 0.0,
            }

        create_key = f'{side}_create_count'
        traded_key = f'{side}_traded_count'
        md_start = (
            self.visualizer.query_market_data(self.date, _ms_to_time(start_ms)) or {}
        ).get('market_data') or {}
        md_end = (
            self.visualizer.query_market_data(self.date, _ms_to_time(end_ms)) or {}
        ).get('market_data') or {}

        elapsed_seconds = duration_ms / 1000
        depleted = max(0.0, float((md_end.get(traded_key) or 0) - (md_start.get(traded_key) or 0)))
        arrived = max(0.0, float((md_end.get(create_key) or 0) - (md_start.get(create_key) or 0)))
        return {
            'window_ms': window_ms,
            'actual_window_ms': duration_ms,
            'elapsed_seconds': elapsed_seconds,
            'depleted_volume': depleted,
            'arrived_volume': arrived,
            'depletion_rate': depleted / elapsed_seconds,
            'arrival_rate': arrived / elapsed_seconds,
        }

    def _second_level_flow_sample(
        self,
        side: str,
        end_ms: int,
        window_ms: int,
    ) -> Dict[str, Any]:
        """
        买2/卖2档流量采样（供三档订单预测）。二档非最优价时不发生成交，
        消耗 = 撤单速度（CSV 修补口径）；新增挂单由 Δ二档总量 = 新增 − 撤单 反推。
        引擎没有二档计数器，总量取窗口两端完整快照（query_by_time）。
        """
        start_ms = max(
            end_ms - window_ms,
            self._session_start_for_time(end_ms),
        )
        duration_ms = max(0, end_ms - start_ms)
        if duration_ms <= 0:
            return {
                'window_ms': window_ms,
                'actual_window_ms': 0,
                'elapsed_seconds': 0.0,
                'depleted_volume': 0.0,
                'arrived_volume': 0.0,
                'depletion_rate': 0.0,
                'arrival_rate': 0.0,
            }

        def _level2_volume(time_ms: int) -> float:
            snap = self._query_by_time_cached(_ms_to_time(time_ms)) or {}
            level = (snap.get('levels') or {}).get(f'{side}2') or {}
            return float(level.get('total_volume') or 0)

        v0 = _level2_volume(start_ms)
        v1 = _level2_volume(end_ms)
        cancel_volume = sum(
            r['volume'] for r in self._second_level_cancel_volumes(start_ms, end_ms)
            if r['side'] == side
        )
        elapsed_seconds = duration_ms / 1000
        arrived = max(0.0, (v1 - v0) + cancel_volume)
        return {
            'window_ms': window_ms,
            'actual_window_ms': duration_ms,
            'elapsed_seconds': elapsed_seconds,
            'depleted_volume': cancel_volume,
            'arrived_volume': arrived,
            'depletion_rate': cancel_volume / elapsed_seconds,
            'arrival_rate': arrived / elapsed_seconds,
        }

    def _same_price_trade_sample(
        self,
        side: int,
        price: float,
        end_ms: int,
        window_ms: int,
    ) -> Dict[str, Any]:
        """统计当前会话内同价格、同被动挂单方向的成交服务速度。"""
        start_ms = max(end_ms - window_ms, self._session_start_for_time(end_ms))
        elapsed_seconds = max(0.0, (end_ms - start_ms) / 1000)
        matching = [
            record for record in (self._passive_trade_records or [])
            if start_ms < record['time_ms'] <= end_ms
            and record['side'] == side
            and abs(record['price'] - price) < 0.000001
        ]
        volume = sum(record['volume'] for record in matching)
        return {
            'window_ms': window_ms,
            'actual_window_ms': end_ms - start_ms,
            'rate': volume / elapsed_seconds if elapsed_seconds > 0 else 0.0,
            'trade_volume': volume,
            'trade_count': len(matching),
            'average_trade_size': volume / len(matching) if matching else 0.0,
        }

    @staticmethod
    def _add_trading_wait(time_str: str, wait_seconds: Optional[float]) -> Dict[str, Any]:
        """在当日连续竞价时段内增加等待时间，自动跳过午休。"""
        if wait_seconds is None:
            return {'time': None, 'beyond_close': False}
        current_ms = _time_to_ms(time_str)
        remaining_ms = max(0, round(wait_seconds * 1000))
        morning_start = 9 * 3600000 + 30 * 60000
        morning_end = 11 * 3600000 + 30 * 60000
        afternoon_start = 13 * 3600000
        close_ms = 15 * 3600000

        if current_ms < morning_start:
            current_ms = morning_start
        if morning_end <= current_ms < afternoon_start:
            current_ms = afternoon_start

        while remaining_ms > 0:
            session_end = morning_end if current_ms < morning_end else close_ms
            available = max(0, session_end - current_ms)
            if remaining_ms <= available:
                return {'time': _ms_to_time(current_ms + remaining_ms), 'beyond_close': False}
            remaining_ms -= available
            if session_end == morning_end:
                current_ms = afternoon_start
            else:
                return {'time': None, 'beyond_close': True}
        return {'time': _ms_to_time(current_ms), 'beyond_close': False}

    def get_order_execution_estimate(self, time_str: str, order_id: int) -> Dict[str, Any]:
        """返回订单真实成交结果和仅使用 time_str 之前数据计算的成交时间预测。"""
        self.update_access_time()
        lifecycle = self.get_order_lifecycle(order_id)
        if not lifecycle.get('success'):
            return lifecycle

        self._ensure_execution_prediction_cache()
        summary = lifecycle['summary']
        events = lifecycle['events']
        trade_events = [event for event in events if event['type'] == 'trade']
        cancel_events = [event for event in events if event['type'] == 'cancel']
        full_fill_event = next(
            (event for event in trade_events if event.get('remaining_after', 1) <= 0),
            None,
        )
        actual = {
            'outcome': summary.get('outcome'),
            'first_fill_time': trade_events[0]['time'] if trade_events else None,
            'last_fill_time': trade_events[-1]['time'] if trade_events else None,
            'full_fill_time': full_fill_event['time'] if full_fill_event else None,
            'cancel_time': cancel_events[-1]['time'] if cancel_events else None,
            'filled_volume': summary.get('filled_size', 0),
            'cancelled_volume': summary.get('cancelled_size', 0),
            'trade_count': len(trade_events),
        }

        end_ms = _time_to_ms(time_str)
        past_trade_events = [
            event for event in trade_events
            if _time_to_ms(event['time'].split(' ')[-1]) <= end_ms
        ]
        has_filled_as_of = bool(past_trade_events)
        filled_volume_as_of = sum(float(event.get('size', 0) or 0) for event in past_trade_events)
        current_position = self._queue_position_at(time_str, order_id)
        meta = (self._order_meta_map or {}).get(order_id)
        if current_position is None or meta is None:
            return {
                'success': True,
                'order': summary,
                'as_of_time': time_str,
                'actual': actual,
                'prediction': {
                    'available': False,
                    'reason': '订单在当前快照六档盘口中不可见，无法取得当前身前量',
                    'lookahead_safe': True,
                },
            }

        # 身前总量 = 所有交易优先级更高档位的等待总量 + 本档位身前量
        # （买2 身前总量 = 买1总量 + 买2档内身前量；买3 再加买2总量，卖侧同理）
        level_key = str(current_position.get('level') or '')
        level_rank = {'bid1': 1, 'bid2': 2, 'bid3': 3, 'ask1': 1, 'ask2': 2, 'ask3': 3}
        rank = level_rank.get(level_key)
        if rank is None:
            return {
                'success': True,
                'order': summary,
                'as_of_time': time_str,
                'actual': actual,
                'prediction': {
                    'available': False,
                    'reason': f'无法识别订单当前档位 {level_key}',
                    'lookahead_safe': True,
                },
            }
        side_key = 'bid' if level_key.startswith('bid') else 'ask'
        side_sign = 1 if side_key == 'bid' else -1
        side_label = '买' if side_key == 'bid' else '卖'

        snap = self._query_by_time_cached(time_str) or {}
        levels = snap.get('levels') or {}
        higher_levels_volume = 0.0
        for r in range(1, rank):
            higher = levels.get(f'{side_key}{r}') or {}
            higher_levels_volume += float(higher.get('total_volume') or 0)
        own_ahead = float(current_position.get('ahead_volume', 0) or 0)
        ahead_total = higher_levels_volume + own_ahead
        remaining_volume = float(current_position.get('remaining_volume', 0) or 0)

        # 最优档价格（快照 price 为 ×10000 整数），成交速度按该档成交价统计
        best_level = levels.get(f'{side_key}1') or {}
        best_price_raw = best_level.get('price')
        best_price = float(best_price_raw) / 10000 if best_price_raw is not None else meta['price']

        candidate_windows = [30000, 120000, 300000, 600000]
        # 每个候选窗口独立计算一组速率与预测（弹窗按窗口表格逐行展示），
        # 再从中自适应选一行作为顶部卡片的头条预测。
        # 净速度口径：买1档 = 买1消耗（新挂单排在其身后，不影响）；
        # 买2档 = 买1消耗 − 买1新增；
        # 买3档 = (买1消耗 − 买1新增) + (买2消耗 − 买2新增)，买2不发生成交，其消耗=撤单。
        window_rows = []
        for window in candidate_windows:
            flow1 = self._best_level_flow_sample(side_key, end_ms, window)
            trade = self._same_price_trade_sample(side_sign, best_price, end_ms, window)
            depletion = float(flow1.get('depletion_rate', 0) or 0)
            arrival = float(flow1.get('arrival_rate', 0) or 0)
            net1 = depletion if rank == 1 else depletion - arrival
            second_net = None
            net = net1
            if rank == 3:
                flow2 = self._second_level_flow_sample(side_key, end_ms, window)
                second_net = flow2['depletion_rate'] - flow2['arrival_rate']
                net = net1 + second_net
            trade_rate = float(trade.get('rate', 0) or 0)

            if net <= 0 or trade_rate <= 0:
                first_wait = None
                full_wait = None
                if net <= 0 and rank > 1 and (arrival > 0 or (second_net is not None and second_net < 0)):
                    reason = f'{side_label}1新增挂单速度不低于消耗速度，身前队列预计不会缩短'
                elif net <= 0:
                    reason = f'窗口内未观察到{side_label}1队列净消耗'
                else:
                    reason = f'窗口内没有可识别的{side_label}1价成交'
            else:
                queue_wait = ahead_total / net
                average_trade_size = float(trade.get('average_trade_size', 0) or 0)
                typical_fill = min(remaining_volume, average_trade_size or remaining_volume)
                first_wait = queue_wait + typical_fill / trade_rate
                full_wait = queue_wait + remaining_volume / trade_rate
                reason = None

            first_clock = self._add_trading_wait(time_str, first_wait)
            full_clock = self._add_trading_wait(time_str, full_wait)
            window_rows.append({
                'window_ms': window,
                'actual_window_ms': flow1['actual_window_ms'],
                'elapsed_seconds': flow1['elapsed_seconds'],
                'depleted_volume': flow1['depleted_volume'],
                'arrived_volume': flow1['arrived_volume'],
                'depletion_rate': depletion,
                'arrival_rate': arrival,
                'second_net_rate': second_net,
                'net_queue_rate': net,
                'trade_rate': trade_rate,
                'trade_count': int(trade.get('trade_count', 0) or 0),
                'trade_volume': trade.get('trade_volume', 0),
                'average_trade_size': trade.get('average_trade_size', 0),
                'available': first_wait is not None,
                'reason': reason,
                'first_fill_wait_ms': round(first_wait * 1000) if first_wait is not None else None,
                'full_fill_wait_ms': round(full_wait * 1000) if full_wait is not None else None,
                'first_fill_time': first_clock['time'],
                'full_fill_time': full_clock['time'],
                'first_fill_beyond_close': first_clock['beyond_close'],
                'full_fill_beyond_close': full_clock['beyond_close'],
            })

        # 可信度评分模型（五个维度加权，每窗独立打分）：
        #   样本量     S1 = min(1, 成交笔数/20)                 权重 20%
        #   稳定性     S2 = max(0, 1 − 本窗净速度偏离各窗均值)    权重 30%（仅 1 窗可算时按中性 0.5）
        #   外推距离   S3 = max(0, 1 − (预测等待/观察时长)/10)    权重 25%
        #   显著性     S4 = min(1, |净速度|/(消耗+新增)/0.3)      权重 15%
        #   观察时长   S5 = min(1, 实际观察秒数/120)              权重 10%
        # 头条预测 = 得分最高的可计算窗口（同分取更长窗口）；全部不可计算时
        # 最长窗口兜底展示原因，可信度直接判低。
        available_rows = [row for row in window_rows if row['available']]
        consensus_net = (
            sum(row['net_queue_rate'] for row in available_rows) / len(available_rows)
            if available_rows else 0.0
        )
        for row in window_rows:
            if not row['available']:
                row['score'] = None
                row['score_parts'] = None
                continue
            elapsed = float(row['elapsed_seconds'] or 0)
            net = row['net_queue_rate']
            tc = row['trade_count']
            s1 = min(1.0, tc / 20)
            if len(available_rows) >= 2 and consensus_net > 0:
                dev = abs(net - consensus_net) / consensus_net
                s2 = max(0.0, 1.0 - dev)
            else:
                dev = None
                s2 = 0.5  # 只有一个窗口可算，无法交叉验证，按中性计
            wait_seconds = (row['first_fill_wait_ms'] or 0) / 1000
            r = wait_seconds / elapsed if elapsed > 0 else float('inf')
            s3 = max(0.0, 1.0 - r / 10) if r != float('inf') else 0.0
            total_flow = row['depletion_rate'] + row['arrival_rate']
            q = abs(net) / total_flow if total_flow > 0 else 0.0
            s4 = min(1.0, q / 0.3)
            s5 = min(1.0, elapsed / 120)
            score = 100 * (0.20 * s1 + 0.30 * s2 + 0.25 * s3 + 0.15 * s4 + 0.10 * s5)
            row['score'] = round(score, 1)
            row['score_parts'] = {
                's1': s1, 's2': s2, 's3': s3, 's4': s4, 's5': s5,
                'dev': dev, 'r': r, 'q': q,
            }

        if available_rows:
            selected = max(available_rows, key=lambda row: (row['score'], row['window_ms']))
        else:
            selected = window_rows[-1]
        selected['selected'] = True

        price_active = rank == 1
        warnings = []
        if not selected['available'] and selected['reason']:
            warnings.append(selected['reason'])

        selected_score = selected['score'] if selected['score'] is not None else 0.0
        if not selected['available']:
            confidence = 'low'
            confidence_reasons = [
                '所有候选窗口（30s/2min/5min/10min）均无法计算预测时间，预测不可用',
            ]
            if selected['reason']:
                confidence_reasons.append(f'兜底窗口（{selected["window_ms"] // 1000}s）：{selected["reason"]}')
        else:
            if selected_score >= 70:
                confidence = 'high'
            elif selected_score >= 40:
                confidence = 'medium'
            else:
                confidence = 'low'
            # 徽标悬浮只展示分档区间；各维度细分在分窗口表格的得分悬浮中展示
            confidence_reasons = [
                '高：总分 ≥ 70 分；中：40 ~ 70 分；低：< 40 分',
            ]

        return {
            'success': True,
            'order': summary,
            'as_of_time': time_str,
            'actual': actual,
            'current_queue': {
                **current_position,
                'higher_levels_volume': higher_levels_volume,
                'ahead_total_volume': ahead_total,
            },
            'prediction': {
                'available': selected['available'],
                'lookahead_safe': True,
                'confidence': confidence,
                'confidence_score': selected_score,
                'confidence_reasons': confidence_reasons,
                'price_active': price_active,
                'has_filled_as_of': has_filled_as_of,
                'filled_volume_as_of': filled_volume_as_of,
                'first_fill_wait_ms': selected['first_fill_wait_ms'],
                'full_fill_wait_ms': selected['full_fill_wait_ms'],
                'first_fill_time': selected['first_fill_time'],
                'full_fill_time': selected['full_fill_time'],
                'first_fill_beyond_close': selected['first_fill_beyond_close'],
                'full_fill_beyond_close': selected['full_fill_beyond_close'],
                'queue_depletion_rate': selected['depletion_rate'],
                'level1_arrival_rate': selected['arrival_rate'],
                'net_queue_rate': selected['net_queue_rate'],
                'same_price_trade_rate': selected['trade_rate'],
                'selected_window_ms': selected['window_ms'],
                'windows': window_rows,
                'warnings': warnings,
                'basis': {
                    'queue_window_ms': selected['actual_window_ms'],
                    'queue_observed_seconds': float(selected['elapsed_seconds'] or 0),
                    'queue_depleted_volume': selected['depleted_volume'],
                    'queue_arrived_volume': selected['arrived_volume'],
                    'trade_window_ms': selected['actual_window_ms'],
                    'trade_count': selected['trade_count'],
                    'trade_volume': selected['trade_volume'],
                    'average_trade_size': selected['average_trade_size'],
                },
            },
        }
    def get_order_lifecycle(self, order_id: int) -> Dict[str, Any]:
        """
        重建订单生命周期：挂出 -> 逐笔成交/撤单 -> 终结。
        order_id 为 CSV 的 orderid（即快照中的 order_local_id）。
        """
        self.update_access_time()
        self._load_tick_data()

        if self._csord_df is None:
            return {"success": False, "message": "csord 数据文件不存在"}

        # 出生事件：csord 中按 orderid 定位
        born = self._csord_df[self._csord_df['orderid'].astype('int64') == order_id]
        if born.empty:
            return {"success": False, "message": f"订单 {order_id} 在 csord 中不存在"}
        born_row = born.sort_values('datetime').iloc[0]

        events: List[Dict] = [{
            'time': str(born_row['datetime']),
            'type': 'create',
            'size': float(born_row['size']),
            'price': float(born_row['price']),
            'remaining_after': float(born_row['size']),
        }]
        remaining = float(born_row['size'])

        # 后续事件：cstra 中按 bid/ask orderid 关联，exectype '1'=成交 '2'=撤单
        if self._cstra_df is not None:
            tra = self._cstra_df[
                (self._cstra_df['bidorderid'].astype('int64') == order_id) |
                (self._cstra_df['askorderid'].astype('int64') == order_id)
            ].sort_values('datetime')
            for _, row in tra.iterrows():
                exectype = self._clean_exectype(row['exectype'])
                event_type = 'trade' if exectype == '1' else ('cancel' if exectype == '2' else f'unknown({exectype})')
                remaining = max(0.0, remaining - float(row['size']))
                events.append({
                    'time': str(row['datetime']),
                    'type': event_type,
                    'size': float(row['size']),
                    'price': float(row['price']),
                    'remaining_after': remaining,
                })

        # 每个事件时刻的队列位置/身前身后量（引擎只读调用，事件数通常很少）
        for ev in events:
            time_part = ev['time'].split(' ')[-1]
            pos = self._queue_position_at(time_part, order_id)
            ev['queue'] = pos  # 不在盘口（已终结/集合竞价）时为 None

        # 汇总
        filled = sum(e['size'] for e in events if e['type'] == 'trade')
        cancelled = sum(e['size'] for e in events if e['type'] == 'cancel')
        total = float(born_row['size'])
        create_ts = pd.Timestamp(events[0]['time'])
        trades = [e for e in events if e['type'] == 'trade']
        if remaining <= 0 and filled >= total - 1e-6:
            outcome = '全部成交'
        elif remaining <= 0 and cancelled > 0:
            outcome = '部分成交后撤单' if filled > 0 else '全部撤单'
        else:
            outcome = '收盘残留'

        # 收盘残留订单没有真实的终结事件，使用当日收盘时刻作为生命周期终点。
        # 成交/撤单订单仍使用最后一条真实事件，避免改变原有统计口径。
        end_ts = (
            pd.Timestamp(f'{self.date} 15:00:00.000')
            if outcome == '收盘残留'
            else pd.Timestamp(events[-1]['time'])
        )
        summary = {
            'order_id': order_id,
            'side': int(born_row['side']),
            'price': float(born_row['price']),
            'size': total,
            'create_time': str(born_row['datetime']),
            'filled_size': filled,
            'cancelled_size': cancelled,
            'outcome': outcome,
            'lifespan_ms': int((end_ts - create_ts).total_seconds() * 1000),
            'first_fill_delay_ms': int((pd.Timestamp(trades[0]['time']) - create_ts).total_seconds() * 1000) if trades else None,
        }
        return {"success": True, "summary": summary, "events": events}

    def __str__(self) -> str:
        return f"TradeBook({self.symbol}_{self.date}, last_accessed={self.last_accessed_at})"

    def __repr__(self) -> str:
        return self.__str__()
