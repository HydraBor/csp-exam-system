"""Anonymous source access must follow publication, exact grading paths and revocation."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import attempts, public_sources, security, store
from csp_exam.web.server import Handler


class PublicSources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='csp-public-sources-')
        cls.data_patch = patch.object(store, 'DATA_DIR', cls.temp.name)
        cls.log_patch = patch.object(Handler, 'log_message', lambda *a: None)
        cls.data_patch.start(); cls.log_patch.start()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        cls.log_patch.stop(); cls.data_patch.stop(); cls.temp.cleanup()

    def setUp(self):
        self.cid, self.kh = 'source-test', 'GD-J12345'
        self.exam = {'released': True, 'problems': [
            {'no': 1, 'pid': 'P1', 'name': 'sum', 'full': 100},
            {'no': 4, 'pid': 'P4', 'name': 'tree', 'full': 100}]}
        self.contest = {'id': self.cid, 'title': '公开源码回归', 'rule': 'OI',
                        'start_at': 1, 'end_at': 2, 'public_sources': True}
        store.save_contests([self.contest]); store.save_exam(self.cid, self.exam)
        store.save_roster(self.cid, {self.kh: {'name': '<甲>', 'campus': '测试校区', 'teacher': '测试老师'}})
        self.rel = '__submissions/' + 'a' * 32 + '/' + self.kh + '/sum/sum.cpp'
        self.src = b'#include <cstdio>\n// <script>alert(1)</script>\nint main(){return 0;}\n'
        base = Path(store.upload_dir(self.cid, self.kh)); full = base / self.rel
        full.parent.mkdir(parents=True, exist_ok=True); full.write_bytes(self.src)
        (full.parent / 'secret.txt').write_text('PRIVATE PERSONAL INFO', encoding='utf-8')
        store.put_result(self.cid, self.kh, {'submitted_at': 'x', 'total': 100,
            'problems': {'T1': {'score': 100, 'file': self.rel, 'testcases': [
                {'status': 1, 'score': 50, 'time': 8, 'memory': 2048,
                 'input': 'PRIVATE CASE INPUT', 'output': 'PRIVATE STUDENT OUTPUT',
                 'answer': 'PRIVATE STANDARD OUTPUT', 'message': 'PRIVATE JUDGE LOG'},
                {'status': 1, 'score': 50, 'time': 12, 'memory': 3072}]}}, 'picked': {'1': self.rel}})
        self.token = public_sources.student_token(self.cid, self.kh)
        self.url = '/scoreboard?' + urllib.parse.urlencode({'c': self.cid, 'source': self.token})

    def get(self, path, *, admin=False, form=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {}
        if admin: headers['Cookie'] = security.ADMIN_COOKIE + '=' + security.make_admin_cookie()
        if form is not None: headers['Content-Type'] = 'application/x-www-form-urlencoded'
        conn.request('POST' if form is not None else 'GET', path,
            urllib.parse.urlencode(form) if form is not None else None, headers)
        response = conn.getresponse(); status,headers,body = response.status,dict(response.getheaders()),response.read()
        conn.close(); return status,headers,body

    def test_anonymous_preview_and_exact_download_without_credentials(self):
        status,_,body = self.get(self.url)
        self.assertEqual(status,200)
        self.assertIn(b'&lt;script&gt;',body)
        self.assertNotIn(b'<script>alert(1)</script>',body)
        self.assertNotIn(self.kh.encode(),body)
        self.assertNotIn(b'PRIVATE PERSONAL INFO',body)
        status,headers,body = self.get(self.url+'&p=1&download=1')
        self.assertEqual(status,200); self.assertEqual(body,self.src)
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertIn('sum.cpp',headers['Content-Disposition'])
        self.assertNotIn(self.kh,headers['Content-Disposition'])

    def test_board_links_contain_only_opaque_ids_and_problem_numbers(self):
        status,_,body = self.get('/api/scoreboard?c='+self.cid)
        row = json.loads(body)['rows'][0]
        self.assertEqual(status,200); self.assertEqual(row['source_id'],self.token)
        self.assertNotIn('kaohao',row)
        status,_,body = self.get('/scoreboard?c='+self.cid)
        self.assertEqual(status,200); self.assertIn(self.token.encode(),body)
        self.assertNotIn(self.kh.encode(),body)

    def test_public_case_details_show_recorded_metrics_without_case_content(self):
        result=store.load_results(self.cid)[self.kh]
        got=result['problems']['T1'];got['score']=50
        got['testcases'][1].update(status=3,score=0,time=2500,memory=262144)
        store.put_result(self.cid,self.kh,result)
        status,_,body=self.get(self.url+'&p=1')
        text=body.decode()
        self.assertEqual(status,200)
        for label in ('逐点评测详情','答案正确','运行超时','2500ms','256.0MB','50 / 100','#1','#2'):
            self.assertIn(label,text)
        for secret in ('PRIVATE CASE INPUT','PRIVATE STUDENT OUTPUT','PRIVATE STANDARD OUTPUT','PRIVATE JUDGE LOG'):
            self.assertNotIn(secret,text)

    def test_missing_case_records_and_compilation_failure_are_explicit(self):
        result=store.load_results(self.cid)[self.kh]
        result['problems']['T1'].update(status=7,score=0,testcases=[],compile_error='PRIVATE COMPILER PATH')
        store.put_result(self.cid,self.kh,result)
        status,_,body=self.get(self.url+'&p=1')
        self.assertEqual(status,200);self.assertIn('编译错误，未运行测试点。'.encode(),body)
        self.assertNotIn(b'PRIVATE COMPILER PATH',body)
        _,_,body=self.get(self.url+'&p=4')
        self.assertIn('本题没有逐点评测记录。'.encode(),body)

    def test_disable_and_withdraw_close_preview_download_and_board_links(self):
        for mode in ('disabled','withdrawn'):
            store.save_contests([{**self.contest,'public_sources':mode!='disabled'}])
            store.save_exam(self.cid,{**self.exam,'released':mode!='withdrawn'})
            for suffix in ('','&p=1&download=1','&private=1'):
                status,_,body=self.get(self.url+suffix,admin=True)
                self.assertEqual(status,403); self.assertNotIn(self.src,body)
            _,_,body=self.get('/api/scoreboard?c='+self.cid)
            self.assertNotIn(self.token.encode(),body)

    def test_flexible_window_waits_for_last_possible_finish(self):
        now=time.time()
        store.save_contests([{**self.contest,'exam_mode':'flexible',
            'start_at':now-7200,'end_at':now-10,'duration_min':180}])
        # The start window has closed, but a late starter still has time.
        for suffix in ('','&p=1&download=1'):
            self.assertEqual(self.get(self.url+suffix)[0],403)

    def test_no_path_query_or_personal_file_access(self):
        for suffix in ('&p=99','&p=../1','&p=4&download=1','&download=1'):
            status,_,body=self.get(self.url+suffix)
            self.assertEqual(status,404); self.assertNotIn(b'PRIVATE PERSONAL INFO',body)
        # Supplying f cannot change the server-selected judged source.
        status,_,body=self.get(self.url+'&p=1&download=1&f=secret.txt')
        self.assertEqual(status,200); self.assertEqual(body,self.src)
        result=store.load_results(self.cid)[self.kh]
        for malicious in ('../../admin_key.txt',self.rel.replace('sum.cpp','secret.txt')):
            result['problems']['T1']['file']=malicious
            store.put_result(self.cid,self.kh,result)
            self.assertEqual(self.get(self.url+'&p=1&download=1')[0],404)

    def test_tokens_cannot_select_a_login_number_or_different_contest(self):
        for token in (self.kh,'f'*32,public_sources.student_token('other',self.kh)):
            path='/scoreboard?'+urllib.parse.urlencode({'c':self.cid,'source':token})
            self.assertEqual(self.get(path)[0],404)

    def test_scored_ioi_file_wins_over_last_upload_and_legacy_prefix_works(self):
        result=store.load_results(self.cid)[self.kh]
        picked=Path(store.upload_dir(self.cid,self.kh))/'new/sum.cpp'
        picked.parent.mkdir(parents=True,exist_ok=True);picked.write_bytes(b'WRONG LATEST CODE')
        result['picked']={'1':'new/sum.cpp'}
        result['problems']['T1']['file']=self.kh+'/'+self.rel
        store.put_result(self.cid,self.kh,result)
        store.save_contests([{**self.contest,'rule':'IOI'}])
        self.assertEqual(self.get(self.url+'&p=1&download=1')[2],self.src)

    def test_pending_and_removed_roster_entries_never_expose_sources(self):
        result=store.load_results(self.cid)[self.kh]
        store.put_result(self.cid,self.kh,{**result,'judging':True})
        self.assertEqual(self.get(self.url)[0],404)
        store.put_result(self.cid,self.kh,result);store.save_roster(self.cid,{})
        self.assertEqual(self.get(self.url)[0],404)

    def test_ioi_without_a_best_source_record_does_not_show_latest_upload(self):
        result=store.load_results(self.cid)[self.kh]
        result['problems']['T1'].pop('file')
        store.put_result(self.cid,self.kh,result)
        store.save_contests([{**self.contest,'rule':'IOI'}])
        self.assertEqual(self.get(self.url+'&p=1&download=1')[0],404)

    def test_admin_switch_requires_auth_and_changes_no_clock_or_score(self):
        before=store.load_results(self.cid)
        self.assertEqual(self.get('/admin/release?c='+self.cid,form={'public_sources':'0'})[0],403)
        self.assertTrue(store.get_contest(self.cid)['public_sources'])
        status,_,_=self.get('/admin/release?c='+self.cid,form={'public_sources':'0'},admin=True)
        self.assertEqual(status,302);self.assertFalse(store.get_contest(self.cid)['public_sources'])
        self.assertEqual(store.load_results(self.cid),before)
        self.assertEqual(attempts.all_for(self.cid),{})
        self.assertEqual(self.get(self.url)[0],403)


if __name__ == '__main__': unittest.main()
