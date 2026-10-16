"""Independent Cortex-M/FPB state model for startup and reset cleanup."""
import asyncio
import unittest
from types import SimpleNamespace
from rmlink_connector.target import TargetEngine,TargetAccessError


class Core(TargetEngine):
    def __init__(self):
        super().__init__(SimpleNamespace(), 'model')
        self.connected=True
        self.memory={0xe000edf0:0x30000,0xe0002000:0x63,0xe000edfc:0x01000400}
        self.regs={14:0xffffffff,15:0x08000400};self.selected=0;self.writes=[];self.wrong_pc=None
        for i in range(6):self.memory[0xe0002008+4*i]=0
    async def vendor(self,*args):return b''
    async def read_word(self,address):
        return self.regs[self.selected] if address==0xe000edf8 else self.memory.get(address,0)
    async def write_word(self,address,value):
        self.writes.append((address,value));self.memory[address]=value
        if address==0xe000edf4:self.selected=value&31
        if address==0xe000ed0c:
            self.regs[15]=0x08000040
            self.memory[0xe000edf0]=0x30000 if self.memory[0xe000edfc]&1 else 0x10000
        if address==0xe000edf0:
            self.memory[address]=0x10000 | (0x20000 if value&2 else 0)
            if not value&2:
                comp=next(self.memory[0xe0002008+4*i] for i in range(6) if self.memory[0xe0002008+4*i]&1)
                self.regs[15]=self.wrong_pc or ((comp&0x1ffffffc)|(2 if comp>>30==2 else 0))
                self.memory[address]|=0x20000


class StartupStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_catches_vector_and_restores_debug_configuration(self):
        core=Core();result=await core.execute('reset',{})
        self.assertEqual(result['state'],'Halted');self.assertEqual(core.regs[15],0x08000040)
        self.assertEqual(core.memory[0xe000edfc],0x01000400)
    async def test_startup_preserves_other_user_breakpoints_and_thumb_address(self):
        core=Core();core.memory[0xe0002008]=0x48000201;core.breakpoints[0]=0x08000200
        before=dict(core.memory);result=await core.execute('run_to_address',{'address':0x08001003,'reset':True})
        self.assertEqual(result['pc'],0x08001002);self.assertTrue(result['cleanupOk'])
        self.assertEqual(result['ignoredUserSlots'],[0]);self.assertEqual(result['temporarySlot'],1)
        for address in [0xe000edfc,0xe0002000,*range(0xe0002008,0xe0002020,4)]:self.assertEqual(core.memory[address],before[address])
        self.assertEqual(core.breakpoints,{0:0x08000200})
    async def test_matching_user_slot_can_be_shared_when_no_slot_is_free(self):
        core=Core()
        for i in range(6):core.memory[0xe0002008+4*i]=0x48001001+4*i;core.breakpoints[i]=0x08001000+4*i
        result=await core.execute('run_to_address',{'address':0x08001001,'reset':False})
        self.assertTrue(result['sharedUserSlot']);self.assertEqual(result['temporaryBreakpointCount'],0)
        self.assertEqual(len(core.breakpoints),6)
    async def test_wrong_stop_and_exhausted_slots_do_not_lose_user_breakpoints(self):
        core=Core();core.wrong_pc=0x08003000;core.memory[0xe0002008]=0x48000201
        with self.assertRaisesRegex(TargetAccessError,'expected'):
            await core.execute('run_to_address',{'address':0x08001000})
        self.assertEqual(core.memory[0xe0002008],0x48000201)
        self.assertTrue(core.memory[0xe000edf0]&0x20000)
        for i in range(6):core.memory[0xe0002008+4*i]=0x48000201+4*i
        before=list(core.writes)
        with self.assertRaisesRegex(TargetAccessError,'No free'):
            await core.execute('run_to_address',{'address':0x08001000})
        self.assertTrue(all(a==0xe000edf0 for a,_ in core.writes[len(before):]))
    async def test_invalid_address_never_writes_core_or_flash(self):
        for address in [None,True,0x20000000,0x08010000]:
            core=Core()
            with self.assertRaises(ValueError):await core.execute('run_to_address',{'address':address})
            self.assertFalse(core.writes)

    async def test_timeout_restores_fpb_and_keeps_halted(self):
        core=Core();core.memory[0xe0002008]=0x48000201
        original=core.poll_word
        async def poll(address,mask,expected,timeout=1):
            if timeout==3:raise TimeoutError('model: application never reaches entry')
            return await original(address,mask,expected,timeout)
        core.poll_word=poll
        with self.assertRaises(TargetAccessError) as error:
            await core.execute('run_to_address',{'address':0x08001000})
        self.assertEqual(error.exception.code,'StartupTimeout')
        self.assertEqual(core.memory[0xe0002008],0x48000201)
        self.assertEqual(core.memory[0xe000200c],0)
        self.assertTrue(core.memory[0xe000edf0]&0x20000)
