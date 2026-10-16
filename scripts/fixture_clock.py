"""Reject accidental HSI fallback in the 72 MHz F103 performance baseline."""
import struct


def require_fixture_72mhz(client):
    raw=bytes.fromhex(client.target('read_memory',address=0x40021000,byte_length=12)['data'])
    cr,cfgr,cir=struct.unpack('<3I',raw)
    if cr&0x03030003!=0x03030003 or cfgr&0x007fffff!=0x001d840a:
        raise AssertionError(f'72 MHz fixture clock required: RCC_CR={cr:08x}, CFGR={cfgr:08x}')
    return dict(systemClockHz=72000000,hclkHz=72000000,pclk1Hz=36000000,pclk2Hz=72000000,
        adcClockHz=12000000,hseHz=8000000,rccCr=cr,rccCfgr=cfgr,rccCir=cir,
        evidence='RCC register decode with independently identified 8 MHz HSE; not an external frequency counter')


def discard_pretest_rtt(client):
    """Fixture setup only: discard queued bytes from before the measurement.

    Public subscription APIs never do this implicitly. A failed offset write
    aborts setup; it is never automatically repeated.
    """
    raw=bytes.fromhex(client.target('read_memory',address=0x20000400,byte_length=48)['data'])
    if raw[:10]!=b'SEGGER RTT':raise AssertionError('Fixture RTT signature')
    base,size,wr,rd=struct.unpack_from('<4I',raw,28)
    if base!=0x20000600 or size!=1024 or not 0<=wr<size or not 0<=rd<size:
        raise AssertionError('Fixture RTT descriptor')
    client.target('write_memory',address=0x20000428,data=struct.pack('<I',wr).hex())
    return dict(discardedPretestBytes=(wr-rd)%size,offset=wr,scope='fixture setup before subscriptions, outside measurement')
