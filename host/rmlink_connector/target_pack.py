"""Shared, source-built target/algorithm package used by Orbit and the probe.

Addresses are bytes, capacities are bytes unless a field explicitly says KiB.
No target identity or writable range is inferred from a neighbouring density.
"""
import hashlib
import json
from pathlib import Path
import sys


def pack_path():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'rmlink_algorithms' / 'targets.json'
    return Path(__file__).resolve().parents[1] / 'algorithms' / 'targets.json'


PACK = json.loads(pack_path().read_text(encoding='utf-8'))


def validate_pack(pack):
    if pack.get('schemaVersion') != 1:
        raise ValueError('Unsupported target package schema')
    names, ids = set(), set()
    for target in pack['targets']:
        if type(target['profileId']) is not int or not 1 <= target['profileId'] <= 255 or target['profileId'] in ids:
            raise ValueError('Duplicate/invalid target profile ID')
        ids.add(target['profileId'])
        if not 1<=len(target['ramRegions'])<=255 or not 1<=len(target['sectors'])<=255 or len(target['optionRegisters'])>4:
            raise ValueError('Target exceeds the firmware descriptor bounds')
        for name in [target['name'], *target['aliases']]:
            if name != name.strip().upper() or name in names:
                raise ValueError('Duplicate/invalid target name')
            names.add(name)
        address = target['flashBase']
        for index, sector in enumerate(target['sectors']):
            if sector['number'] != index or sector['address'] != address or sector['size'] <= 0:
                raise ValueError('Flash sectors must exactly tile the declared window')
            address += sector['size']
        if address != target['flashBase'] + target['flashSize'] or address > 2**32:
            raise ValueError('Flash sector/capacity mismatch')
        if target['preflight']['expectedFlashSizeKiB'] * 1024 != target['flashSize']:
            raise ValueError('Flash identity capacity mismatch')
        algorithm = pack['algorithms'][target['algorithm']]
        if not {'init','uninit','eraseSector','programPage','bkpt'} <= algorithm['entries'].keys():
            raise ValueError('Required algorithm entry is missing')
        page = algorithm['pageSize']
        if not 0 < page <= 65536 or page % 4 or any(s['size'] % page for s in target['sectors']):
            raise ValueError('Invalid algorithm program page size')
        ram = sorted(target['ramRegions'], key=lambda r: r['address'])
        if not ram or any(r['size'] <= 0 or r['address'] < 0 or r['address'] + r['size'] > 2**32 for r in ram):
            raise ValueError('Invalid RAM window')
        if any(a['address'] + a['size'] > b['address'] for a, b in zip(ram, ram[1:])):
            raise ValueError('Overlapping RAM windows')
        loader = target['loader']; region = target['ramRegions'][target['loaderRamIndex']]
        if not (region['address'] == loader['address'] < loader['buffer']
                and loader['buffer'] + page <= loader['stack'] - loader['stackSize']
                and loader['stack'] <= region['address'] + region['size'] and loader['stack'] % 8 == 0):
            raise ValueError('Algorithm/buffer/stack RAM layout overlaps or leaves its window')
        if algorithm.get('verifyReturn') != 'zero':
            raise ValueError('Convert external algorithms to the shared zero-status ABI before adding them')
    return pack


validate_pack(PACK)
PACK_TAG = int.from_bytes(hashlib.sha256(json.dumps(PACK, sort_keys=True, separators=(',', ':')).encode()).digest()[:4], 'little')


def resolve_target(name=None):
    normalized = (name or 'STM32F103C8T6').strip().upper()
    for target in PACK['targets']:
        if normalized in (target['name'], *target['aliases']):
            return target
    raise ValueError('Target is not registered in the shared algorithm package: ' + normalized)


def contains(target, address, size, ram_only=False):
    if type(address) is not int or type(size) is not int or address < 0 or size <= 0 or address + size > 2**32:
        return False
    regions = target['ramRegions'] if ram_only else [*target['ramRegions'], {'address': target['flashBase'], 'size': target['flashSize']}]
    return any(r['address'] <= address and address + size <= r['address'] + r['size'] for r in regions)


def flash_sectors(target, address, length):
    if type(address) is not int or type(length) is not int or length <= 0:
        raise ValueError('Invalid Flash image range')
    selected = [s for s in target['sectors'] if s['address'] < address + length and s['address'] + s['size'] > address]
    # A staged binary owns whole erase sectors, so untouched sector bytes are
    # never silently discarded by a partially specified erase operation.
    if not selected or selected[0]['address'] != address or selected[-1]['address'] + selected[-1]['size'] != address + length:
        raise ValueError('Binary image must cover complete declared erase sectors')
    return selected


def algorithm_bytes(target):
    name = target['algorithm']; metadata = PACK['algorithms'][name]
    folder = Path(sys._MEIPASS) / 'rmlink_algorithms' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2] / 'artifacts/build/flash-algorithms'
    code = (folder / metadata['fileName']).read_bytes()
    validate_algorithm(target, code)
    return code


def validate_algorithm(target, code):
    metadata = PACK['algorithms'][target['algorithm']]; loader = target['loader']
    if not code or len(code) % 4 or loader['address'] + len(code) > loader['buffer']:
        raise ValueError('Algorithm image exceeds its reserved RAM window')
    if any(type(v) is not int or v < 0 or v + 2 > len(code) or v % 2 for v in metadata['entries'].values()):
        raise ValueError('Algorithm entry is outside its image')
    at = metadata['entries']['bkpt']
    if code[at:at+2] != b'\x00\xbe':
        raise ValueError('Algorithm trusted BKPT is missing')
    digest=hashlib.sha256(code).hexdigest()
    if digest!=metadata.get('binarySha256'):
        raise ValueError('Algorithm binary differs from the shared target package')
    if not getattr(sys,'frozen',False):
        source=Path(__file__).resolve().parents[1]/'algorithms'/target['algorithm']
        if hashlib.sha256((source/'algorithm.c').read_bytes()).hexdigest()!=metadata.get('sourceSha256') or hashlib.sha256((source/'algorithm.ld').read_bytes()).hexdigest()!=metadata.get('linkerSha256'):
            raise ValueError('Algorithm source changed; rebuild the shared target package')
    digest_file = (Path(sys._MEIPASS) / 'rmlink_algorithms' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2] / 'artifacts/build/flash-algorithms') / 'manifest.json'
    manifest = json.loads(digest_file.read_text())
    if manifest.get('pack_tag') != PACK_TAG or manifest['algorithms'][target['algorithm']]['sha256'] != digest:
        raise ValueError('Algorithm package hash/version mismatch; rebuild the shared package')
