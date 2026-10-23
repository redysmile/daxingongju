import unittest, tempfile, threading, json, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); server.DB=Path(cls.tmp.name)/'test.db';server.init_db()
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.http.shutdown();cls.http.server_close();cls.tmp.cleanup()
    def setUp(self):
        with server.connect() as db:
            for t in ['projects','kols','activity']: db.execute('DELETE FROM '+t)
            db.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps({'weights':server.DEFAULT_WEIGHTS}),))
    def request(self,path,method='GET',d=None,headers=None):
        req=Request(self.base+'/api/'+path,data=json.dumps(d).encode() if d is not None else None,method=method,headers={'Content-Type':'application/json',**(headers or {})})
        try:
            with urlopen(req) as r: return r.status,json.load(r)
        except HTTPError as e: return e.code,json.load(e)
    def project(self,**kwargs): return {'name':'Test Alpha','owner':'测试员','scores':[90]*6,**kwargs}
    def test_create_edit_and_persist(self):
        code,p=self.request('projects','POST',self.project());self.assertEqual(code,200);self.assertEqual(p['grade'],'A')
        code,p=self.request('projects/'+p['id'],'PUT',{**p,'status':'已关注'});self.assertEqual(code,200)
        data=self.request('state')[1];self.assertEqual(data['projects'][0]['status'],'已关注');self.assertEqual(len(data['activity']),2)
    def test_s_requires_distinct_active_kols(self):
        signals=[{'handle':'one','weight':90,'type':'转发'},{'handle':'one','weight':90,'type':'回复'}]
        p=self.request('projects','POST',self.project(signals=signals))[1];self.assertEqual(p['grade'],'A')
        signals[1]['handle']='two';p=self.request('projects/'+p['id'],'PUT',{**p,'signals':signals})[1];self.assertEqual(p['grade'],'S')
    def test_incomplete_not_zero(self):
        p=self.request('projects','POST',self.project(scores=[90,None,70,80,90,60]))[1];self.assertIsNone(p['score']);self.assertEqual(p['grade'],'待评估')
    def test_prepared_gate(self):
        self.assertEqual(self.request('projects','POST',self.project(pool='预备参与池'))[0],400)
        self.assertEqual(self.request('projects','POST',self.project(pool='预备参与池',scores=[30]*6,mintAt='2026-11-01T00:00:00Z'))[0],400)
        self.assertEqual(self.request('projects','POST',self.project(pool='预备参与池',scores=[30]*6,mintAt='2026-11-01T00:00:00Z',poolReason='人工保留参与机会'))[0],200)
    def test_duplicate_normalization(self):
        self.request('projects','POST',self.project(handle='Alpha'))
        self.assertEqual(self.request('projects','POST',self.project(name='Different',handle='@alpha'))[0],400)
    def test_manual_grade_reason(self):
        self.assertEqual(self.request('projects','POST',self.project(manualGrade='S'))[0],400)
        p=self.request('projects','POST',self.project(manualGrade='B',manualReason='风险仍需验证'))[1];self.assertEqual(p['grade'],'B');self.assertEqual(p['autoGrade'],'A')
    def test_weights_recalculate(self):
        self.request('projects','POST',self.project(scores=[100,0,0,0,0,0]))
        self.assertEqual(self.request('settings','PUT',{'weights':[1]*6})[0],400)
        self.assertEqual(self.request('settings','PUT',{'weights':[100,0,0,0,0,0]})[0],200)
        self.assertEqual(self.request('state')[1]['projects'][0]['score'],100)
    def test_notes_and_delete(self):
        p=self.request('projects','POST',self.project())[1]
        self.assertEqual(self.request('notes','POST',{'projectId':p['id'],'note':'发现新进展','actor':'研究员 B'})[0],200)
        self.assertEqual(self.request('state')[1]['activity'][0]['note'],'发现新进展')
        self.request('projects/'+p['id'],'DELETE');self.assertEqual(self.request('state')[1]['projects'],[])
    def test_invalid_input_and_cross_site(self):
        for update in [{'website':'javascript:alert(1)'},{'scores':[101]*6},{'mintAt':'tomorrow'}]: self.assertEqual(self.request('projects','POST',self.project(**update))[0],400)
        self.assertEqual(self.request('projects','POST',self.project(),{'Origin':'https://elsewhere.example'})[0],403)
    def test_kol_and_demo(self):
        self.assertEqual(self.request('kols','POST',{'name':'Researcher','handle':'test','weight':90})[0],200)
        self.assertEqual(self.request('kols','POST',{'name':'Duplicate','handle':'TEST','weight':90})[0],400)
        self.assertEqual(self.request('demo','POST',{})[0],200)
        self.assertEqual(len(self.request('state')[1]['projects']),6)
        self.request('projects','POST',self.project())
        self.request('demo','DELETE');self.assertEqual(len(self.request('state')[1]['projects']),1)

if __name__=='__main__': unittest.main()
