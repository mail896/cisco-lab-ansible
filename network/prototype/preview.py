#!/usr/bin/python3
"""Offline preview ONLY. Prints a candidate keyfile, performs no network changes."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'security'))
from lab_request import decode_request, keyfile

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest',type=Path,help='Local verified identity JSON, treated as root input')
    p.add_argument('request',type=Path,help='JSON request (bounded to 16 KiB)')
    args=p.parse_args()
    # Bound reads as well as JSON parsing; never read arbitrarily large requests.
    with args.request.open() as stream:
        request=decode_request(stream.read(16385))
    print(keyfile(request,json.loads(args.manifest.read_text())))
