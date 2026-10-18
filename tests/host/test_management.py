"""Management must stay outside target ownership and preserve paired state."""
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from rmlink_connector.broker import Broker
from rmlink_connector.discovery import Device
from rmlink_connector.management import ManagementError, provision
from rmlink_connector.service import Connector
from rmlink_connector.state import State
from rmlink_connector.target import TargetAccessError
from rmlink_connector.telemetry import Traffic, Rates
from test_connector import FakeTransport, ID


class ProvisionTests(unittest.TestCase):
    def test_utf8_limits_validate_both_fields_before_any_write(self):
        for ssid, password in [('网' * 11, 'abcdefgh'), ('network', 'short'), ('net\0work', 'abcdefgh')]:
            sent = []
            with self.assertRaises(ManagementError):
                provision(lambda p: sent.append(p), ssid, password)
            self.assertEqual(sent, [])

    def test_open_network_still_sends_an_explicit_empty_password_field(self):
        sent = []
        result = provision(lambda p: sent.append(p) or bytes([0x80, p[1], 0]), 'network', '')
        self.assertTrue(result['saved'])
        self.assertIn(b'\x80\x02\x01\x00\x00\x00', sent)
        self.assertEqual(sent[-1], b'\x80\x03')

    def test_rejected_fragment_never_commits_or_retries(self):
        sent = []
        with self.assertRaises(ConnectionError):
            provision(lambda p: sent.append(p) or b'\x80\x02\x01', 'network', 'abcdefgh')
        self.assertEqual(len(sent), 1)


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.connector = Connector(State(self.temp.name))
        self.device = Device(ID, 'rmlink-test.local', '127.0.0.1', 3241, '0.2.40')
        self.connector.devices[ID] = self.device
        self.connector.state.data['paired'][ID] = dict(self.device.as_dict(), alias='工作台', auto_mount=False)
        self.transport = FakeTransport()
        self.transport.traffic = Traffic()
        self.broker = Broker(self.transport, ID)
        await self.broker.start()
        self.connector.brokers[ID] = self.broker

    async def asyncTearDown(self):
        await self.connector.close()
        self.temp.cleanup()

    async def test_metrics_are_cached_and_never_query_or_claim_target(self):
        await self.broker.submit('debugger', [b'\x02\x01'])
        before = list(self.transport.requests)
        self.transport.traffic.rx_bytes = 1234
        result = await self.connector.route('GET', f'/v1/devices/{ID}/metrics', {})
        self.assertEqual(result['traffic']['rx_bytes'], 1234)
        self.assertEqual(self.transport.requests, before)
        self.assertEqual(self.broker.owner, 'debugger')

    async def test_active_target_and_even_unused_leases_block_disruptive_management(self):
        lease = await self.connector.route('POST', '/v1/sessions', {'device_id': ID})
        for path in ('/v1/disconnect', '/v1/unpair', f'/v1/devices/{ID}/wifi'):
            with self.assertRaises(TargetAccessError) as error:
                await self.connector.route('POST', path, dict(device_id=ID, ssid='network', password='abcdefgh'))
            self.assertEqual(error.exception.code, 'TargetBusy')
        self.assertEqual(self.transport.requests, [])
        self.assertIn(lease['session_id'], self.connector.sessions)

    async def test_disconnect_disables_reconnect_without_forgetting_pairing(self):
        await self.connector.route('POST', '/v1/disconnect', {'device_id': ID})
        self.assertFalse(self.connector.state.data['paired'][ID]['auto_connect'])
        self.assertTrue(self.transport.closed)
        self.assertNotIn(ID, self.connector.brokers)
        with patch('rmlink_connector.service.discover', AsyncMock(return_value={})), patch(
                'rmlink_connector.service.TcpTransport', side_effect=AssertionError('Reconnected manually disconnected device')):
            await self.connector.refresh()

    async def test_pair_again_preserves_alias_mount_and_transport_preferences(self):
        self.connector.state.data['paired'][ID].update(auto_connect=False, transport='usb')
        with patch.object(self.connector, 'refresh', AsyncMock()):
            await self.connector.route('POST', '/v1/pair', {'device_id': ID})
        saved = self.connector.state.data['paired'][ID]
        self.assertEqual(saved['alias'], '工作台')
        self.assertFalse(saved['auto_mount'])
        self.assertEqual(saved['transport'], 'usb')

    async def test_unpair_removes_default_but_preserves_other_preferences_and_token(self):
        token = self.connector.state.data['token']
        await self.connector.route('POST', '/v1/preferences', {'default_device': ID})
        await self.connector.route('POST', '/v1/unpair', {'device_id': ID})
        self.assertNotIn(ID, self.connector.state.data['paired'])
        self.assertEqual(self.connector.state.data['preferences'], {})
        self.assertEqual(self.connector.state.data['token'], token)

    async def test_alias_cannot_inject_terminal_controls_or_a_token_setting(self):
        with self.assertRaises(ManagementError):
            await self.connector.route('POST', '/v1/alias', {'device_id': ID, 'alias': '\x1b[2J'})
        with self.assertRaises(ManagementError):
            await self.connector.route('POST', '/v1/preferences', {'token': 'changed'})

    async def test_firmware_capability_does_not_send_target_flash_commands(self):
        result = await self.connector.route('GET', f'/v1/devices/{ID}/firmware', {})
        self.assertFalse(result['supported'])
        self.assertEqual(self.transport.requests, [])

    async def test_wifi_broker_path_checks_fragments_before_commit_and_cannot_be_used_by_api_owner(self):
        self.transport.native_usb = True
        accepted = []
        async def exchange(packets):
            accepted.extend(packets)
            return [b'\x80\x00\x00\x01' + bytes(11) + b'\x40\x00' if p == b'\x80\x00' else
                    b'\x80\x02\x01' if p[:2] == b'\x80\x02' else b'\x80\x03\x00' for p in packets]
        self.transport.exchange_many = exchange
        with self.assertRaises(ConnectionError):
            await self.connector.route('POST', f'/v1/devices/{ID}/wifi', {'ssid': 'network', 'password': 'abcdefgh'})
        self.assertNotIn(b'\x80\x03', accepted)
        self.assertIsNone(self.broker.owner)
        before = len(accepted)
        reply = await self.broker.submit('api-arbitrary', [b'\x80\x03'])
        self.assertEqual(reply, [b'\x80\xff'])
        self.assertEqual(len(accepted), before)


class RateTests(unittest.TestCase):
    def test_rates_use_real_time_and_reset_on_reconnect_instead_of_showing_a_spike(self):
        rates = Rates()
        self.assertIsNone(rates.update(ID, dict(generation='a', rx_bytes=100, tx_bytes=50), 10)['rx_bps'])
        self.assertEqual(rates.update(ID, dict(generation='a', rx_bytes=1124, tx_bytes=562), 12),
                         dict(rx_bps=512, tx_bps=256))
        self.assertIsNone(rates.update(ID, dict(generation='b', rx_bytes=0, tx_bytes=0), 13)['rx_bps'])
