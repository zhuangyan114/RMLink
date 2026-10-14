"""Compatibility import for the shared native WinUSB transport."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"host"))
from rmlink_native_usb import GUID, InterfaceData, UsbInterface, PipeInfo, UsbClient
