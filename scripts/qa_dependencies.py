"""OSV public package metadata audit; no application content leaves machine."""
import json,sys,datetime
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];out=ROOT/'reports/qa_20261124'
packages=[]
for line in (ROOT/'backend/requirements.lock').read_text().splitlines():
    if '==' in line:
        name,version=line.split('==',1);packages.append({'ecosystem':'PyPI','name':name,'version':version,'dev':name.lower() in ['pytest','pygments','pluggy','iniconfig']})
lock=json.loads((ROOT/'frontend/package-lock.json').read_text())
seen=set()
for path,p in lock.get('packages',{}).items():
    if not path or not p.get('version'):continue
    name=p.get('name') or path.rsplit('node_modules/',1)[-1]
    key=(name,p['version'])
    if key not in seen:
        seen.add(key);packages.append({'ecosystem':'npm','name':name,'version':p['version'],'dev':p.get('dev',False)})
report={'queried_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source':'https://api.osv.dev/v1/querybatch','packages_checked':len(packages),'scope':'locked package name/version metadata only','packages':packages}
try:
    with httpx.Client(timeout=20) as client:
        r=client.post(report['source'],json={'queries':[{'package':{'ecosystem':p['ecosystem'],'name':p['name']},'version':p['version']} for p in packages]});r.raise_for_status()
        results=r.json()['results'];report['status']='completed';findings=[]
        for p,result in zip(packages,results):
            for vuln in result.get('vulns',[]):findings.append({'package':p,**vuln})
        details={}
        for finding in findings:
            ident=finding['id']
            if ident in details:continue
            try:
                r=client.get('https://api.osv.dev/v1/vulns/'+ident);r.raise_for_status();details[ident]=r.json()
            except Exception as exc:details[ident]={'error':type(exc).__name__+': '+str(exc)}
        report['findings']=findings;report['advisories']=details
except Exception as exc:report.update(status='unavailable',error=type(exc).__name__+': '+str(exc),findings=None)
(out/'dependency_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('packages','advisories','findings')},ensure_ascii=False));print('findings:',None if report.get('findings') is None else len(report['findings']))
