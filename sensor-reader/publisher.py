# sensor-reader/publisher.py
"""Blocking-pika adapter for the alert seam.

Knows how to get an `Alert` onto the exchange and nothing about what an alert
means — the payload shape and routing key belong to `common.alerts`.
"""
from __future__ import annotations

import logging
import time

import pika
from pika import exceptions as px
from pika.adapters.blocking_connection import BlockingChannel

from common.alerts import Alert

log = logging.getLogger(__name__)


class Publisher:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        exchange: str,
        exchange_type: str = "topic",
        heartbeat: int = 30,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.pwd = password
        self.exchange = exchange
        self.exchange_type = exchange_type
        # Keep heartbeats modest since we let the connection drop between cycles
        self.heartbeat = heartbeat

        self.conn = None
        self.channel: BlockingChannel | None = None

    @classmethod
    def from_settings(cls, settings) -> "Publisher":
        return cls(
            host=settings.rabbitmq_host,
            port=settings.rabbitmq_port,
            user=settings.rabbitmq_user,
            password=settings.rabbitmq_pass,
            exchange=settings.exchange,
            exchange_type=settings.exchange_type,
            heartbeat=settings.amqp_heartbeat,
        )

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

    def publish(self, alert: Alert) -> None:
        """
        Try once; on failure reconnect and retry once.
        Raise if it still fails.
        """
        body = alert.encode()

        def _do_publish():
            self.channel.basic_publish(
                exchange=self.exchange,
                routing_key=alert.routing_key,
                body=body,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,  # persistent
                ),
                mandatory=False,
            )
            log.info("[Publisher] Sent %s: %s", alert.routing_key, body.decode("utf-8"))

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
