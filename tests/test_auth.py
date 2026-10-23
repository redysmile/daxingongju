import unittest, tempfile, threading, json, os, sys, time
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server, auth

class AuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.original_db=server.DB;server.DB=Path(cls.tmp.name)/'auth.db';server.init_db()
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);cls.base='http://127.0.0.1:'+str(cls.http.server_port)
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.tmp.cleanup();server.DB=cls.original_db
    def setUp(self):
        self.settings=patch.dict(os.environ,{'SCOUT_REQUIRE_AUTH':'1','SCOUT_COOKIE_SECURE':'0'});self.settings.start()
        with server.connect() as db:
            for t in ['users','sessions','login_attempts','projects','activity']:db.execute('DELETE FROM '+t)
            auth.set_user(db,'researcher','a-long-test-password')
    def tearDown(self):self.settings.stop()
    def request(self,path,method='GET',d=None,cookie=None,origin=None):
        headers={'Content-Type':'application/json'}
        if cookie:headers['Cookie']=cookie
        if origin:headers['Origin']=origin
        req=Request(self.base+path,data=json.dumps(d).encode() if d is not None else None,method=method,headers=headers)
        try:
            with urlopen(req) as r:return r.status,r.read(),r.headers
        except HTTPError as e:return e.code,e.read(),e.headers
    def login(self):
        code,_,headers=self.request('/api/login','POST',{'username':'researcher','password':'a-long-test-password'})
        self.assertEqual(code,200);return headers['Set-Cookie']
    def test_api_denies_read_and_write_without_session(self):
        self.assertEqual(self.request('/api/state')[0],401)
        self.assertEqual(self.request('/api/projects','POST',{'name':'Unauthorized','owner':'A'})[0],401)
        code,body,_=self.request('/');self.assertEqual(code,200);self.assertIn('团队登录'.encode(),body)
        self.assertEqual(self.request('/api/health')[0],200)
    def test_login_cookie_and_logout(self):
        cookie=self.login();self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        code,body,_=self.request('/api/state',cookie=cookie);self.assertEqual(code,200);self.assertEqual(json.loads(body)['user'],'researcher')
        self.assertEqual(self.request('/api/logout','POST',{},cookie)[0],200)
        self.assertEqual(self.request('/api/state',cookie=cookie)[0],401)
    def test_audit_uses_session_identity(self):
        cookie=self.login()
        code,body,_=self.request('/api/projects','POST',{'name':'Owned project','owner':'Another researcher'},cookie)
        self.assertEqual(code,200)
        _,body,_=self.request('/api/state',cookie=cookie)
        self.assertEqual(json.loads(body)['activity'][0]['actor'],'researcher')
    def test_wrong_password_and_throttle(self):
        for _ in range(8):self.assertEqual(self.request('/api/login','POST',{'username':'researcher','password':'wrong'})[0],401)
        self.assertEqual(self.request('/api/login','POST',{'username':'researcher','password':'a-long-test-password'})[0],429)
    def test_reset_revokes_and_hash_is_not_password(self):
        cookie=self.login()
        with server.connect() as db:
            auth.set_user(db,'researcher','a-new-long-test-password')
            row=db.execute('SELECT password_hash FROM users').fetchone()[0]
            self.assertNotIn('a-new-long-test-password',row)
        self.assertEqual(self.request('/api/state',cookie=cookie)[0],401)
    def test_session_expiry_and_cross_site(self):
        cookie=self.login()
        with server.connect() as db:db.execute('UPDATE sessions SET expires=?',(int(time.time())-1,))
        self.assertEqual(self.request('/api/state',cookie=cookie)[0],401)
        self.assertEqual(self.request('/api/login','POST',{'username':'researcher','password':'a-long-test-password'},origin='https://other.example')[0],403)
    def test_secure_cookie_default_and_bootstrap(self):
        with patch.dict(os.environ,{'SCOUT_COOKIE_SECURE':'1'}):self.assertIn('Secure',self.login())
        with server.connect() as db:db.execute('DELETE FROM users')
        with patch.dict(os.environ,{'SCOUT_ADMIN_PASSWORD':''}):
            with self.assertRaises(RuntimeError):auth.bootstrap(server.connect)
        with patch.dict(os.environ,{'SCOUT_ADMIN_USERNAME':'owner','SCOUT_ADMIN_PASSWORD':'bootstrap-long-password'}):auth.bootstrap(server.connect)
        with server.connect() as db:self.assertTrue(db.execute('SELECT 1 FROM users WHERE username="owner"').fetchone())

if __name__=='__main__':unittest.main()
