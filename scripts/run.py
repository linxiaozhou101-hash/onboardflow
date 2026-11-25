"""Local-only launcher. Reads .env without shell evaluation."""
import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if (ROOT/'.env').exists():
    for raw in (ROOT/'.env').read_text().splitlines():
        line=raw.strip()
        if line and not line.startswith('#') and '=' in line:
            key,value=line.split('=',1);os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))
host=os.getenv('BIND_HOST','127.0.0.1')
if host not in ('127.0.0.1','localhost','::1'):raise SystemExit('此版本仅支持本机演示，请将 BIND_HOST 设为 127.0.0.1。')
import uvicorn
if __name__=='__main__':uvicorn.run('backend.app.main:app',host=host,port=8000)
