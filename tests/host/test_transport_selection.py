import asyncio
import tempfile
import unittest
from unittest.mock import patch
from rmlink_connector.broker import Broker
from rmlink_connector.discovery import Device
from rmlink_connector.service import Connector
from rmlink_connector.state import State

ID='50787d15a924'
class Probe:
    packet_size=64;native_usb=True
    def __init__(self,*args):self.closed=False
    async def connect(self):pass
    async def close(self):self.closed=True
    async def exchange_many(self,packets):
        return [b'\x80\x09\x00'+bytes.fromhex(ID) if p[:2]==b'\x80\x09' else b'\x03\x00' for p in packets]


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.connector=Connector(State(self.temp.name))
        device=Device(ID,'model','127.0.0.1',3241,'0.2.2');self.connector.devices[ID]=device
        self.connector.state.data['paired'][ID]=dict(device.as_dict(),auto_mount=False)
        self.probe=Probe();broker=Broker(self.probe,ID);await broker.start();self.connector.brokers[ID]=broker
    async def asyncTearDown(self):
        await self.connector.close();self.temp.cleanup()
    async def test_close_leases_before_selecting_and_replace_only_one_broker(self):
        session=await self.connector.route('POST','/v1/sessions',{'device_id':ID})
        with self.assertRaises(ValueError):await self.connector.route('POST','/v1/transport',{'device_id':ID,'transport':'usb'})
        self.assertFalse(self.probe.closed)
        await self.connector.close_session(session['session_id'])
        async def discover(**kwargs):return {}
        with patch('rmlink_connector.service.discover',discover),patch('rmlink_connector.service.UsbTransport',Probe):
            result=await self.connector.route('POST','/v1/transport',{'device_id':ID,'transport':'usb'})
        self.assertTrue(result['connected']);self.assertTrue(self.probe.closed)
        self.assertEqual(len(self.connector.brokers),1)
        self.assertEqual(self.connector.state.data['paired'][ID]['transport'],'usb')
        self.assertFalse(self.connector.switching)
    async def test_unknown_transport_never_closes_probe(self):
        with self.assertRaises(ValueError):await self.connector.route('POST','/v1/transport',{'device_id':ID,'transport':'auto'})
        self.assertFalse(self.probe.closed)
