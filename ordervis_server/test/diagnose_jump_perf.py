#!/usr/bin/env python3
"""
诊断「输入时间直接跳转（如 13:30:00.000）加载慢」的瓶颈。

模拟前端跳转触发的完整后端链路，分级计时：
  1. TradeBook 初始化（C++ 引擎重建订单簿）
  2. snapshot_by_time        -> query_by_time + create_time enrich
  3. pastTimeTradeInfo       -> 6 次 query_market_data + 撤单修补(首建)
  4. trade_flow_series       -> 61 次 query_market_data + 61 次 query_by_time
                                + 撤单修补 + 成交价记录(首建)
  5. order_queue_series      -> 60 次 query_by_time + 逐档遍历

用法: ../.venv/bin/python test/diagnose_jump_perf.py [sym] [date] [HH:MM:SS.mmm]
"""
import sys
import time

sys.path.insert(0, "/home/donghuale/project2-test")
sys.path.insert(0, "/home/donghuale/project2-test/ordervis_server")  # lib 包所在目录

SYM = sys.argv[1] if len(sys.argv) > 1 else "000027.SZ"
DATE = sys.argv[2] if len(sys.argv) > 2 else "2025-08-01"
JUMP = sys.argv[3] if len(sys.argv) > 3 else "13:30:00.000"


def timed(label, fn, repeat=1):
    """计时执行 fn()，首次与重复都报告。"""
    t0 = time.perf_counter()
    result = fn()
    first = time.perf_counter() - t0
    if repeat > 1:
        t0 = time.perf_counter()
        for _ in range(repeat - 1):
            fn()
        again = (time.perf_counter() - t0) / (repeat - 1)
        print(f"{label:<46} 首次 {first*1000:10.1f} ms | 重复 {again*1000:10.2f} ms")
    else:
        print(f"{label:<46} 首次 {first*1000:10.1f} ms")
    return result


def wait_prewarm(tb, timeout=30):
    """等待后台预热完成（模拟真实用户：初始化后看盘片刻再跳转）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if (
            0 in tb._cancel_records_cache
            and 1 in tb._cancel_records_cache
            and tb._trade_price_records is not None
        ):
            return time.time() - t0
        time.sleep(0.1)
    return None


def full_jump_simulation(tb):
    """模拟前端跳转触发的完整请求序列（预热已完成）。"""
    print("\n=== 全链路跳转模拟（预热完成后，模拟前端 4 个请求） ===")
    tb._snapshot_cache.clear()
    t_total = time.perf_counter()

    t0 = time.perf_counter()
    tb.get_snapshot_by_time(JUMP)
    print(f"  1. snapshot_by_time            {(time.perf_counter()-t0)*1000:8.1f} ms")

    t0 = time.perf_counter()
    from ordervis_server.utils.tradebook import _time_to_ms, _ms_to_time
    cur = tb.get_market_data(JUMP)
    for ms in (60000, 3000, 500, 50, 10):
        tb.get_market_data(_ms_to_time(_time_to_ms(JUMP) - ms))
    end_ms = _time_to_ms(JUMP)
    tb._best_level_cancel_volumes(end_ms - 60000, end_ms)
    print(f"  2. pastTimeTradeInfo           {(time.perf_counter()-t0)*1000:8.1f} ms")

    t0 = time.perf_counter()
    tb.get_trade_flow_series(JUMP, 3000, 60)
    print(f"  3. trade_flow_series(3s窗)     {(time.perf_counter()-t0)*1000:8.1f} ms")

    t0 = time.perf_counter()
    snap = tb._query_by_time_cached(JUMP) or {}
    ids = []
    for lvl in (snap.get("levels") or {}).values():
        for o in lvl.get("orders", [])[:1]:
            ids.append(int(o["order_local_id"]))
        if len(ids) >= 2:
            break
    if ids:
        tb.get_order_queue_series(JUMP, 3000, ids, 60)
    print(f"  4. order_queue_series(3s窗)    {(time.perf_counter()-t0)*1000:8.1f} ms")

    print(f"  合计                            {(time.perf_counter()-t_total)*1000:8.1f} ms")

    # 相邻跳转（+1s，窗口重叠 2/3）：验证状态指纹缓存的跨窗口命中
    near = _ms_to_time(_time_to_ms(JUMP) + 1000)
    t0 = time.perf_counter()
    tb.get_trade_flow_series(near, 3000, 60)
    print(f"  5. 再跳 +1s 的 C2 图(重叠窗)    {(time.perf_counter()-t0)*1000:8.1f} ms")


def main():
    from ordervis_server.utils.tradebook import TradeBook

    print(f"=== 诊断 {SYM} {DATE} 跳转到 {JUMP} ===\n")

    tb = timed("TradeBook 初始化(引擎重建)", lambda: TradeBook(SYM, DATE))

    waited = wait_prewarm(tb)
    print(f"后台预热等待: {'%.1fs' % waited if waited is not None else '超时(未完成)'}")

    print(f"\n总快照数: {tb.get_total_snapshots()}, 总变化数: {tb.get_total_changes()}")

    full_jump_simulation(tb)

    print("\n--- 引擎层单点 ---")
    timed("engine.query_by_time(13:30:00.000)",
          lambda: tb.visualizer.query_by_time(DATE, JUMP), repeat=5)
    timed("engine.query_market_data(13:30:00.000)",
          lambda: tb.visualizer.query_market_data(DATE, JUMP), repeat=5)

    print("\n--- 接口 1: snapshot_by_time ---")
    snap = timed("get_snapshot_by_time", lambda: tb.get_snapshot_by_time(JUMP), repeat=3)
    order_count = sum(
        len(lvl.get("orders", [])) for lvl in (snap.get("levels") or {}).values()
    )
    print(f"    六档订单总数: {order_count}")

    print("\n--- 接口 2: pastTimeTradeInfo 内部 ---")
    from ordervis_server.utils.tradebook import _time_to_ms, _ms_to_time, CONTINUOUS_AUCTION_START_MS

    def past_time_info():
        cur = tb.get_market_data(JUMP)
        for ms in (60000, 3000, 500, 50, 10):
            past = tb.get_market_data(_ms_to_time(_time_to_ms(JUMP) - ms))
        # 撤单修补（首次触发 _cancel_records_at_level(0) 全量构建）
        end_ms = _time_to_ms(JUMP)
        tb._best_level_cancel_volumes(end_ms - 60000, end_ms)

    timed("pastTimeTradeInfo 全流程(含撤单修补首建)", past_time_info, repeat=3)

    print("\n--- 接口 3: trade_flow_series ---")
    timed("get_trade_flow_series(60点)", lambda: tb.get_trade_flow_series(JUMP, 300000, 60), repeat=3)

    print("\n--- 模拟真实跳转：C2 图与锁定图同窗口并发（缓存命中场景） ---")
    tb._snapshot_cache.clear()
    timed("  C2 图请求(冷缓存)", lambda: tb.get_trade_flow_series(JUMP, 3000, 60))
    timed("  锁定图同窗口请求(应命中缓存)",
          lambda: tb.get_order_queue_series(JUMP, 3000, [42], 60))

    print("\n--- 接口 4: order_queue_series（锁定订单） ---")
    # 从快照里取两个订单做锁定模拟
    sample_ids = []
    for lvl in (snap.get("levels") or {}).values():
        for o in lvl.get("orders", [])[:1]:
            sample_ids.append(int(o["order_local_id"]))
        if len(sample_ids) >= 2:
            break
    if sample_ids:
        timed(
            f"get_order_queue_series(60点, {len(sample_ids)}单)",
            lambda: tb.get_order_queue_series(JUMP, 300000, sample_ids, 60),
            repeat=3,
        )

    print("\n--- 撤单/成交缓存构建细项 ---")
    tb2_cancels = tb._cancel_records_at_level(1)  # 二档撤单（未构建过）
    t0 = time.perf_counter()
    tb._cancel_records_at_level(1)
    print(f"{'_cancel_records_at_level(1) 首建':<46} 首次 {(time.perf_counter()-t0)*1000:10.1f} ms")

    import pandas as pd
    csord = pd.read_csv(tb._csord_path())
    cstra = pd.read_csv(tb._cstra_path())
    et = cstra["exectype"].astype(str).str.strip("b' ")
    print(f"\ncsord 行数: {len(csord)}, cstra 行数: {len(cstra)}, 撤单行数(et=2): {(et=='2').sum()}, 成交行数(et=1): {(et=='1').sum()}")


if __name__ == "__main__":
    main()
