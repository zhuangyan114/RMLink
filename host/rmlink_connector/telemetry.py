"""Passive application-byte counters; reading these never touches the target."""
import time
import uuid


class Traffic:
    def __init__(self):
        self.generation = uuid.uuid4().hex
        self.started = time.monotonic()
        self.tx_bytes = 0
        self.rx_bytes = 0
        self.last_batch_ms = None

    def snapshot(self):
        return dict(generation=self.generation, elapsed_s=time.monotonic() - self.started,
                    tx_bytes=self.tx_bytes, rx_bytes=self.rx_bytes,
                    last_batch_ms=self.last_batch_ms,
                    scope='probe application bytes, including framing and USB OUT padding; excluding OS/network overhead')


class Rates:
    """Use counter generations and monotonic time, including across reconnects."""
    def __init__(self):
        self.previous = {}

    def update(self, device_id, metrics, now=None):
        now = time.monotonic() if now is None else now
        previous = self.previous.get(device_id)
        self.previous[device_id] = (now, dict(metrics))
        if not previous or previous[1].get('generation') != metrics.get('generation'):
            return dict(rx_bps=None, tx_bps=None)
        elapsed = now - previous[0]
        if elapsed <= 0:
            return dict(rx_bps=None, tx_bps=None)
        return {name + '_bps': max(0, metrics[name + '_bytes'] - previous[1][name + '_bytes']) / elapsed
                for name in ('rx', 'tx')}
