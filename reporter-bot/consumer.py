# reporter-bot/consumer.py
import os, json, asyncio, logging, aio_pika
import logging
log = logging.getLogger("reporter_bot.consumer")

def require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v

class AsyncConsumer:
    def __init__(self, url, exchange, queue, routing_keys, exchange_type=aio_pika.ExchangeType.TOPIC):
        self.url = url
        self.exchange_name = exchange
        self.queue_name = queue
        self.routing_keys = routing_keys
        self.exchange_type = exchange_type
        self._stopping = asyncio.Event()

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
        prefetch = int(os.getenv("AMQP_PREFETCH", "10"))
        retry_delay = float(os.getenv("AMQP_RETRY_DELAY", "3"))

        while not self._stopping.is_set():
            try:
                log.info("AMQP connecting (robust) to %s", self.url)
                conn: aio_pika.RobustConnection = await aio_pika.connect_robust(self.url)
                async with conn:
                    ch: aio_pika.RobustChannel = await conn.channel()
                    await ch.set_qos(prefetch_count=prefetch)
                    log.info("QoS set prefetch_count=%s", prefetch)

                    ex: aio_pika.RobustExchange = await ch.declare_exchange(
                        self.exchange_name, self.exchange_type, durable=True
                    )
                    q: aio_pika.RobustQueue = await ch.declare_queue(
                        self.queue_name, durable=True
                    )

                    # robust bindings (will be restored after reconnect)
                    for rk in self.routing_keys:
                        await q.bind(ex, rk)
                        log.info("Bound queue=%s to exchange=%s rk=%s", self.queue_name, self.exchange_name, rk)

                    # ask broker for consumer count (optional)
                    q_state = await ch.declare_queue(self.queue_name, passive=True)
                    log.info("Broker reports queue=%s consumer_count=%s", self.queue_name, getattr(q_state, "consumer_count", "n/a"))

                    async def _on_message(msg: aio_pika.IncomingMessage):
                        log.info("Delivery: rk=%s size=%d ctype=%s", msg.routing_key, len(msg.body), msg.content_type)
                        async with msg.process(requeue=False):  # auto-ack on success, nack on exception
                            payload = json.loads(msg.body.decode("utf-8"))
                            await handler(payload)

                    # IMPORTANT: consume callback, not iterator. Robust will re-subscribe after reconnects.
                    consumer_tag = await q.consume(_on_message, no_ack=False)
                    log.info("Started consuming queue=%s consumer_tag=%s", self.queue_name, consumer_tag)

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
                log.exception("AMQP loop error: %s; retrying in %.1fs", type(e).__name__, retry_delay)
                await asyncio.sleep(retry_delay)