"""Local single-process launcher; demo is explicit and has no host access."""
import argparse
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=("demo", "control", "agent"), default="demo")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=19080)
arguments = parser.parse_args()
web = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(web))
os.chdir(web)
os.environ["MOTOR_MODE"] = arguments.mode
import uvicorn
uvicorn.run("backend.app:app", host=arguments.host, port=arguments.port, workers=1, access_log=False)
