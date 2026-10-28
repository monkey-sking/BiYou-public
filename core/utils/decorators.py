import asyncio
import random
import functools
from core.utils.logger import log

def async_retry(max_retries=3, base_delay=2, max_delay=30, jitter=True, exceptions=(Exception,)):
    """
    Decorator for async functions to retry with exponential backoff.
    
    Args:
        max_retries (int): Maximum number of retries.
        base_delay (int): Initial delay in seconds.
        max_delay (int): Maximum delay between retries.
        jitter (bool): Whether to add random jitter to delay.
        exceptions (tuple): Exceptions to catch and retry on.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        log.error(f"❌ Failed after {max_retries} retries: {func.__name__}")
                        raise last_exception
                    
                    # Calculate delay: base_delay * 2^attempt
                    delay = min(max_delay, base_delay * (2 ** attempt))
                    
                    # Add jitter
                    if jitter:
                        delay = delay * (0.5 + random.random())
                    
                    log.warning(
                        f"⚠️ Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {str(e)}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    
                    await asyncio.sleep(delay)
            
            return None # Should not reach here
        return wrapper
    return decorator
