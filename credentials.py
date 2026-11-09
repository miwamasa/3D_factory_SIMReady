#!/usr/bin/env python3
"""Display one development credential to the local human operator."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("role", choices=["agent", "supervisor", "planner", "viewer"])
parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent / ".runtime"))
args = parser.parse_args()
path = Path(args.data_dir) / "credentials.json"
if not path.exists():
    parser.error("先にserver.pyを起動してください")
print(json.loads(path.read_text())[args.role]["token"])
