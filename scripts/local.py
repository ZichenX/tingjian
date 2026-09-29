#!/usr/bin/env python3
"""Optional native developer entry point; always loopback-only, real models, never a fake fallback."""
import argparse
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.configure import ROOT, load_env

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','run']);a=p.parse_args()
    values=load_env(ROOT/'.env')
    if values.get('APP_ORIGIN')!='http://localhost:8000':raise SystemExit('请先 configure.py --dev；此入口不能用于公网生产部署')
    os.environ.update(values)
    if a.action=='prepare':
        for script in ['download_models.py','self_test.py']:
            subprocess.run([sys.executable,str(ROOT/'scripts'/script)],check=True,cwd=ROOT)
    else:
        import uvicorn
        from app.main import create_app
        uvicorn.run(create_app(),host='127.0.0.1',port=8000,access_log=False,proxy_headers=False,
                    ws_max_size=131072,ws_max_queue=4,limit_concurrency=32)

if __name__=='__main__':main()
