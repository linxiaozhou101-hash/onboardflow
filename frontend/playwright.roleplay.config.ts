import {defineConfig} from '@playwright/test';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {existsSync,mkdirSync} from 'node:fs';
const runId=process.env.ROLEPLAY_RUN_ID||(process.env.ROLEPLAY_RUN_ID=String(Date.now()));
const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
mkdirSync('../reports/roleplay_20261124/screenshots',{recursive:true});
export default defineConfig({testDir:'./tests/roleplay',fullyParallel:false,workers:1,retries:0,globalTimeout:180000,timeout:60000,expect:{timeout:8000},reporter:[['list'],['json',{outputFile:'../reports/roleplay_20261124/journeys.json'}],['junit',{outputFile:'../reports/roleplay_20261124/journeys.xml'}]],outputDir:'../reports/roleplay_20261124/artifacts-'+runId,use:{actionTimeout:12000,baseURL:'http://127.0.0.1:8004',headless:true,timezoneId:'Asia/Shanghai',viewport:{width:1440,height:1000},launchOptions:existsSync(chrome)?{executablePath:chrome}:{},trace:'retain-on-failure',screenshot:'only-on-failure'},webServer:{command:'../.venv/bin/python -m uvicorn backend.app.main:app --app-dir .. --host 127.0.0.1 --port 8004',url:'http://127.0.0.1:8004/api/health',reuseExistingServer:false,env:{DATABASE_PATH:join(tmpdir(),`onboardflow-roleplay-${process.pid}.sqlite3`),DATA_ORIGIN:'test',BIND_HOST:'127.0.0.1',APP_MODE:'demo',LLM_MODE:'offline',ALLOWED_ORIGINS:'http://127.0.0.1:8004'}}});
