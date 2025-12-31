import queue
from typing import Optional, List, Tuple, Callable, Any, Dict
import threading
from concurrent.futures import Future
import psutil
import os
import time
import asyncio

from ..log_creator import get_file_logger

logger = get_file_logger()

# Dynamic worker configuration for uploads
CPU_UTILIZATION_THRESHOLD = 80.0  # Only scale up if CPU < 80%
CPU_CHECK_INTERVAL = 10  # Check CPU every 10 seconds
SCALE_UP_COOLDOWN = 15  # Wait 15 seconds after scaling up
SCALE_DOWN_COOLDOWN = 5  # Wait 5 seconds after scaling down

MIN_WORKERS = 2     # Minimum workers
MAX_WORKERS = max(2, int((os.cpu_count() or 2) * 0.8))  # 80% of vCPUs

# Scaling state tracking
last_scale_up_time = 0.0
last_scale_down_time = 0.0

executor_lock = threading.Lock()

def get_cpu_utilization() -> float:
    """Get current CPU utilization percentage"""
    return psutil.cpu_percent(interval=1)

class DynamicThreadPool:
    """Custom thread pool that can dynamically scale workers based on CPU utilization"""

    def __init__(self, min_workers: int = 2, max_workers: int = 10):
        self.min_workers = min_workers
        self.max_workers = max_workers
        self.task_queue: queue.Queue[Optional[Tuple[Callable[..., Any], Tuple[Any, ...], Dict[str, Any], Future[Any]]]] = queue.Queue()
        self.workers: List[threading.Thread] = []
        self.lock = threading.Lock()
        self.shutdown_flag = threading.Event()
        self.active_tasks = 0
        self.active_tasks_lock = threading.Lock()

        # Start with minimum workers
        self._scale_to(min_workers)

    def _worker(self):
        """Worker thread that processes tasks from the queue"""
        while not self.shutdown_flag.is_set():
            try:
                # Wait for a task with timeout to allow checking shutdown flag
                task_item = self.task_queue.get(timeout=1)
                if task_item is None:  # Poison pill to stop worker
                    self.task_queue.task_done()
                    break

                func, args, kwargs, future = task_item

                with self.active_tasks_lock:
                    self.active_tasks += 1

                try:
                    result = func(*args, **kwargs)
                    future.set_result(result)
                except Exception as e:
                    future.set_exception(e)
                finally:
                    with self.active_tasks_lock:
                        self.active_tasks -= 1
                    self.task_queue.task_done()

            except queue.Empty:
                continue

    def _scale_to(self, target_workers: int):
        """Scale the thread pool to the target number of workers"""
        with self.lock:
            current_count = len(self.workers)

            if target_workers > current_count:
                # Scale up - add more workers
                for _ in range(target_workers - current_count):
                    worker = threading.Thread(target=self._worker, daemon=True)
                    worker.start()
                    self.workers.append(worker)

            elif target_workers < current_count:
                # Scale down - remove workers
                workers_to_remove = current_count - target_workers
                for _ in range(workers_to_remove):
                    self.task_queue.put(None)  # Poison pill

                # Remove dead workers from list
                self.workers = [w for w in self.workers if w.is_alive()]

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any]:
        """Submit a task to the thread pool"""
        future: Future[Any] = Future()
        self.task_queue.put((func, args, kwargs, future))
        return future

    def get_worker_count(self) -> int:
        """Get the current number of active workers"""
        with self.lock:
            # Clean up dead workers
            self.workers = [w for w in self.workers if w.is_alive()]
            return len(self.workers)

    def get_active_tasks(self) -> int:
        """Get the number of currently executing tasks"""
        with self.active_tasks_lock:
            return self.active_tasks

    def get_queue_size(self) -> int:
        """Get the number of queued tasks"""
        return self.task_queue.qsize()

    def scale_workers(self, target: int):
        """Dynamically scale workers to target count"""
        target = max(self.min_workers, min(self.max_workers, target))
        current = self.get_worker_count()

        if target != current:
            self._scale_to(target)
            return True
        return False

    def shutdown(self, wait: bool = True):
        """Shutdown the thread pool"""
        self.shutdown_flag.set()

        # Send poison pills to all workers
        with self.lock:
            for _ in self.workers:
                self.task_queue.put(None)

        if wait:
            for worker in self.workers:
                worker.join(timeout=5)

executor = DynamicThreadPool(min_workers=MIN_WORKERS, max_workers=MAX_WORKERS)

def calculate_optimal_workers(cpu_util: float, queue_size: int = 0) -> int:
    """
    Calculate optimal number of workers based on CPU utilization and queue size

    Args:
        cpu_util: Current CPU utilization percentage
        queue_size: Number of tasks waiting in queue

    Returns:
        Number of workers to use
    """
    current_workers = executor.get_worker_count()

    if cpu_util >= CPU_UTILIZATION_THRESHOLD:
        # CPU is at or above threshold, scale down aggressively
        cpu_overage = cpu_util - CPU_UTILIZATION_THRESHOLD

        if cpu_overage >= 15:  # CPU > 95%
            # Drastic scale down - remove half the workers above minimum
            workers_above_min = current_workers - MIN_WORKERS
            return max(MIN_WORKERS, current_workers - max(2, workers_above_min // 2))
        elif cpu_overage >= 10:  # CPU 90-95%
            # Aggressive scale down - remove 2 workers
            return max(MIN_WORKERS, current_workers - 2)
        else:  # CPU 80-90%
            # Moderate scale down - remove 1 worker
            return max(MIN_WORKERS, current_workers - 1)

    # CPU is below threshold, we can scale up
    # Scale workers based on how much headroom we have
    headroom = CPU_UTILIZATION_THRESHOLD - cpu_util
    scale_factor = min(1.0, headroom / CPU_UTILIZATION_THRESHOLD)

    # Calculate target workers
    target_workers = int(MIN_WORKERS + (MAX_WORKERS - MIN_WORKERS) * scale_factor)

    # If there are queued tasks and CPU is low, scale up faster
    if queue_size > current_workers and cpu_util < CPU_UTILIZATION_THRESHOLD * 0.5:
        target_workers = min(MAX_WORKERS, current_workers + 2)

    return max(MIN_WORKERS, min(MAX_WORKERS, target_workers))

def adjust_worker_pool():
    """
    Dynamically adjust thread pool size based on current CPU utilization
    Enforces cool down periods to allow new threads to stabilize before further scaling
    """
    global last_scale_up_time, last_scale_down_time

    try:
        cpu_util = get_cpu_utilization()
        queue_size = executor.get_queue_size()
        active_tasks = executor.get_active_tasks()
        current_workers = executor.get_worker_count()
        current_time = time.time()

        optimal_workers = calculate_optimal_workers(cpu_util, queue_size)

        if optimal_workers != current_workers:
            # Determine if this is a scale up or scale down
            is_scale_up = optimal_workers > current_workers
            is_scale_down = optimal_workers < current_workers

            # Check cool down periods
            if is_scale_up:
                time_since_last_scale_up = current_time - last_scale_up_time
                if time_since_last_scale_up < SCALE_UP_COOLDOWN:
                    logger.debug(
                        f"[CPU Monitor] Scale-up blocked (cooldown: {SCALE_UP_COOLDOWN - time_since_last_scale_up:.1f}s remaining). "
                        f"Current: {current_workers}, Target: {optimal_workers}, CPU: {cpu_util:.1f}%"
                    )
                    return

            elif is_scale_down:
                time_since_last_scale_down = current_time - last_scale_down_time
                if time_since_last_scale_down < SCALE_DOWN_COOLDOWN:
                    logger.debug(
                        f"[CPU Monitor] Scale-down blocked (cool down: {SCALE_DOWN_COOLDOWN - time_since_last_scale_down:.1f}s remaining). "
                        f"Current: {current_workers}, Target: {optimal_workers}, CPU: {cpu_util:.1f}%"
                    )
                    return

            # Perform scaling
            with executor_lock:
                success = executor.scale_workers(optimal_workers)
                if success:
                    # Update cooldown timers
                    if is_scale_up:
                        last_scale_up_time = current_time
                    elif is_scale_down:
                        last_scale_down_time = current_time

                    logger.info(
                        f"[CPU Monitor] Scaled executor workers: {current_workers} → {optimal_workers} "
                        f"({'UP' if is_scale_up else 'DOWN'}) - "
                        f"CPU: {cpu_util:.1f}%, Queue: {queue_size}, Active: {active_tasks}"
                    )
        else:
            logger.debug(
                f"[CPU Monitor] Upload workers optimal: {current_workers} "
                f"(CPU: {cpu_util:.1f}%, Queue: {queue_size}, Active: {active_tasks})"
            )

    except Exception as e:
        logger.error(f"[CPU Monitor] Error adjusting workers: {str(e)}")

async def cpu_monitoring_loop():
    """Background task to monitor CPU and adjust workers"""
    logger.info(f"[CPU Monitor] Started monitoring (interval: {CPU_CHECK_INTERVAL}s)")

    while True:
        try:
            await asyncio.sleep(CPU_CHECK_INTERVAL)
            adjust_worker_pool()
        except asyncio.CancelledError:
            logger.info("[CPU Monitor] Monitoring stopped")
            break
        except Exception as e:
            logger.error(f"[CPU Monitor] Error in monitoring loop: {str(e)}")