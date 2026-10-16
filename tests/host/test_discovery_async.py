import asyncio
import socket
import unittest
from unittest.mock import AsyncMock,patch
from rmlink_connector.discovery import discover


class DiscoveryAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_hostname_resolution_uses_async_loop_resolver(self):
        loop=asyncio.get_running_loop();resolver=AsyncMock(return_value=[])
        with patch.object(loop,'getaddrinfo',resolver),patch('socket.getaddrinfo',side_effect=AssertionError('Blocking resolver on service loop')):
            self.assertEqual(await discover(seconds=0),{})
        resolver.assert_awaited_once_with(socket.gethostname(),0,family=socket.AF_INET,type=socket.SOCK_DGRAM)
