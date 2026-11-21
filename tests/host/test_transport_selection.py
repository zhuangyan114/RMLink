import asyncio
import tempfile
import unittest
from unittest.mock import patch
from rmlink_connector.broker import Broker
from rmlink_connector.discovery import Device
from rmlink_connector.management import ManagementError
from rmlink_connector.service import Connector
from rmlink_connector.state import State
from rmlink_connector.target import TargetAccessError

ID='50787d15a924'
class Probe:
    packet_size=64;native_usb=True
    def __init__(self,*args):self.closed=False;self.requests=[]
    async def connect(self):pass
    async def close(self):self.closed=True
    async def exchange_many(self,packets):
        self.requests.extend(packets)
        info=b'\x80\x00\x00\x01\x00\x03\x05'+bytes(8)+b'\x40\x00'
        return [b'\x80\x09\x00'+bytes.fromhex(ID) if p[:2]==b'\x80\x09' else info if p==b'\x80\x00' else b'\x03\x00' for p in packets]


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.connector=Connector(State(self.temp.name))
        device=Device(ID,'model','127.0.0.1',3241,'0.2.2');self.connector.devices[ID]=device
        self.connector.state.data['paired'][ID]=dict(device.as_dict())
        self.probe=Probe();self.probe.native_usb=False
        broker=Broker(self.probe,ID);await broker.start();self.connector.brokers[ID]=broker
    async def asyncTearDown(self):
        await self.connector.close();self.temp.cleanup()
    async def test_close_leases_before_selecting_and_replace_only_one_broker(self):
        session=await self.connector.route('POST','/v1/sessions',{'device_id':ID})
        with self.assertRaises(TargetAccessError) as err:
            await self.connector.route('POST','/v1/transport',{'device_id':ID,'transport':'usb'})
        self.assertEqual(err.exception.code,'TargetBusy')
        self.assertFalse(self.probe.closed)
        await self.connector.close_session(session['session_id'])
        async def discover(**kwargs):return {}
        with patch('rmlink_connector.service.discover',discover),patch('rmlink_connector.service.UsbTransport',Probe):
            result=await self.connector.route('POST','/v1/transport',{'device_id':ID,'transport':'usb'})
        self.assertTrue(result['connected']);self.assertTrue(self.probe.closed)
        self.assertEqual(result['preferred_transport'],'usb')
        self.assertEqual(result['active_transport'],'usb')
        self.assertEqual(len(self.connector.brokers),1)
        self.assertEqual(self.connector.state.data['paired'][ID]['transport'],'usb')
        self.assertFalse(self.connector.switching)
        self.assertEqual(self.connector.devices[ID].firmware,'0.3.5')
        self.assertEqual(self.connector.state.data['paired'][ID]['firmware'],'0.3.5')
    async def test_unknown_transport_never_closes_probe(self):
        with self.assertRaises(ManagementError):
            await self.connector.route('POST','/v1/transport',{'device_id':ID,'transport':'bluetooth'})
        self.assertFalse(self.probe.closed)
    async def test_transport_route_reconnects_after_manual_disconnect_and_reports_failed_broker(self):
        await self.connector.route('POST','/v1/disconnect',{'device_id':ID})
        self.assertFalse(self.connector.state.data['paired'][ID]['auto_connect'])
        async def discover(**kwargs):return {}
        with patch('rmlink_connector.service.discover',discover),patch('rmlink_connector.service.UsbTransport',Probe):
            result=await self.connector.route('POST','/v1/transport',{'device_id':ID,'transport':'usb'})
        self.assertTrue(result['connected'])
        self.assertTrue(self.connector.state.data['paired'][ID]['auto_connect'])
        self.connector.brokers[ID].failed='broken'
        self.assertFalse(self.connector.snapshot()['devices'][0]['connected'])
        with patch('rmlink_connector.service.discover',discover):
            result=await self.connector.route('POST','/v1/transport',{'device_id':ID,'transport':'wifi','connect':False})
        self.assertFalse(result['connected'])
        self.assertIsNone(result['active_transport'])
    async def test_switching_usb_to_wifi_enables_board_wifi_and_reads_new_ip(self):
        usb=Probe();usb.native_usb=True
        ip_bytes=bytes([192,168,137,42])
        async def usb_exchange(packets):
            usb.requests.extend(packets)
            out=[]
            for p in packets:
                if p[:2]==b'\x80\x09': out.append(b'\x80\x09\x00'+bytes.fromhex(ID))
                elif p==b'\x80\x00': out.append(b'\x80\x00\x00\x01\x00\x03\x05'+bytes(8)+b'\x40\x00')
                elif p==b'\x80\x12': out.append(b'\x80\x12\x00\x01\x00\x01\x00\x01\x01\x00\x00\x03')
                elif p==b'\x80\x13\x01': out.append(b'\x80\x13\x00')
                elif p==b'\x80\x04': out.append(b'\x80\x04\x00\x01\x01\xc4'+ip_bytes)
                else: out.append(b'\x03\x00')
            return out
        usb.exchange_many=usb_exchange
        await self.connector.brokers[ID].close()
        usb_broker=Broker(usb,ID);await usb_broker.start();self.connector.brokers[ID]=usb_broker
        tcp_targets=[]
        class WifiProbe(Probe):
            native_usb=False
            def __init__(self,host,port):
                super().__init__();tcp_targets.append((host,port))
        async def discover(**kwargs):return {}
        with patch('rmlink_connector.service.discover',discover),patch('rmlink_connector.service.TcpTransport',WifiProbe):
            result=await self.connector.route('POST','/v1/transport',{'device_id':ID,'transport':'wifi'})
        self.assertTrue(result['connected'])
        self.assertEqual(result['active_transport'],'wifi')
        self.assertIn(b'\x80\x13\x01',usb.requests)
        self.assertTrue(usb.closed)
        self.assertEqual(tcp_targets,[('192.168.137.42',3241)])
        self.assertEqual(self.connector.state.data['paired'][ID]['address'],'192.168.137.42')


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_usb_probe_does_not_block_later_paired_probe(self):
        missing='001122334455'
        attempted=[];transports=[]
        class UsbProbe(Probe):
            def __init__(self,serial):
                super().__init__();self.serial=serial;transports.append(self)
            async def connect(self):
                attempted.append(self.serial)
                if self.serial==missing.upper():
                    raise RuntimeError('No available RMLink WinUSB interface')
        async def discover(**kwargs):return {}
        with tempfile.TemporaryDirectory() as directory:
            connector=Connector(State(directory))
            for key in (missing,ID):
                device=Device(key,'model','0.0.0.0',3241,'0.3.4')
                connector.state.data['paired'][key]=dict(device.as_dict(),transport='usb',auto_connect=True)
            try:
                with patch('rmlink_connector.service.discover',discover),patch('rmlink_connector.service.UsbTransport',UsbProbe):
                    await connector.refresh()
                self.assertEqual(attempted,[missing.upper(),ID.upper()])
                self.assertIn('RuntimeError',connector.connection_errors[missing])
                self.assertTrue(transports[0].closed)
                self.assertNotIn(missing,connector.brokers)
                self.assertIn(ID,connector.brokers)
                self.assertNotIn(ID,connector.connection_errors)
            finally:await connector.close()

    async def test_auto_mode_prefers_usb_falls_back_to_wifi_and_upgrades_idle_wifi(self):
        usb_available=False
        class FakeUsb(Probe):
            native_usb=True
            def __init__(self,serial): super().__init__()
            async def connect(self):
                if not usb_available: raise RuntimeError('No available RMLink WinUSB interface')
        class FakeWifi(Probe):
            native_usb=False
            def __init__(self,host,port): super().__init__()
            async def exchange_many(self,packets):
                link=b'\x80\x12\x00\x01\x01\x01\x01'+bytes([1 if usb_available else 0])+b'\x02\x00\x00\x03'
                return [link if p==b'\x80\x12' else (await super().exchange_many([p]))[0] for p in packets]
        async def discover(**kwargs):return {}
        with tempfile.TemporaryDirectory() as directory:
            connector=Connector(State(directory))
            device=Device(ID,'rmlink-a5995c.local','192.168.137.10',3241,'0.3.5')
            connector.state.data['paired'][ID]=dict(device.as_dict(),transport='auto',auto_connect=True)
            try:
                with patch('rmlink_connector.service.discover',discover),patch('rmlink_connector.service.UsbTransport',FakeUsb),patch('rmlink_connector.service.TcpTransport',FakeWifi):
                    await connector.refresh()
                    self.assertEqual(connector.snapshot()['devices'][0]['active_transport'],'wifi')
                    self.assertEqual(connector.snapshot()['devices'][0]['preferred_transport'],'auto')
                    wifi_broker=connector.brokers[ID]
                    # Active session prevents auto-upgrade even when USB appears.
                    usb_available=True
                    session=await connector.route('POST','/v1/sessions',{'device_id':ID})
                    await connector.refresh()
                    self.assertIs(connector.brokers[ID],wifi_broker)
                    await connector.close_session(session['session_id'])
                    # Once idle, refresh upgrades Wi-Fi -> USB make-before-break.
                    await connector.refresh()
                    self.assertIsNot(connector.brokers[ID],wifi_broker)
                    self.assertTrue(wifi_broker.transport.closed)
                    self.assertEqual(connector.snapshot()['devices'][0]['active_transport'],'usb')
            finally:await connector.close()

    async def test_mdns_discovery_persists_updated_ip_address(self):
        async def discover(**kwargs):
            return {ID:Device(ID,'rmlink-a5995c.local','192.168.137.88',3241,'0.3.5')}
        with tempfile.TemporaryDirectory() as directory:
            connector=Connector(State(directory))
            device=Device(ID,'rmlink-a5995c.local','192.168.137.10',3241,'0.3.5')
            connector.state.data['paired'][ID]=dict(device.as_dict(),transport='wifi',auto_connect=False)
            try:
                with patch('rmlink_connector.service.discover',discover):
                    await connector.refresh()
                self.assertEqual(connector.state.data['paired'][ID]['address'],'192.168.137.88')
                self.assertEqual(connector.devices[ID].address,'192.168.137.88')
            finally:await connector.close()
