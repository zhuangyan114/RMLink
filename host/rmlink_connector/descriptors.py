"""CMSIS-DAP v2 Full-Speed descriptors, including Microsoft OS 2.0 WinUSB.

USB layout follows the selected ESP USB Bridge/RMLink firmware. USB IDs are
prototype IDs inherited from that base, not a production USB ID allocation.
"""
import struct
import uuid

GUID = '{CDB3B5AD-293B-4663-AA36-1AAE46463776}'


class Descriptors:
    def __init__(self, device_id):
        self.device_id = device_id
        self.device = struct.pack('<BBHBBBBHHHBBBB', 18, 1, 0x0210, 0, 0, 0, 64,
                                  0x303a, 0x1002, 0x0100, 1, 2, 3, 1)
        self.config = bytes([9, 2, 32, 0, 1, 1, 0, 0x80, 50,
                             9, 4, 0, 0, 2, 0xff, 0, 0, 4,
                             7, 5, 0x03, 2, 64, 0, 0,
                             7, 5, 0x83, 2, 64, 0, 0])
        name = 'DeviceInterfaceGUIDs\0'.encode('utf-16le')
        value = (GUID + '\0\0').encode('utf-16le')
        prop = struct.pack('<HHHH', 10 + len(name) + len(value), 4, 7, len(name)) + name + struct.pack('<H', len(value)) + value
        self.ms_os = (struct.pack('<HHIH', 10, 0, 0x06030000, 10 + 20 + len(prop)) +
                      struct.pack('<HH8s8s', 20, 3, b'WINUSB\0\0', bytes(8)) + prop)
        platform = (bytes([28, 16, 5, 0]) + uuid.UUID('D8DD60DF-4589-4CC7-9CD2-659D9E648A9F').bytes_le +
                    struct.pack('<IHBB', 0x06030000, len(self.ms_os), 0x20, 0))
        self.bos = struct.pack('<BBHB', 5, 15, 5 + len(platform), 1) + platform
        self.strings = {1: 'RMLink', 2: 'RMLink CMSIS-DAP', 3: 'RM' + device_id.upper(), 4: 'CMSIS-DAP v2'}

    def control(self, setup, out_data=b''):
        kind, request, value, index, length = struct.unpack('<BBHHH', setup)
        if kind == 0xc0 and request == 0x20 and index == 7:
            return self.ms_os[:length]
        if kind == 0x80 and request == 6:  # GET_DESCRIPTOR
            dtype, number = value >> 8, value & 255
            if dtype == 1 and number == 0: data = self.device
            elif dtype == 2 and number == 0: data = self.config
            elif dtype == 15 and number == 0: data = self.bos
            elif dtype == 3:
                if number == 0: data = b'\x04\x03\x09\x04'
                elif number in self.strings and index in (0, 0x0409):
                    text = self.strings[number].encode('utf-16le')
                    data = bytes([len(text) + 2, 3]) + text
                else: raise ValueError('Unsupported USB string')
            else: raise ValueError('Unsupported descriptor')
            return data[:length]
        if kind in (0x80, 0x81, 0x82) and request == 0: return bytes(2)[:length]
        if kind == 0x80 and request == 8: return b'\x01'[:length]
        if kind == 0x81 and request == 10 and index == 0: return b'\x00'[:length]
        if kind == 0 and request == 5 and value <= 127 and length == 0: return b''
        if kind == 0 and request == 9 and value in (0, 1) and length == 0: return b''
        if kind == 1 and request == 11 and value == 0 and index == 0 and length == 0: return b''
        if kind == 2 and request == 1 and value == 0 and index in (3, 0x83) and length == 0: return b''
        raise ValueError('Unsupported USB control request')
