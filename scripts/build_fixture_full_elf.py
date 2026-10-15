"""Keep fixture DWARF while presenting its canonical dense 64KiB as one PT_LOAD.

Only changes program headers of this test artifact. Existing section/symbol/debug
metadata retains its addresses; the original ELF is never modified.
"""
import hashlib
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[1]


def main():
    directory=ROOT/'artifacts/build/stm32-fixture'
    data=bytearray((directory/'fixture.elf').read_bytes())
    image=(directory/'fixture-64k.bin').read_bytes()
    assert len(image)==65536 and hashlib.sha256(image).hexdigest()=='2a65c721fa1be9c4fcddba2a32bb96ccaeece439b517adb167f00ea928c031a4'
    assert data[:7]==b'\x7fELF\x01\x01\x01' and struct.unpack_from('<H',data,18)[0]==40
    phoff=struct.unpack_from('<I',data,28)[0]
    size,count=struct.unpack_from('<HH',data,42);assert size==32 and count>0 and phoff+size*count<=len(data)
    offset=(len(data)+4095)&~4095;data.extend(bytes(offset-len(data)));data.extend(image)
    first=None
    for i in range(count):
        at=phoff+i*size;kind=struct.unpack_from('<I',data,at)[0]
        if kind!=1:continue
        if first is None:
            first=at;struct.pack_into('<8I',data,at,1,offset,0x08000000,0x08000000,len(image),len(image),5,4096)
        else:struct.pack_into('<I',data,at,0)
    assert first is not None
    output=directory/'fixture-full-image.elf'
    if output.exists() and output.read_bytes()!=data:raise ValueError('Preserve the existing fixture ELF artifact')
    output.write_bytes(data)
    print(output,hashlib.sha256(data).hexdigest())


if __name__=='__main__':main()
