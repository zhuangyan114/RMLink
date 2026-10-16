import socket
import sys
import unittest
from unittest.mock import patch
from rmlink_connector.tcp_options import configure_probe_tcp, probe_tcp_info


class TcpOptionsTests(unittest.TestCase):
    def sockets(self):
        listener=socket.socket();self.addCleanup(listener.close)
        listener.bind(('127.0.0.1',0));listener.listen()
        sender=socket.create_connection(listener.getsockname());self.addCleanup(sender.close)
        receiver,_=listener.accept();self.addCleanup(receiver.close)
        return sender,receiver

    @unittest.skipUnless(sys.platform=='win32','Windows per-socket ACK IOCTL')
    def test_windows_ioctl_accepts_live_socket_and_keeps_data_intact(self):
        sender,receiver=self.sockets()
        self.assertTrue(configure_probe_tcp(receiver))
        sender.sendall(b'RMLink TCP data')
        self.assertEqual(receiver.recv(64),b'RMLink TCP data')

    def test_unsupported_windows_option_preserves_the_connection(self):
        sender,receiver=self.sockets()
        with patch('rmlink_connector.tcp_options.sys.platform','win32'), patch(
            'rmlink_connector.tcp_options._set_windows_ack_frequency',side_effect=OSError(10045,'not supported')):
            with self.assertLogs(level='WARNING'): self.assertFalse(configure_probe_tcp(receiver))
        self.assertEqual(receiver.getsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY),1)
        sender.sendall(b'fallback')
        self.assertEqual(receiver.recv(64),b'fallback')

    @unittest.skipUnless(sys.platform=='win32','Windows per-socket TCP counters')
    def test_windows_tcp_counters_track_received_data_without_consuming_it(self):
        sender,receiver=self.sockets()
        sender.sendall(b'counter-check')
        counters=probe_tcp_info(receiver)
        self.assertIn('recv_window',counters)
        self.assertGreater(counters['recv_window'],0)
        self.assertEqual(receiver.recv(64),b'counter-check')
        self.assertGreaterEqual(probe_tcp_info(receiver)['bytes_in'],13)
