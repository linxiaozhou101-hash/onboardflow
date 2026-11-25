import os
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from . import auth,cases,knowledge,analytics
from .seed import seed

@asynccontextmanager
async def lifespan(app):
    seed()
    yield
app=FastAPI(title='OnboardFlow · 入职服务工作台',version='1.0.0',lifespan=lifespan,description='本地演示，人物、制度、业务记录全部为模拟数据。')

@app.middleware('http')
async def origin_guard(request:Request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        allowed=set(os.getenv('ALLOWED_ORIGINS','http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000').split(','))
        if (origin and origin not in allowed) or request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':{'code':'invalid_origin','message':'请求来源不受信任'}},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    return response
for r in (auth.router,cases.router,knowledge.router,analytics.router):app.include_router(r)
@app.get('/api/health')
def health():return {'status':'ok','app_mode':os.getenv('APP_MODE','demo'),'provider_mode':os.getenv('LLM_MODE','offline'),'data':'synthetic','real_external_actions':False}
DIST=Path(__file__).resolve().parents[2]/'frontend'/'dist'
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')
    @app.get('/{path:path}',include_in_schema=False)
    def spa(path:str):
        if path.startswith('api/'):return JSONResponse({'detail':{'code':'not_found','message':'接口不存在'}},404)
        return FileResponse(DIST/'index.html')
