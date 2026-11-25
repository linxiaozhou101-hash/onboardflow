import {defineConfig} from '@playwright/test';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {existsSync} from 'node:fs';
const localChrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
export default defineConfig({testDir:'./tests',testIgnore:'**/roleplay/**',fullyParallel:false,workers:1,retries:0,timeout:60000,expect:{timeout:10000},reporter:[['list'],['json',{outputFile:'../reports/browser-tests.json'}]],outputDir:'test-results',use:{baseURL:'http://127.0.0.1:8001',headless:true,viewport:{width:1440,height:1050},launchOptions:existsSync(localChrome)?{executablePath:localChrome}:{},trace:'retain-on-failure',screenshot:'only-on-failure'},webServer:{command:'../.venv/bin/python -m uvicorn backend.app.main:app --app-dir .. --host 127.0.0.1 --port 8001',url:'http://127.0.0.1:8001/api/health',reuseExistingServer:false,timeout:30000,env:{DATABASE_PATH:join(tmpdir(),`onboardflow-browser-${process.pid}.sqlite3`),DATA_ORIGIN:'test',BIND_HOST:'127.0.0.1',APP_MODE:'demo',LLM_MODE:'offline',ALLOWED_ORIGINS:'http://127.0.0.1:8001'}}});
