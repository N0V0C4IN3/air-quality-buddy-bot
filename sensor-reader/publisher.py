# sensor-reader/publisher.py
import os
import json
import time
import logging
import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika import exceptions as px

log = logging.getLogger(__name__)

class Publisher:
    def __init__(self):
        self.host = os.environ["RABBITMQ_HOST"]
        self.port = int(os.environ.get("RABBITMQ_PORT", "5672"))
        self.user = os.environ["RABBITMQ_USER"]
        self.pwd  = os.environ["RABBITMQ_PASS"]

        self.exchange = os.environ["AQ_EXCHANGE"]
        self.exchange_type = os.environ.get("AQ_EXCHANGE_TYPE", "topic")

        # Keep heartbeats modest since we’ll let the connection drop between cycles
        self.heartbeat = int(os.environ.get("AMQP_HEARTBEAT", "30"))

        self.conn = None
        self.channel: BlockingChannel | None = None

    # ---------- connection helpers ----------

    def _connect(self):
        """(Re)open connection/channel and ensure exchange exists."""
        creds = pika.PlainCredentials(self.user, self.pwd)
        params = pika.ConnectionParameters(
            host=self.host,
            port=self.port,
            credentials=creds,
            heartbeat=self.heartbeat,
            blocked_connection_timeout=30,
            connection_attempts=3,
            retry_delay=2.0,
        )
        log.info("[Publisher] Connecting to %s:%s …", self.host, self.port)
        self.conn = pika.BlockingConnection(params)
        self.channel = self.conn.channel()
        self.channel.exchange_declare(
            exchange=self.exchange,
            exchange_type=self.exchange_type,
            durable=True,
        )
        log.info("[Publisher] Connected and exchange declared: %s", self.exchange)

    def _ensure_open(self):
        """Connect if not open."""
        if self.conn is None or self.channel is None:
            self._connect()
            return
        if self.conn.is_closed or self.channel.is_closed:
            self._connect()

    def close(self):
        try:
            if self.conn and self.conn.is_open:
                self.conn.close()
        except Exception:
            pass
        finally:
            self.conn = None
            self.channel = None

    # ---------- publishing with one retry ----------

    def publish_alert(self, kind: str, pm25_value: float, pm10_value: float, unit: str, ts: float):
        """
        Try once; on failure reconnect and retry once.
        Raise if it still fails.
        """
        body = {
            "type": kind,
            "pm25_value": pm25_value,
            "pm10_value": pm10_value,
            "unit": unit,
            "ts": ts,
        }
        payload = json.dumps(body)

        def _do_publish():
            rk = f"alerts.{kind}"
            self.channel.basic_publish(
                exchange=self.exchange,
                routing_key=rk,
                body=payload,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,  # persistent
                ),
                mandatory=False,
            )
            log.info("[Publisher] Sent %s: %s", rk, body)

        # First attempt (open if needed)
        self._ensure_open()
        try:
            _do_publish()
            return
        except (px.AMQPConnectionError,
                px.ChannelWrongStateError,
                px.StreamLostError,
                px.ConnectionClosed,
                px.ChannelClosedByBroker,
                px.IncompatibleProtocolError) as e:
            log.warning("[Publisher] Publish failed (%s). Reconnecting…", type(e).__name__)
            # Hard reset and retry once
            self.close()
            # small backoff so broker has time to drop old sockets
            time.sleep(0.5)
            self._connect()
            _do_publish()
