import logging
import redis
from typing import Optional, Any, Set

# Configure logger for this module
logger = logging.getLogger(__name__)

SUBSCRIBERS_SET_KEY = "subscribers"

class RedisHelper:
    def __init__(self, host='', port=6379, db=0, password: Optional[str] = None):
        self.client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True  # decode bytes to str
        )
        logger.info(f"Connected to Redis at {host}:{port}, db={db}")

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        logger.debug(f"Setting key '{key}' with value '{value}' and expiry={ex}")
        result = self.client.set(name=key, value=value, ex=ex)
        logger.debug(f"Set result for key '{key}': {result}")
        return result

    def get(self, key: str) -> Optional[str]:
        value = self.client.get(name=key)
        logger.debug(f"Retrieved key '{key}': {value}")
        return value

    def pop(self, key: str) -> Optional[str]:
        logger.debug(f"Popping key '{key}'")
        pipeline = self.client.pipeline()
        pipeline.get(key)
        pipeline.delete(key)
        result = pipeline.execute()
        logger.debug(f"Pop result for key '{key}': {result}")
        return result[0]

    def delete(self, key: str) -> int:
        logger.debug(f"Deleting key '{key}'")
        result = self.client.delete(key)
        logger.debug(f"Deleted key '{key}', result: {result}")
        return result

    def exists(self, key: str) -> bool:
        result = self.client.exists(key) == 1
        logger.debug(f"Exists check for key '{key}': {result}")
        return result

    def get_subscribers(self) -> Set[int]:
        raw = self.client.smembers(SUBSCRIBERS_SET_KEY)
        subscribers = {int(cid) for cid in raw}
        logger.debug(f"Current subscribers: {subscribers}")
        return subscribers

    def add_subscriber(self, chat_id: int):
        result = self.client.sadd(SUBSCRIBERS_SET_KEY, chat_id)
        logger.info(f"Added subscriber {chat_id}. Result: {result}")

    def remove_subscriber(self, chat_id: int):
        result = self.client.srem(SUBSCRIBERS_SET_KEY, chat_id)
        logger.info(f"Removed subscriber {chat_id}. Result: {result}")

    def is_subscribed(self, chat_id: int) -> bool:
        result = self.client.sismember(SUBSCRIBERS_SET_KEY, chat_id)
        logger.debug(f"Checked if {chat_id} is subscribed: {result}")
        return result

    def preload_subscribers(self, ids: list[int]):
        if ids:
            result = self.client.sadd(SUBSCRIBERS_SET_KEY, *ids)
            logger.info(f"Preloaded subscribers {ids}. Result: {result}")
