import {defineConfig} from '@playwright/test';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {existsSync} from 'node:fs';
const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const reportDir=process.env.QA_REPORT_DIR||'../reports/qa_20261124';
process.env.QA_SCREENSHOT_DIR=reportDir+'/screenshots';
export default defineConfig({testDir:'./tests',testIgnore:'**/roleplay/**',fullyParallel:false,workers:1,retries:0,timeout:30000,expect:{timeout:4000},reporter:[['list'],['json',{outputFile:reportDir+'/browser-results.json'}],['junit',{outputFile:reportDir+'/browser-results.xml'}]],outputDir:reportDir+'/browser-artifacts-'+Date.now(),use:{actionTimeout:7000,baseURL:'http://127.0.0.1:8003',headless:true,viewport:{width:1440,height:1050},launchOptions:existsSync(chrome)?{executablePath:chrome}:{},trace:'retain-on-failure',screenshot:'only-on-failure'},webServer:{command:'../.venv/bin/python -m uvicorn backend.app.main:app --app-dir .. --host 127.0.0.1 --port 8003',url:'http://127.0.0.1:8003/api/health',reuseExistingServer:false,env:{DATABASE_PATH:join(tmpdir(),`onboardflow-qa-browser-${process.pid}.sqlite3`),DATA_ORIGIN:'test',BIND_HOST:'127.0.0.1',APP_MODE:'demo',LLM_MODE:'offline',ALLOWED_ORIGINS:'http://127.0.0.1:8003'}}});
