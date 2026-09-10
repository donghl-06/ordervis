#!/usr/bin/env python3
"""
回归验证：对比优化后 TradeBook 与 git HEAD 旧版在同数据同参数下的输出是否一致。

对比项：
  1. get_snapshot_by_time（含 create_time enrich）
  2. get_trade_flow_series（C2 流量序列，含一档量/价、撤单修补、成交价）
  3. get_order_queue_series（锁定订单队列序列，取真实订单）
  4. _best_level_cancel_volumes / _second_level_cancel_volumes（撤单修补口径）

用法: ../.venv/bin/python test/verify_parity_new_old.py [sym] [date]
"""
import importlib.util
import sys

sys.path.insert(0, "/home/donghuale/project2-test")
sys.path.insert(0, "/home/donghuale/project2-test/ordervis_server")

SYM = sys.argv[1] if len(sys.argv) > 1 else "000027.SZ"
DATE = sys.argv[2] if len(sys.argv) > 2 else "2025-08-01"
DATA = "/home/donghuale/project2-test/ordervis_server/data"


def load_old_module():
    path = "/home/donghuale/project2-test/ordervis_server/tmp_old/tradebook_old.py"
    spec = importlib.util.spec_from_file_location("tradebook_old", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def first_diff(a, b, path="root", depth=0):
    """递归找第一个差异，返回描述字符串；一致返回 None。"""
    if depth > 8:
        return None
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return f"{path}: 类型 {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                return f"{path}.{k}: 旧版缺失"
            if k not in b:
                return f"{path}.{k}: 新版缺失"
            d = first_diff(a[k], b[k], f"{path}.{k}", depth + 1)
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: 长度 {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]", depth + 1)
            if d:
                return d
        return None
    if a != b:
        return f"{path}: {a!r} != {b!r}"
    return None


def main():
    old_mod = load_old_module()
    from ordervis_server.utils.tradebook import TradeBook as NewTB

    old_tb = old_mod.TradeBook(SYM, DATE, DATA)
    new_tb = NewTB(SYM, DATE, DATA)

    checks = []

    # 1. 快照
    for t in ["10:00:00.000", "13:30:00.000", "14:59:59.999"]:
        d = first_diff(old_tb.get_snapshot_by_time(t), new_tb.get_snapshot_by_time(t))
        checks.append((f"snapshot_by_time({t})", d))

    # 2. C2 流量序列（两个窗口粒度）
    for window, pts in [(3000, 60), (300000, 60)]:
        a = old_tb.get_trade_flow_series("13:30:00.000", window, pts)
        b = new_tb.get_trade_flow_series("13:30:00.000", window, pts)
        checks.append((f"trade_flow_series(w={window})", first_diff(a, b)))

    # 3. 锁定订单队列序列（取真实订单）
    snap = new_tb.get_snapshot_by_time("13:30:00.000")
    ids = []
    for lvl in (snap.get("levels") or {}).values():
        for o in lvl.get("orders", []):
            ids.append(int(o["order_local_id"]))
        if len(ids) >= 3:
            break
    if ids:
        a = old_tb.get_order_queue_series("13:30:00.000", 3000, ids[:3], 60)
        b = new_tb.get_order_queue_series("13:30:00.000", 3000, ids[:3], 60)
        checks.append((f"order_queue_series(ids={ids[:3]})", first_diff(a, b)))

    # 4. 撤单修补（一档/二档，两个窗口）
    from ordervis_server.utils.tradebook import _time_to_ms
    end = _time_to_ms("13:30:00.000")
    for level_fn_old, level_fn_new, label in [
        (old_tb._best_level_cancel_volumes, new_tb._best_level_cancel_volumes, "best_level_cancel"),
        (old_tb._second_level_cancel_volumes, new_tb._second_level_cancel_volumes, "second_level_cancel"),
    ]:
        for win in (60000, 3000):
            a = level_fn_old(end - win, end)
            b = level_fn_new(end - win, end)
            checks.append((f"{label}({win}ms)", first_diff(a, b)))

    # 5. 订单生命周期 + 成交预测（iterrows 改造涉及）
    if ids:
        oid = ids[0]
        a = old_tb.get_order_lifecycle(oid)
        b = new_tb.get_order_lifecycle(oid)
        checks.append((f"order_lifecycle({oid})", first_diff(a, b)))
        a = old_tb.get_order_execution_estimate("13:30:00.000", oid)
        b = new_tb.get_order_execution_estimate("13:30:00.000", oid)
        checks.append((f"order_execution_estimate({oid})", first_diff(a, b)))

    print(f"=== 新旧实现输出对比 {SYM} {DATE} ===")
    failed = 0
    for label, d in checks:
        if d is None:
            print(f"  [一致] {label}")
        else:
            failed += 1
            print(f"  [差异] {label}: {d}")
    print(f"\n{'全部一致 ✓' if failed == 0 else f'{failed} 项存在差异 ✗'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
