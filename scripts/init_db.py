import sys,argparse,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.db import ROOT,connect
from backend.app.seed import seed
p=argparse.ArgumentParser();p.add_argument('--reset-demo',action='store_true');p.add_argument('--confirm',default='');args=p.parse_args()
if args.reset_demo:
    if args.confirm!='RESET_DEMO':p.error('重置必须显式提供 --confirm RESET_DEMO')
    target=Path(os.getenv('DATABASE_PATH',str(ROOT/'data'/'onboardflow.sqlite3'))).resolve()
    if target!=ROOT/'data'/'onboardflow.sqlite3':p.error('重置仅允许默认 data/onboardflow.sqlite3 演示数据库')
    for path in (target,Path(str(target)+'-wal'),Path(str(target)+'-shm')):
        if path.exists():path.unlink()
print('已创建模拟数据' if seed() else '已有数据，保持不变')
