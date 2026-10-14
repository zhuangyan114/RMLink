"""Independent TCP framing peer: fragmentation, bounded headers, disconnect handling."""
import socket
import struct
import threading
import unittest
from rmlink_tool import TcpClient


class TcpClientTests(unittest.TestCase):
    def peer(self, reply, requests=1):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen()
        errors = []
        def serve():
            try:
                with listener, listener.accept()[0] as conn:
                    for _ in range(requests):
                        header = b''
                        while len(header) < 8: header += conn.recv(8 - len(header))
                        self.assertEqual(header, b'DAP\0\x02\x00\x01\x00')
                        request = b''
                        while len(request) < 2: request += conn.recv(2 - len(request))
                        self.assertEqual(request, b'\x00\xff')
                        for byte in reply: conn.sendall(bytes([byte]))
            except BaseException as error: errors.append(error)
        thread = threading.Thread(target=serve)
        thread.start()
        return listener.getsockname()[1], thread, errors

    def test_fragmented_and_repeated(self):
        port, thread, errors = self.peer(b'DAP\0\x04\x00\x02\x00\x00\x02\x40\x00', 2)
        with TcpClient('127.0.0.1', port, negotiate=False) as client:
            for _ in range(2): self.assertEqual(client.exchange(b'\x00\xff'), b'\x00\x02\x40\x00')
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])

    def test_invalid_oversize_response(self):
        port, thread, errors = self.peer(b'DAP\0\x41\x00\x02\x00')
        with TcpClient('127.0.0.1', port, negotiate=False) as client:
            with self.assertRaises(ValueError): client.exchange(b'\x00\xff')
        thread.join(timeout=3); self.assertEqual(errors, [])

    def test_disconnect_in_header(self):
        port, thread, errors = self.peer(b'DAP\0')
        with TcpClient('127.0.0.1', port, negotiate=False) as client:
            with self.assertRaises(ConnectionError): client.exchange(b'\x00\xff')
        thread.join(timeout=3); self.assertEqual(errors, [])

    def test_no_oversize_request(self):
        client = TcpClient.__new__(TcpClient)
        with self.assertRaises(ValueError): client.exchange(bytes(65))

    def test_pipeline_fragmented_responses(self):
        port, thread, errors = self.peer(b'DAP\0\x04\x00\x02\x00\x00\x02\x40\x00', 2)
        with TcpClient('127.0.0.1', port, negotiate=False) as client:
            self.assertEqual(client.exchange_many([b'\x00\xff', b'\x00\xff']), [b'\x00\x02\x40\x00'] * 2)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])

    def test_invalid_later_packet_not_partially_sent(self):
        client = TcpClient.__new__(TcpClient)
        with self.assertRaises(ValueError): client.exchange_many([b'\x00\xff', bytes(65)])
        with self.assertRaises(ValueError): client.exchange_many([b'\x00\xff'] * 9)

    def test_abort_never_waits_for_response(self):
        class CaptureSocket:
            def __init__(self): self.sent = []
            def sendall(self, data): self.sent.append(data)
        client = TcpClient.__new__(TcpClient)
        client.sock = CaptureSocket()
        client.abort_transfer()
        self.assertEqual(client.sock.sent, [b'DAP\0\x01\x00\x01\x00\x07'])
        with self.assertRaises(ValueError): client.exchange(b'\x07')
        with self.assertRaises(ValueError): client.exchange_many([b'\x00\xff', b'\x07'])
        self.assertEqual(len(client.sock.sent), 1)


if __name__ == '__main__': unittest.main()
