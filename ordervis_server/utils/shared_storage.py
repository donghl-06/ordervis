#!/usr/bin/env python3
"""
TradeBook本地缓存存储类
每个进程维护独立的内存缓存，支持自动清理过期数据
适用于多进程FastAPI应用
"""

import os
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, List

from .tradebook import TradeBook, DEFAULT_DATA_PATH
from .progress_manager import progress_manager, create_progress_callback
from .adata_worker import fetch_dataset_to_csv
from ordervis_server.package import backend_logger
import lib.sh_revert_module as sh_revert_module
import pandas as pd


def is_orderbook_fund_code(symbol: str) -> bool:
    """判断基金代码是否属于交易所场内、理论上具备盘口数据的市场。"""
    normalized = str(symbol or "").strip().upper()
    return normalized.endswith((".SH", ".SZ"))


def is_otc_fund_code(symbol: str) -> bool:
    """判断是否为场外基金代码（.OF），此类标的没有本页面需要的订单簿数据。"""
    return str(symbol or "").strip().upper().endswith(".OF")


DATA_PATH = DEFAULT_DATA_PATH
os.makedirs(DATA_PATH, exist_ok=True)


class SharedTradeBookStorage:    
    def __init__(self, cleanup_interval: int = 3600, expiry_hours: float = 12.0):
        """ 初始化本地缓存存储 """
        # 本地缓存：存储TradeBook对象（每个进程独有）
        self.local_cache: Dict[str, TradeBook] = {}
        self._initializing_tasks: Dict[str, str] = {}
        
        # 本地锁（用于线程安全）
        self.local_lock = threading.Lock()

        # 初始化全局串行锁：不同标的的 TradeBook 重建若并发，C++ 引擎会
        # 互相踩内存抛 std::bad_alloc（2026-09-11 实测：561790.SH 与
        # 512010.SH/002156.SZ 并发初始化双双失败，单跑均成功）。引擎重建
        # 不释放 GIL，并发初始化本来也没有吞吐收益，串行排队即可。
        self._init_lock = threading.Lock()
        
        # 配置
        self.cleanup_interval = cleanup_interval
        self.expiry_hours = expiry_hours
        
        # 日志
        self.logger = backend_logger.Log("shared_storage")
        self.log_level = backend_logger.LogLevel
        
        # 股票和基金代码缓存
        self._stock_codes: set = set()
        self._fund_codes: set = set()
        self._codes_loaded: bool = False
        
        # 启动清理线程
        self._cleanup_thread = None
        self._stop_cleanup = False
        self.start_cleanup_thread()
    
    def _generate_key(self, symbol: str, date: str) -> str:
        """生成存储key"""
        return f"{symbol}_{date}"
    
    def _load_symbol_codes(self):
        """从数据库加载股票和基金代码"""
        if self._codes_loaded:
            return
        
        try:
            from ordervis_server.utils.utils import get_data_sqlserver
            current_date = datetime.now().strftime("%Y%m%d")
            
            stock_sql = f"""
                SELECT S_INFO_WINDCODE as code
                FROM Filesync.dbo.asharedescription
                WHERE
                    (S_INFO_LISTDATE IS NULL OR S_INFO_LISTDATE = '' OR S_INFO_LISTDATE <= '{current_date}')
                    AND (S_INFO_DELISTDATE IS NULL OR S_INFO_DELISTDATE = '' OR S_INFO_DELISTDATE > '{current_date}')
            """
            stock_data = get_data_sqlserver(stock_sql)
            if not stock_data.empty:
                self._stock_codes = set(stock_data['code'].tolist())
            
            fund_sql = f"""
                SELECT F_INFO_WINDCODE as code
                FROM Filesync.dbo.chinamutualfunddescription
                WHERE
                    (F_INFO_LISTDATE IS NULL OR F_INFO_LISTDATE = '' OR F_INFO_LISTDATE <= '{current_date}')
                    AND (F_INFO_DELISTDATE IS NULL OR F_INFO_DELISTDATE = '' OR F_INFO_DELISTDATE > '{current_date}')
            """
            fund_data = get_data_sqlserver(fund_sql)
            if not fund_data.empty:
                self._fund_codes = {
                    code for code in fund_data["code"].tolist()
                    if is_orderbook_fund_code(code)
                }
            
            self._codes_loaded = True
            self.logger.n_log(f"加载代码列表: 股票 {len(self._stock_codes)}个, 基金 {len(self._fund_codes)}个", self.log_level.INFO)
        except Exception as e:
            self.logger.n_log(f"加载代码列表失败: {e}", self.log_level.ERROR)
    
    def _is_etf(self, symbol: str) -> bool:
        """判断是否为基金/ETF（根据代码来源）"""
        self._load_symbol_codes()
        result = symbol in self._fund_codes
        in_stock = symbol in self._stock_codes
        self.logger.n_log(f"[DEBUG] _is_etf('{symbol}') = {result}, 股票列表包含: {in_stock}", self.log_level.DEBUG)
        return result

    def classify_symbol(self, symbol: str) -> str:
        """返回证券类型: 'fund' / 'stock' / 'unknown'"""
        self._load_symbol_codes()
        if symbol in self._fund_codes:
            return 'fund'
        if symbol in self._stock_codes:
            return 'stock'
        return 'unknown'

    def _get_data_db(self, symbol: str, date: str, progress_callback=None) -> None:
        """确保三类本地数据存在；缺失数据通过隔离子进程从 adata 拉取。"""
        if is_otc_fund_code(symbol):
            raise ValueError(
                f"{symbol} 是场外基金（.OF），没有可用于盘口回放的订单簿数据；请改选场内 ETF/LOF。"
            )

        def report(progress: int, message: str):
            if progress_callback:
                progress_callback(progress, message)

        def fetch_required(dataset: str, data_name: str, path: str):
            try:
                fetch_dataset_to_csv(dataset, date, symbol, path)
            except Exception as exc:
                if "数据为空" in str(exc):
                    raise ValueError(
                        f"{symbol} 在 {date} 没有可用的盘口数据（{data_name}为空）；"
                        "请确认选择的是场内 ETF/LOF，且该日期有交易数据。"
                    ) from exc
                raise

        cstra_path = os.path.join(DATA_PATH, f"cstra_{symbol}_{date}.csv")
        if not os.path.exists(cstra_path):
            report(5, "正在从 adata 获取逐笔成交数据...")
            self.logger.n_log(f"获取并转换 cstra 数据: {symbol}_{date}", self.log_level.INFO)
            fetch_required("cstra", "逐笔成交", cstra_path)
        report(18, "逐笔成交数据已就绪")

        csord_path = os.path.join(DATA_PATH, f"csord_{symbol}_{date}.csv")
        if not os.path.exists(csord_path):
            report(20, "正在从 adata 获取逐笔委托数据...")
            self.logger.n_log(f"获取并转换 csord 数据: {symbol}_{date}", self.log_level.INFO)
            fetch_required("csord", "逐笔委托", csord_path)

            if symbol.endswith(".SH"):
                recovered_path = f"{csord_path}.recovered.part"
                try:
                    engine = sh_revert_module.RecoverEngine()
                    engine.recover(csord_path, cstra_path, recovered_path)
                    os.replace(recovered_path, csord_path)
                except Exception:
                    if os.path.exists(recovered_path):
                        os.remove(recovered_path)
                    if os.path.exists(csord_path):
                        os.remove(csord_path)
                    raise
        report(35, "逐笔委托数据已就绪")

        cstick_path = os.path.join(DATA_PATH, f"cstick_{symbol}_{date}.csv")
        if not os.path.exists(cstick_path):
            report(38, "正在从 adata 获取盘口快照数据...")
            self.logger.n_log(f"获取并转换 cstick 数据: {symbol}_{date}", self.log_level.INFO)
            fetch_required("cstick", "盘口快照", cstick_path)
        report(50, "数据文件准备完成，正在重建订单簿...")


    def _put(self, tradebook: TradeBook) -> bool:
        """
        存储TradeBook到本地缓存
        """
        key = tradebook.get_key()
        
        try:
            with self.local_lock:
                self.local_cache[key] = tradebook
            
            self.logger.n_log(f"存储TradeBook到本地缓存: {key}", self.log_level.INFO)
            return True
            
        except Exception as e:
            self.logger.n_log(f"存储TradeBook失败: {key}, 错误: {e}", self.log_level.ERROR)
            return False
    
    def get(self, symbol: str, date: str) -> Optional[TradeBook]:
        """
        获取TradeBook
        1. 检查本地缓存
        2. 如果不存在，创建新的TradeBook对象并缓存
        """
        key = self._generate_key(symbol, date)
        is_etf = self._is_etf(symbol)

        try:
            if not self.exists(symbol, date):
                self._get_data_db(symbol, date)
                self._put(TradeBook(symbol, date, DATA_PATH, is_ETF=is_etf))
        except Exception as e:
            self.logger.n_log(f"获取数据文件失败: {key}, 错误: {e}", self.log_level.ERROR)
            return None
        
        try:
            # 检查本地缓存
            with self.local_lock:
                if key in self.local_cache:
                    tradebook = self.local_cache[key]
                    
                    # 检查是否过期
                    if tradebook.is_expired(self.expiry_hours):
                        self.logger.n_log(f"TradeBook已过期，重新创建: {key}", self.log_level.INFO)
                        del self.local_cache[key]
                    else:
                        return tradebook
                
                # 创建新的TradeBook对象
                self.logger.n_log(f"创建新的TradeBook: {key}", self.log_level.INFO)
                tradebook = TradeBook(symbol, date, DATA_PATH, is_ETF=is_etf)
                self.local_cache[key] = tradebook
                return tradebook
                
        except Exception as e:
            self.logger.n_log(f"获取TradeBook失败: {key}, 错误: {e}", self.log_level.ERROR)
            return None

    def get_with_progress(self, symbol: str, date: str) -> str:
        """
        异步获取 TradeBook，返回任务 ID；同一标的日期只保留一个初始化任务。
        """
        key = self._generate_key(symbol, date)
        if is_otc_fund_code(symbol):
            raise ValueError(
                f"{symbol} 是场外基金（.OF），没有可用于盘口回放的订单簿数据；请改选场内 ETF/LOF。"
            )
        is_etf = self._is_etf(symbol)
        self.logger.n_log(f"[DEBUG] get_with_progress: {symbol}, is_ETF={is_etf}", self.log_level.DEBUG)

        with self.local_lock:
            if key in self.local_cache:
                tradebook = self.local_cache[key]
                if not tradebook.is_expired(self.expiry_hours):
                    return None

            existing_task_id = self._initializing_tasks.get(key)
            if existing_task_id:
                existing_task = progress_manager.get_task_info(existing_task_id)
                if existing_task and existing_task.get("status") == "initializing":
                    return existing_task_id
                self._initializing_tasks.pop(key, None)

            task_id = progress_manager.create_task(symbol, date)
            self._initializing_tasks[key] = task_id

        def init_tradebook():
            try:
                progress_manager.update_progress(task_id, 1, "正在准备 adata 数据...")
                self._get_data_db(
                    symbol,
                    date,
                    lambda progress, message: progress_manager.update_progress(
                        task_id, progress, message
                    ),
                )
                self.logger.n_log(f"[DEBUG] 数据文件准备完成: {key}", self.log_level.DEBUG)

                callback = create_progress_callback(task_id, 50, 99)
                self.logger.n_log(f"[DEBUG] 开始创建TradeBook, is_ETF={is_etf}", self.log_level.DEBUG)
                # 仅 C++ 引擎重建需要串行（数据下载/转换不参与，避免网络慢阻塞他人）
                if self._init_lock.locked():
                    progress_manager.update_progress(task_id, 50, "排队等待其它标的初始化完成...")
                with self._init_lock:
                    tradebook = TradeBook.create_with_progress(
                        symbol, date, DATA_PATH, callback, is_ETF=is_etf
                    )

                with self.local_lock:
                    self.local_cache[key] = tradebook

                progress_manager.complete_task(task_id, success=True)
            except Exception as exc:
                self.logger.n_log(f"[DEBUG] 初始化异常: {exc}", self.log_level.ERROR)
                self.logger.n_log(
                    f"异步初始化TradeBook失败: {key}, 错误: {exc}", self.log_level.ERROR
                )
                progress_manager.complete_task(task_id, success=False, error=str(exc))
            finally:
                with self.local_lock:
                    if self._initializing_tasks.get(key) == task_id:
                        self._initializing_tasks.pop(key, None)

        thread = threading.Thread(target=init_tradebook, daemon=True)
        thread.start()
        return task_id

    def exists(self, symbol: str, date: str) -> bool:
        """检查TradeBook是否在本地缓存中存在"""
        key = self._generate_key(symbol, date)
        with self.local_lock:
            return key in self.local_cache
    
    def remove(self, symbol: str, date: str) -> bool:
        """从本地缓存中删除TradeBook"""
        key = self._generate_key(symbol, date)
        
        try:
            with self.local_lock:
                if key in self.local_cache:
                    del self.local_cache[key]
                    self.logger.n_log(f"从本地缓存删除TradeBook: {key}", self.log_level.INFO)
                    return True
                else:
                    return False
            
        except Exception as e:
            self.logger.n_log(f"删除TradeBook失败: {key}, 错误: {e}", self.log_level.ERROR)
            return False
    
    def get_all_keys(self) -> List[str]:
        """获取本地缓存中所有的key"""
        with self.local_lock:
            return list(self.local_cache.keys())
    
    def cleanup_expired(self) -> int:
        """清理过期的TradeBook"""
        try:
            expired_keys = []
            
            with self.local_lock:
                for key, tradebook in self.local_cache.items():
                    if tradebook.is_expired(self.expiry_hours):
                        expired_keys.append(key)
                
                # 删除过期项
                for key in expired_keys:
                    if key in self.local_cache:
                        del self.local_cache[key]
            
            if expired_keys:
                self.logger.n_log(
                    f"清理过期TradeBook: {len(expired_keys)}个, keys: {expired_keys}", 
                    self.log_level.INFO
                )
            
            return len(expired_keys)
            
        except Exception as e:
            self.logger.n_log(f"清理过期数据失败: {e}", self.log_level.ERROR)
            return 0
    
    def _cleanup_worker(self):
        """清理工作线程"""
        self.logger.n_log(f"启动清理线程，间隔: {self.cleanup_interval}秒", self.log_level.INFO)
        
        while not self._stop_cleanup:
            try:
                time.sleep(self.cleanup_interval)
                if not self._stop_cleanup:
                    cleaned_count = self.cleanup_expired()
                    if cleaned_count > 0:
                        self.logger.n_log(f"定时清理完成，清理了{cleaned_count}个过期TradeBook", self.log_level.INFO)
                        
            except Exception as e:
                self.logger.n_log(f"清理线程异常: {e}", self.log_level.ERROR)
    
    def start_cleanup_thread(self):
        """启动清理线程"""
        if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
            self._stop_cleanup = False
            self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
            self._cleanup_thread.start()
    
    def stop_cleanup_thread(self):
        """停止清理线程"""
        self._stop_cleanup = True
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5)
    
    def clear_all(self):
        """清空本地缓存中的所有数据"""
        with self.local_lock:
            self.local_cache.clear()
            
        self.logger.n_log("清空本地缓存中的所有TradeBook数据", self.log_level.INFO)
    
    def get_cache_stats(self) -> Dict:
        """获取缓存统计信息"""
        with self.local_lock:
            cache_count = len(self.local_cache)
            
            # 计算内存使用情况
            total_snapshots = 0
            total_changes = 0
            
            for tradebook in self.local_cache.values():
                try:
                    total_snapshots += tradebook.get_total_snapshots()
                    total_changes += tradebook.get_total_changes()
                except:
                    pass
        
        return {
            'cache_count': cache_count,
            'total_snapshots': total_snapshots,
            'total_changes': total_changes,
            'avg_snapshots_per_tradebook': total_snapshots / max(cache_count, 1),
            'avg_changes_per_tradebook': total_changes / max(cache_count, 1)
        }
    
    def warmup_cache(self, symbol_date_pairs: List[tuple]):
        """预热缓存 - 预先加载指定的TradeBook"""
        self.logger.n_log(f"开始预热缓存，加载 {len(symbol_date_pairs)} 个TradeBook", self.log_level.INFO)
        
        for symbol, date in symbol_date_pairs:
            try:
                # 触发get方法，如果不存在会自动创建并缓存
                tradebook = self.get(symbol, date)
                if tradebook:
                    self.logger.n_log(f"预热缓存成功: {symbol}_{date}", self.log_level.DEBUG)
                else:
                    self.logger.n_log(f"预热缓存失败: {symbol}_{date}", self.log_level.WARNING)
            except Exception as e:
                self.logger.n_log(f"预热缓存异常: {symbol}_{date}, 错误: {e}", self.log_level.ERROR)
        
        self.logger.n_log("缓存预热完成", self.log_level.INFO)
    
    def __del__(self):
        """析构函数"""
        self.stop_cleanup_thread()


# 全局存储实例
_shared_storage = None

def get_shared_storage() -> SharedTradeBookStorage:
    """
    获取全局存储实例（每个进程一个实例）
    
    Returns:
        SharedTradeBookStorage: 存储实例
    """
    global _shared_storage
    if _shared_storage is None:
        _shared_storage = SharedTradeBookStorage()
    return _shared_storage