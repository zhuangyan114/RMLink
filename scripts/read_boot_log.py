"""Capture real UART boot evidence from the development board (not its target MCU)."""
import argparse
from pathlib import Path
import time
import serial

parser = argparse.ArgumentParser()
parser.add_argument('--port', default='COM13')
parser.add_argument('--seconds', type=float, default=10)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--no-reset',action='store_true',help='Observe UART passively during a live test')
parser.add_argument('--live',action='store_true',help='Flush a new evidence file incrementally during a long test')
args = parser.parse_args()
if args.live and args.output.exists():parser.error('Preserve existing evidence; choose a new output')
args.output.parent.mkdir(parents=True,exist_ok=True)
with serial.Serial(port=None, baudrate=115200, timeout=0.1) as uart:
    uart.port = args.port
    uart.dtr = False
    uart.rts = False
    uart.open()
    if not args.no_reset:
        uart.rts = True
        time.sleep(0.1)
        uart.rts = False
    data = bytearray()
    deadline = time.monotonic() + args.seconds
    live=args.output.open('xb') if args.live else None
    try:
        while time.monotonic() < deadline:
            piece=uart.read(uart.in_waiting or 1);data.extend(piece)
            if live and piece:live.write(piece);live.flush()
    finally:
        if live:live.close()
text = data.decode('utf-8', errors='replace')
args.output.parent.mkdir(parents=True, exist_ok=True)
if not args.live:args.output.write_text(text, encoding='utf-8')
print(text)
