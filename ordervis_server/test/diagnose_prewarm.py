#!/usr/bin/env python3
"""
验证「定向状态预热」：用户在 T 停留后，前向/后向步进命中预建缓存。

场景（模拟真实使用：跳到 T → 停留等预热完成 → 步进）：
  1. 冷跳对照：清空快照缓存并静音预热线程，测 flow/queue 基线
  2. 触发预热（模拟前端四请求链的 snapshot+flow），等 worker 空闲
  3. 命中验证：T+3s / T+1s / T-1s 跳转计时（缓存保留，预期毫秒级）
  4. 30ms 小步连跳 20 次（不静音 worker，考察真实的追赶/插队行为）

用法: ../../.venv/bin/python test/diagnose_prewarm.py [sym] [date] [HH:MM:SS.mmm]
"""
import sys
import time

sys.path.insert(0, "/home/donghuale/project2-test")
sys.path.insert(0, "/home/donghuale/project2-test/ordervis_server")  # lib 包所在目录

from ordervis_server.utils.tradebook import TradeBook, _ms_to_time, _time_to_ms

SYM = sys.argv[1] if len(sys.argv) > 1 else "000930.SZ"
DATE = sys.argv[2] if len(sys.argv) > 2 else "2024-06-28"
JUMP = sys.argv[3] if len(sys.argv) > 3 else "13:30:00.000"
WINDOW_MS = 3000


def quiet_worker(tb):
    """作废进行中/挂起的预热扫描（generation 越过 worker 持有的旧值即中断）。"""
    tb._prewarm_generation += 1
    tb._prewarm_wakeup.clear()


def wait_worker_idle(tb, timeout=180):
    """等预热线程真正闲下来：状态（busy/anchor/generation）静默超过 1s。

    不能只看 busy=False——worker 唤醒后有 300ms debounce 静默期，期间
    busy 尚未置位，单点检查会误判完成。
    """
    t0 = time.time()
    last_state, last_change = None, time.time()
    while time.time() - t0 < timeout:
        state = (tb._prewarm_busy, tb._prewarm_anchor_ms, tb._prewarm_generation)
        if state != last_state:
            last_state, last_change = state, time.time()
        elif not tb._prewarm_busy and time.time() - last_change > 1.0:
            return time.time() - t0
        time.sleep(0.1)
    return None


def timed_flow(tb, t_ms):
    t0 = time.perf_counter()
    tb.get_trade_flow_series(_ms_to_time(t_ms), WINDOW_MS)
    return (time.perf_counter() - t0) * 1000


def timed_flow_queue(tb, t_ms):
    t0 = time.perf_counter()
    tb.get_trade_flow_series(_ms_to_time(t_ms), WINDOW_MS)
    tb.get_order_queue_series(_ms_to_time(t_ms), WINDOW_MS, [1])
    return (time.perf_counter() - t0) * 1000


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * p), len(ordered) - 1)]


def main():
    print(f"=== 定向状态预热诊断: {SYM} {DATE}，跳转目标 {JUMP} ===\n")
    t0 = time.perf_counter()
    tb = TradeBook(SYM, DATE)
    print(f"TradeBook 初始化: {time.perf_counter()-t0:.1f}s，总状态数 {tb.get_total_changes()}")

    anchor_ms = _time_to_ms(JUMP)

    # --- 场景 1：冷跳对照（缓存清空 + worker 静音） ---
    quiet_worker(tb)
    tb._snapshot_cache.clear()
    cold = timed_flow(tb, anchor_ms)
    print(f"\n[对照] 冷缓存 flow({WINDOW_MS}ms 窗):        {cold:9.1f} ms")

    # --- 场景 2：触发预热并等完成 ---
    tb.get_snapshot_by_time(_ms_to_time(anchor_ms))
    tb.get_trade_flow_series(_ms_to_time(anchor_ms), WINDOW_MS)
    waited = wait_worker_idle(tb)
    if waited is None:
        print("[警告] 预热线程 180s 未空闲，后续结果按部分预热解读")
    else:
        print(f"[预热] worker 完成耗时 {waited:.1f}s，缓存状态数 {len(tb._snapshot_cache)}")

    # --- 场景 3：命中验证（保留缓存，逐点计时前静音 worker 防插队） ---
    print(f"\n[命中] 预热完成后跳转（3s 窗 flow+queue 双请求）:")
    for label, t_ms in (
        ("T+3s ", anchor_ms + 3000),
        ("T+1s ", anchor_ms + 1000),
        ("T-1s ", anchor_ms - 1000),
        ("T+30m", anchor_ms + 30 * 60000),
    ):
        quiet_worker(tb)
        elapsed = timed_flow_queue(tb, t_ms)
        verdict = "命中" if elapsed < 100 else "部分命中" if elapsed < 1000 else "未命中"
        print(f"  跳 {label} -> {elapsed:9.1f} ms  ({verdict})")

    # --- 场景 4：小步连跳（worker 不静音，考察真实的追赶/插队行为） ---
    print(f"\n[连跳] 30ms 小步 ×20（worker 在场，模拟用户狂点）:")
    samples = []
    t_ms = anchor_ms
    for _ in range(20):
        t_ms += 30
        samples.append(timed_flow(tb, t_ms))
    print("  每步:", " ".join(f"{s:.0f}" for s in samples))
    print(f"  p50 {percentile(samples, 0.5):7.1f} ms | p95 {percentile(samples, 0.95):7.1f} ms | "
          f"max {max(samples):7.1f} ms")

    # --- 场景 5：连跳后停留 2s（debounce 过后 worker 追预热）再步进 ---
    time.sleep(2)
    wait_worker_idle(tb)
    quiet_worker(tb)
    elapsed = timed_flow_queue(tb, t_ms + 30)
    print(f"[停留] 停留 2s 后再 +30ms 步进:          {elapsed:9.1f} ms  "
          f"({'命中' if elapsed < 100 else '部分命中' if elapsed < 1000 else '未命中'})")


if __name__ == "__main__":
    main()
