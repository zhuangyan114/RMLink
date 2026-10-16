"""Per-connection TCP latency options for the physical probe sockets."""
import ctypes
import logging
import socket
import sys


def _set_windows_ack_frequency(sock):
    # Windows SDK mstcpip.h: _WSAIOW(IOC_VENDOR, 23), with a one-UCHAR
    # TCP_ACK_FREQUENCY_PARAMETERS (TcpDelayedAckFrequency = 1).
    ws = ctypes.WinDLL('Ws2_32')
    ws.WSAIoctl.argtypes = [ctypes.c_size_t, ctypes.c_uint32, ctypes.c_void_p,
                           ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                           ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p, ctypes.c_void_p]
    ws.WSAIoctl.restype = ctypes.c_int
    ws.WSAGetLastError.restype = ctypes.c_int
    frequency, returned = ctypes.c_ubyte(1), ctypes.c_uint32()
    if ws.WSAIoctl(sock.fileno(), 0x98000017, ctypes.byref(frequency), 1,
                   None, 0, ctypes.byref(returned), None, None):
        raise OSError(ws.WSAGetLastError(), 'SIO_TCP_SET_ACK_FREQUENCY')


def configure_probe_tcp(sock):
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if sys.platform != 'win32':
        return False
    try:
        _set_windows_ack_frequency(sock)
        return True
    except (OSError, AttributeError) as error:
        # Older Windows/network stacks may not support this optional IOCTL.
        # Keep the valid TCP connection and its normal ACK behavior.
        logging.warning('RMLink immediate TCP ACK unavailable; using OS default: %s', error)
        return False


def probe_tcp_info(sock):
    """Read socket counters for opt-in diagnostics, without changing TCP state."""
    if sys.platform != 'win32':
        return None
    # Windows SDK mstcpip.h TCP_INFO_v0; fixed-width fields and native padding.
    u32, u64 = ctypes.c_uint32, ctypes.c_uint64
    class Info(ctypes.Structure):
        _fields_ = [('state', u32), ('mss', u32), ('connection_ms', u64),
                    ('timestamps', ctypes.c_ubyte), ('rtt_us', u32), ('min_rtt_us', u32),
                    ('in_flight', u32), ('cwnd', u32), ('send_window', u32),
                    ('recv_window', u32), ('recv_buffer', u32), ('bytes_out', u64),
                    ('bytes_in', u64), ('reordered', u32), ('retransmitted', u32),
                    ('fast_retransmit', u32), ('duplicate_acks', u32), ('timeouts', u32),
                    ('syn_retransmit', ctypes.c_ubyte)]
    ws = ctypes.WinDLL('Ws2_32')
    ws.WSAIoctl.argtypes = [ctypes.c_size_t, u32, ctypes.c_void_p, u32,
                           ctypes.c_void_p, u32, ctypes.POINTER(u32), ctypes.c_void_p, ctypes.c_void_p]
    ws.WSAIoctl.restype = ctypes.c_int
    version, returned, info = u32(0), u32(), Info()
    if ws.WSAIoctl(sock.fileno(), 0xd8000027, ctypes.byref(version), 4,
                   ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned), None, None):
        return {'unavailable': ws.WSAGetLastError()}
    if returned.value != ctypes.sizeof(info):
        return {'unexpected_bytes': returned.value}
    return {name: getattr(info, name) for name, _ in info._fields_}
