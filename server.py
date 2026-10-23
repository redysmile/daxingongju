#!/usr/bin/env python3
"""Web3 Scout internal research workspace. Python 3.12+, no dependencies."""
import json, os, sqlite3, uuid, math, secrets
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from contextvars import ContextVar
import auth

request_actor = ContextVar("request_actor", default=None)

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('SCOUT_DB', ROOT / '.data/scout.db'))
DEFAULT_WEIGHTS = [35, 20, 15, 15, 10, 5]
POOLS = ['候选池', '观察池', '预备参与池', '已归档']
STATUSES = ['待研究', '已关注', '已参与', '忽略']

def now(): return datetime.now(timezone.utc).isoformat()
def connect():
    db = sqlite3.connect(DB, timeout=20)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS kols(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
        ''')
        db.execute('INSERT OR IGNORE INTO settings VALUES(1, ?)', (json.dumps({'weights': DEFAULT_WEIGHTS}),))
        auth.init(db)

def weights(db): return json.loads(db.execute('SELECT data FROM settings WHERE id=1').fetchone()[0])['weights']
def rating(p, w):
    scores = p.get('scores', [])
    if len(scores) != 6 or any(v is None for v in scores): return {'score': None, 'grade': p.get('manualGrade') or '待评估', 'autoGrade': '待评估'}
    score = round(sum(a*b for a,b in zip(scores,w))/100, 1)
    # S requires at least two distinct high-weight KOLs with active interaction.
    strong = len({s['handle'].lower() for s in p.get('signals', []) if s['weight'] >= 80 and s['type'] in ['推广', '转发', '回复']}) >= 2
    grade = 'S' if score >= 85 and strong else 'A' if score >= 70 else 'B' if score >= 50 else 'C'
    return {'score': score, 'grade': p.get('manualGrade') or grade, 'autoGrade': grade}

def decorate(p,w): return {**p, **rating(p,w)}
def activity(db, title, project=None, note='', actor='内部测试员'):
    db.execute('INSERT INTO activity(data) VALUES(?)', (json.dumps({'title':title, 'projectId': project.get('id') if project else None, 'projectName':project.get('name') if project else '', 'note':note, 'actor':request_actor.get() or actor, 'at':now()}, ensure_ascii=False),))

def text(d,key,limit=2000,required=False):
    value = d.get(key,'')
    if not isinstance(value,str): raise ValueError(f'{key} 必须是文本')
    value=value.strip()
    if required and not value: raise ValueError(f'{key} 不能为空')
    if len(value)>limit: raise ValueError(f'{key} 超过长度限制 {limit}')
    return value

def validate_project(d,old=None):
    p={k:text(d,k,lim,k=='name') for k,lim in {'name':80,'handle':80,'description':1000,'chain':60,'category':60,'website':500,'mintUrl':500,'source':1000,'summary':2000,'owner':60,'nextAction':1000,'mintAt':40,'reviewAt':40,'manualGrade':10,'manualReason':1000,'poolReason':1000}.items()}
    if not p['owner']: raise ValueError('负责研究员不能为空')
    p['handle']=p['handle'].lstrip('@')
    if any(c.isspace() for c in p['handle']) or any(c in p['handle'] for c in '/?#'): raise ValueError('X 账号请填写 handle，不含链接或空格')
    for k in ['website','mintUrl']:
        if p[k] and (urlparse(p[k]).scheme not in ['https','http'] or not urlparse(p[k]).hostname): raise ValueError('链接必须是完整的 http(s) 地址')
    for k in ['mintAt','reviewAt']:
        if p[k]:
            try: datetime.fromisoformat(p[k].replace('Z','+00:00'))
            except ValueError: raise ValueError('时间格式不正确')
    p['pool']=d.get('pool','候选池'); p['status']=d.get('status','待研究')
    if p['pool'] not in POOLS or p['status'] not in STATUSES: raise ValueError('项目分层或状态无效')
    if p['manualGrade'] not in ['', 'S','A','B','C']: raise ValueError('评级无效')
    if p['manualGrade'] and not p['manualReason']: raise ValueError('人工调整评级必须填写原因')
    p['scores']=d.get('scores',[None]*6)
    if not isinstance(p['scores'],list) or len(p['scores'])!=6 or any(v is not None and (type(v) not in [int,float] or not math.isfinite(v) or not 0<=v<=100) for v in p['scores']): raise ValueError('六项评分须为 0–100 的数字或留空')
    p['signals']=d.get('signals',[])
    if not isinstance(p['signals'],list) or len(p['signals'])>100: raise ValueError('信号列表无效')
    normalized=[]
    for s in p['signals']:
        if not isinstance(s,dict): raise ValueError('信号格式无效')
        handle=text(s,'handle',80,True).lstrip('@')
        weight=s.get('weight',50)
        if type(weight) not in [int,float] or not math.isfinite(weight) or not 0<=weight<=100: raise ValueError('KOL 权重须为 0–100')
        if s.get('type') not in ['关注','推广','转发','回复']: raise ValueError('互动类型无效')
        normalized.append({'handle':handle,'weight':weight,'type':s['type']})
    p['signals']=normalized
    p['id']=old['id'] if old else uuid.uuid4().hex
    p['createdAt']=old['createdAt'] if old else now(); p['updatedAt']=now()
    p['demo']=old.get('demo',False) if old else False
    return p

def save_project(db,d,old=None):
    p=validate_project(d,old)
    for row in db.execute('SELECT data FROM projects'):
        other=json.loads(row[0])
        if other['id']!=p['id'] and (other['name'].casefold()==p['name'].casefold() or (p['handle'] and other['handle'].casefold()==p['handle'].casefold())): raise ValueError('已存在同名项目或相同 X 账号，请编辑原项目')
    if p['pool']=='预备参与池':
        r=rating(p,weights(db))
        if not p['mintAt']: raise ValueError('进入预备参与池前，请填写 Mint / 相关事件时间')
        if r['grade'] not in ['S','A'] and not p['poolReason']: raise ValueError('低于 A 级或尚未评估的项目，进入预备参与池需填写人工分池原因')
    db.execute('INSERT OR REPLACE INTO projects VALUES(?,?)',(p['id'],json.dumps(p,ensure_ascii=False)))
    detail=[]
    if old:
        for key,label in [('pool','分层'),('status','跟踪状态'),('manualGrade','人工评级')]:
            if p.get(key)!=old.get(key): detail.append(f'{label}：{old.get(key) or "自动"} → {p.get(key) or "自动"}')
        if p['scores']!=old.get('scores'): detail.append('更新六维评分')
        if p['manualReason'] and p['manualGrade']: detail.append('评级原因：'+p['manualReason'])
    activity(db,'更新项目' if old else '录入项目',p,'；'.join(detail),p['owner'] or '内部测试员')
    return decorate(p,weights(db))

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args): pass
    def reply(self,status,payload,extra_headers=None):
        body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store');
        for key,value in (extra_headers or {}).items(): self.send_header(key,value)
        self.end_headers(); self.wfile.write(body)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if not 0<n<=300000: raise ValueError('请求大小无效')
        d=json.loads(self.rfile.read(n))
        if not isinstance(d,dict): raise ValueError('请求格式无效')
        return d
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        super().end_headers()
    def authorize(self):
        request_actor.set(None)
        if not auth.enabled(): return True
        with connect() as db: user=auth.current_user(db,self.headers)
        request_actor.set(user)
        return bool(user)
    def do_GET(self):
        path=urlparse(self.path).path
        public=['/login','/login.js','/style.css','/api/health','/brand/scout-mark.png','/favicon.ico']
        if path not in public and not self.authorize():
            if path.startswith('/api/'): return self.reply(401,{'error':'请先登录'})
            self.send_response(303); self.send_header('Location','/login'); self.send_header('Cache-Control','no-store'); self.end_headers(); return
        if path=='/api/state':
            with connect() as db:
                w=weights(db)
                self.reply(200,{'user':request_actor.get(), 'projects':[decorate(json.loads(r[0]),w) for r in db.execute('SELECT data FROM projects')], 'kols':[json.loads(r[0]) for r in db.execute('SELECT data FROM kols')], 'weights':w, 'activity':[json.loads(r[0]) for r in db.execute('SELECT data FROM activity ORDER BY id DESC LIMIT 500')]})
            return
        if path=='/api/health': return self.reply(200,{'ok':True})
        names={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/login':'login.html','/login.js':'login.js','/brand/scout-mark.png':'brand/scout-mark.png','/favicon.ico':'brand/scout-mark.png'}
        if path not in names: return self.reply(404,{'error':'页面不存在'})
        f=ROOT/'web'/names[path]; body=f.read_bytes()
        content_type={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png'}[f.suffix]
        self.send_response(200); self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','public, max-age=86400' if f.suffix=='.png' else 'no-store')
        self.end_headers(); self.wfile.write(body)
    def do_POST(self): self.mutate('POST')
    def do_PUT(self): self.mutate('PUT')
    def do_DELETE(self): self.mutate('DELETE')
    def mutate(self,method):
        try:
            # Reject cross-site writes; the MVP is intended for a trusted internal network.
            origin=self.headers.get('Origin')
            if origin and urlparse(origin).netloc!=self.headers.get('Host'): return self.reply(403,{'error':'不允许跨站写入'})
            path=urlparse(self.path).path; parts=path.strip('/').split('/')
            if path=='/api/login' and method=='POST':
                if not auth.enabled(): return self.reply(400,{'error':'当前未启用账号登录'})
                d=self.body()
                with connect() as db: code,result,cookie=auth.login(db,d.get('username'),d.get('password'),self.client_address[0])
                return self.reply(code,result,{'Set-Cookie':cookie} if cookie else {})
            if not self.authorize(): return self.reply(401,{'error':'请先登录'})
            if path=='/api/logout' and method=='POST':
                with connect() as db: auth.logout(db,self.headers)
                return self.reply(200,{'ok':True},{'Set-Cookie':auth.cookie(expire=True)})
            d=self.body() if method!='DELETE' else {}
            with connect() as db:
                if path=='/api/projects' and method=='POST': result=save_project(db,d)
                elif len(parts)==3 and parts[:2]==['api','projects']:
                    row=db.execute('SELECT data FROM projects WHERE id=?',(parts[2],)).fetchone()
                    if not row: return self.reply(404,{'error':'项目不存在，请刷新'})
                    old=json.loads(row[0])
                    if method=='PUT': result=save_project(db,d,old)
                    elif method=='DELETE':
                        db.execute('DELETE FROM projects WHERE id=?',(parts[2],)); activity(db,'删除项目',old,actor=old.get('owner') or '内部测试员'); result={'ok':True}
                    else: raise ValueError('不支持的操作')
                elif path=='/api/notes' and method=='POST':
                    row=db.execute('SELECT data FROM projects WHERE id=?',(d.get('projectId'),)).fetchone()
                    if not row: raise ValueError('项目不存在')
                    p=json.loads(row[0]); note=text(d,'note',2000,True); actor=text(d,'actor',60) or '内部测试员'
                    activity(db,'跟踪记录',p,note,actor); p['updatedAt']=now(); db.execute('UPDATE projects SET data=? WHERE id=?',(json.dumps(p,ensure_ascii=False),p['id'])); result={'ok':True}
                elif path=='/api/settings' and method=='PUT':
                    w=d.get('weights')
                    if not isinstance(w,list) or len(w)!=6 or any(type(x)!=int or not 0<=x<=100 for x in w) or sum(w)!=100: raise ValueError('六项权重须为非负整数，合计 100%')
                    db.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps({'weights':w}),)); activity(db,'更新评分权重',note='权重：'+', '.join(map(str,w))); result={'ok':True}
                elif path=='/api/kols' and method=='POST':
                    name=text(d,'name',80,True); handle=text(d,'handle',80,True).lstrip('@'); weight=d.get('weight',50)
                    if type(weight) not in [int,float] or not math.isfinite(weight) or not 0<=weight<=100: raise ValueError('权重须为 0–100')
                    if any(json.loads(r[0])['handle'].casefold()==handle.casefold() for r in db.execute('SELECT data FROM kols')): raise ValueError('此 KOL 已存在')
                    kol={'id':uuid.uuid4().hex,'name':name,'handle':handle,'weight':weight,'category':text(d,'category',60),'note':text(d,'note',1000)}
                    db.execute('INSERT INTO kols VALUES(?,?)',(kol['id'],json.dumps(kol,ensure_ascii=False))); activity(db,'新增 KOL',note=name); result=kol
                elif len(parts)==3 and parts[:2]==['api','kols'] and method=='DELETE':
                    db.execute('DELETE FROM kols WHERE id=?',(parts[2],)); result={'ok':True}
                elif path=='/api/demo' and method=='POST':
                    if db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]: raise ValueError('仅空项目池可载入示例')
                    names=[('Aether Nexus','Ethereum','Infra','预备参与池',[94,88,90,92,85,80]),('Solstice Protocol','Solana','DeFi','观察池',[80,76,82,78,75,70]),('Nebula Worlds','Ethereum','NFT','观察池',[72,65,78,70,62,60]),('Rune Garden','Bitcoin','Runes','候选池',[55,60,65,52,50,40]),('Tape Genesis','BEM / TapeOut','BEM / TapeOut','候选池',[None]*6),('Orbit Identity','Base','DID','预备参与池',[85,78,86,88,80,75])]
                    from datetime import timedelta
                    for i,(name,chain,cat,pool,scores) in enumerate(names):
                        p=save_project(db,{'name':name,'handle':'demo_'+name.lower().replace(' ','_'),'description':['为链上应用构建模块化执行与数据基础设施。','探索新一代链上收益与流动性组合。','以社区共创为核心的数字身份与收藏品世界。','围绕 Bitcoin 原生资产的早期生态实验。','BEM / TapeOut 生态原生项目，等待补充研究。','连接链上声誉与可验证身份。'][i],'chain':chain,'category':cat,'pool':pool,'scores':scores,'owner':'示例研究员','status':'已关注' if i in [0,1,5] else '待研究','mintAt':(datetime.now(timezone.utc)+timedelta(days=i+1)).isoformat() if pool=='预备参与池' else '', 'signals':[{'handle':'demo_researcher_a','weight':90,'type':'转发'},{'handle':'demo_researcher_b','weight':85,'type':'回复'}] if i==0 else [],'source':'演示数据，不代表真实项目或投资建议'})
                        p.pop('score');p.pop('grade');p.pop('autoGrade');p['demo']=True
                        db.execute('UPDATE projects SET data=? WHERE id=?',(json.dumps(p,ensure_ascii=False),p['id']))
                    result={'ok':True}
                elif path=='/api/demo' and method=='DELETE':
                    for row in db.execute('SELECT id,data FROM projects').fetchall():
                        if json.loads(row['data']).get('demo'): db.execute('DELETE FROM projects WHERE id=?',(row['id'],))
                    activity(db,'清除示例项目'); result={'ok':True}
                else: return self.reply(404,{'error':'接口不存在'})
            self.reply(200,result)
        except (ValueError,TypeError,KeyError) as e: self.reply(400,{'error':str(e)})
        except Exception: self.reply(500,{'error':'服务处理失败，请稍后重试'})

if __name__=='__main__':
    init_db()
    port=int(os.environ.get('PORT','3000')); host=os.environ.get('HOST','127.0.0.1')
    if host not in ['127.0.0.1','localhost','::1'] and not auth.enabled():
        raise RuntimeError('公网监听必须设置 SCOUT_REQUIRE_AUTH=1 并初始化账号。')
    auth.bootstrap(connect)
    print(f'Web3 Scout running on {host}:{port}',flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
