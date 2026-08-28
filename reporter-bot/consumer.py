# reporter-bot/consumer.py
"""aio_pika adapter for the alert seam. Delivers decoded `Alert`s to a handler
and knows nothing about their contents.
"""
from __future__ import annotations

import asyncio
import logging

import aio_pika

from common.alerts import Alert, AlertDecodeError

log = logging.getLogger("reporter_bot.consumer")


class AsyncConsumer:
    def __init__(self, url, exchange, queue, routing_keys,
                 exchange_type=aio_pika.ExchangeType.TOPIC,
                 prefetch: int = 10, retry_delay: float = 3.0):
        self.url = url
        self.exchange_name = exchange
        self.queue_name = queue
        self.routing_keys = routing_keys
        self.exchange_type = exchange_type
        self.prefetch = prefetch
        self.retry_delay = retry_delay
        self._stopping = asyncio.Event()

    @classmethod
    def from_settings(cls, settings) -> "AsyncConsumer":
        return cls(
            url=settings.amqp_url,
            exchange=settings.exchange,
            queue=settings.queue,
            routing_keys=settings.routing_keys,
            prefetch=settings.amqp_prefetch,
            retry_delay=settings.amqp_retry_delay,
        )

    async def stop(self):
        self._stopping.set()

    async def start(self, handler):
        """
        Robust consume loop:
          - Connect (robust)
          - Declare exchange/queue/bindings (robust objects)
          - Use queue.consume(callback) instead of iterator()
          - On any exception, sleep a bit and reconnect
        """
        while not self._stopping.is_set():
            try:
                log.info("AMQP connecting (robust) to %s", self.url)
                conn: aio_pika.RobustConnection = await aio_pika.connect_robust(self.url)
                async with conn:
                    ch: aio_pika.RobustChannel = await conn.channel()
                    await ch.set_qos(prefetch_count=self.prefetch)
                    log.info("QoS set prefetch_count=%s", self.prefetch)

                    ex: aio_pika.RobustExchange = await ch.declare_exchange(
                        self.exchange_name, self.exchange_type, durable=True
                    )
                    q: aio_pika.RobustQueue = await ch.declare_queue(
                        self.queue_name, durable=True
                    )

                    # robust bindings (will be restored after reconnect)
                    for rk in self.routing_keys:
                        await q.bind(ex, rk)
                        log.info("Bound queue=%s to exchange=%s rk=%s",
                                 self.queue_name, self.exchange_name, rk)

                    async def _on_message(msg: aio_pika.IncomingMessage):
                        log.info("Delivery: rk=%s size=%d ctype=%s",
                                 msg.routing_key, len(msg.body), msg.content_type)
                        async with msg.process(requeue=False):  # ack on success, nack on raise
                            try:
                                alert = Alert.decode(msg.body)
                            except AlertDecodeError:
                                log.exception("Dropping malformed alert rk=%s", msg.routing_key)
                                return
                            await handler(alert)

                    # IMPORTANT: consume callback, not iterator. Robust re-subscribes.
                    consumer_tag = await q.consume(_on_message, no_ack=False)
                    log.info("Started consuming queue=%s consumer_tag=%s",
                             self.queue_name, consumer_tag)

                    # park here until stop or connection closes
                    await self._stopping.wait()

                    # graceful stop: cancel consumer and exit
                    try:
                        await q.cancel(consumer_tag)
                    except Exception:
                        pass
                    log.info("Consumer cancelled, shutting down")

            except asyncio.CancelledError:
                log.info("AMQP consumer task cancelled")
                break
            except Exception as e:
                log.exception("AMQP loop error: %s; retrying in %.1fs",
                              type(e).__name__, self.retry_delay)
                await asyncio.sleep(self.retry_delay)
