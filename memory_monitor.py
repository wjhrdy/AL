import psutil
import os
import time
import logging
from memory_profiler import profile
import tracemalloc
from hello import MusicIdentifier, main
import asyncio
import gc

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='memory_usage.log'
)
logger = logging.getLogger(__name__)

class MemoryMonitor:
    def __init__(self, interval=1.0):
        self.interval = interval
        self.running = False
        self.baseline_memory = 0
        tracemalloc.start()

    def get_process_memory(self):
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / 1024 / 1024  # Convert to MB

    def log_memory_usage(self):
        current_memory = self.get_process_memory()
        gc_count = gc.get_count()
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics('lineno')
        
        logger.info(f"Memory Usage: {current_memory:.2f} MB")
        logger.info(f"Memory Change: {current_memory - self.baseline_memory:.2f} MB")
        logger.info(f"GC Count: {gc_count}")
        
        logger.info("Top 10 memory allocations:")
        for stat in top_stats[:10]:
            logger.info(stat)

    async def monitor_memory(self):
        self.baseline_memory = self.get_process_memory()
        logger.info(f"Baseline memory usage: {self.baseline_memory:.2f} MB")
        
        self.running = True
        while self.running:
            self.log_memory_usage()
            await asyncio.sleep(self.interval)

    def stop(self):
        self.running = False
        tracemalloc.stop()

@profile
async def run_with_monitoring():
    monitor = MemoryMonitor(interval=5.0)  # Log every 5 seconds
    try:
        # Start memory monitoring in the background
        monitor_task = asyncio.create_task(monitor.monitor_memory())
        
        # Run the main application
        app_task = asyncio.create_task(main())
        
        # Wait for both tasks
        await asyncio.gather(monitor_task, app_task)
    except Exception as e:
        logger.error(f"Error in application: {str(e)}")
        logger.error(traceback.format_exc())
    finally:
        monitor.stop()

if __name__ == "__main__":
    try:
        asyncio.run(run_with_monitoring())
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    finally:
        print("Cleanup complete") 