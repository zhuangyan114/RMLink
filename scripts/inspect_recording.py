"""Validate and summarize a .rmlrec without loading it all into memory."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from rmlink_connector.recording_reader import inspect_recording

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('path',type=Path);args=p.parse_args()
    print(json.dumps(inspect_recording(args.path),indent=2))

if __name__=='__main__':main()
