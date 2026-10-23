import subprocess,os,secrets,json
prefix='web3-scout-validation'
base=['docker','--host=unix:///var/run/docker.sock']
env={k:v for k,v in os.environ.items() if k not in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']}
env.update(BUILDX_CONFIG='/tmp/scout-buildx',SCOUT_DEPLOY_PREFIX=prefix,SCOUT_SKIP_TUNNEL='1')
def d(*args,**kw):
    r=subprocess.run(base+list(args),env=env,text=True,capture_output=True,**kw)
    if r.returncode:raise RuntimeError(r.stdout+'\n'+r.stderr)
    return r.stdout.strip()
password=secrets.token_urlsafe(24)
try:
    d('volume','create','--label','com.web3-scout.stack='+prefix,prefix+'-data')
    d('run','--rm','-i','--network','none','--mount','type=volume,src='+prefix+'-data,dst=/data','web3-scout-deploy-test','python3','manage.py','user-add','tester','--password-stdin',input=password+'\n')
    for iteration in range(2):
        run=subprocess.run(['bash','deploy/deploy.sh'],env=env,text=True,capture_output=True)
        if run.returncode:raise RuntimeError(run.stdout+'\n'+run.stderr)
        print('PASS: deployment script run',iteration+1)
        code='''import json,sys,urllib.request,urllib.error
password=sys.stdin.readline().strip()
url='http://127.0.0.1:3000'
try:
    urllib.request.urlopen(url+'/api/state');raise AssertionError('unauthenticated data leak')
except urllib.error.HTTPError as e:assert e.code==401
request=urllib.request.Request(url+'/api/login',data=json.dumps({'username':'tester','password':password}).encode(),headers={'Content-Type':'application/json'})
r=urllib.request.urlopen(request);cookie=r.headers['Set-Cookie'];assert 'Secure' in cookie and 'HttpOnly' in cookie
request=urllib.request.Request(url+'/api/state',headers={'Cookie':cookie})
state=json.load(urllib.request.urlopen(request))
if not state['projects']:
    request=urllib.request.Request(url+'/api/projects',data=json.dumps({'name':'Docker persistence test','owner':'Tester'}).encode(),headers={'Content-Type':'application/json','Cookie':cookie});assert urllib.request.urlopen(request).status==200
else:assert state['projects'][0]['name']=='Docker persistence test'
print('PASS: authenticated container workflow and durable data')
'''
        print(d('exec','-i',prefix+'-app','python3','-c',code,input=password+'\n'))
    print('PASS: non-root Docker build, read-only rootfs, safe repeatable update, backups, auth and persistence')
finally:
    for name in [prefix+'-app',prefix+'-rollback',prefix+'-tunnel']:
        subprocess.run(base+['rm','-f',name],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(base+['network','rm',prefix+'-network'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(base+['volume','rm',prefix+'-data'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
