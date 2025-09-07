import logging
import time
import redis.exceptions
from socketio import RedisManager

logger = logging.getLogger(__name__)


class ResilientRedisManager(RedisManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.redis_available = True

    def _listen(self):
        """Override to handle Redis connection failures gracefully"""
        while True:
            try:
                if not self.redis_available:
                    logger.warning("Redis unavailable - sleeping before retry")
                    time.sleep(30)  # Wait 30 seconds before retrying

                # Try to use Redis normally
                for message in super()._listen():
                    yield message

                self.redis_available = True

            except redis.exceptions.ConnectionError:
                if self.redis_available:
                    logger.error("Redis connection lost - entering fallback mode")
                    self.redis_available = False
                time.sleep(5)  # Short sleep before retry

            except Exception as e:
                logger.error(f"Unexpected error in Redis listener: {e}")
                time.sleep(10)

    def _publish(self, data):
        """Override to handle publish failures"""
        if not self.redis_available:
            logger.debug("Not publishing - Redis unavailable")
            return

        try:
            super()._publish(data)
        except redis.exceptions.ConnectionError:
            if self.redis_available:
                logger.error("Redis publish failed - marking as unavailable")
                self.redis_available = False
        except Exception as e:
            logger.error(f"Unexpected publish error: {e}")