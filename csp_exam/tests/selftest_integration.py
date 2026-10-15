"""Cross-feature regressions against isolated SQLite, files and real HTTP routes."""
import http.client
import concurrent.futures
from contextlib import closing
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest.mock import patch

from csp_exam.core import attempts, database, exam_numbers, grading, judgelocal, localoj, problems, scoreboard, security, store, task_queue
from csp_exam.web.server import Handler


class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='csp-integration-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for obj, key, value in [(store, 'DATA_DIR', self.tmp.name),
                                (judgelocal, 'PROBLEMS_DIR', str(self.root/'problems')),
                                (Handler, 'log_message', lambda *a: None)]:
            p = patch.object(obj, key, value); p.start(); self.addCleanup(p.stop)
        self.now = int(time.time())
        self.cid = store.create_contest('待开考', start_at=self.now+3600, end_at=self.now+7200)['id']
        self.g = store.create_group('一班', ['甲', '乙'], campus='南山', teacher='张老师')
        self.enroll(self.cid)

    def enroll(self, cid, g=None):
        g = g or self.g
        return store.assign_kaohaos(cid, g['students'], campus=g['campus'], teacher=g['teacher'],
                                   group_id=g['gid'], group_name=g['name'])

    def server(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), t.join()))
        return server

    def req(self, s, path, payload=None, *, public=False, raw_json=False):
        conn = http.client.HTTPConnection(*s.server_address, timeout=8)
        headers = {} if public else {'Cookie': security.ADMIN_COOKIE+'='+security.make_admin_cookie()}
        if payload is None:
            method, body = 'GET', None
        else:
            method = 'POST'
            body = json.dumps(payload, ensure_ascii=False).encode() if raw_json else urllib.parse.urlencode(payload)
            headers['Content-Type'] = 'application/json' if raw_json else 'application/x-www-form-urlencoded'
        conn.request(method, path, body, headers)
        r = conn.getresponse(); result = r.status, r.read().decode('utf-8')
        conn.close(); return result

    def query(self, s, name='甲', campus='南山', teacher='张老师'):
        status, body = self.req(s, '/api/exam-numbers', dict(name=name,campus=campus,teacher=teacher), public=True, raw_json=True)
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_http_group_change_updates_lookup_print_and_cached_rank(self):
        s = self.server(); before = store.load_roster(self.cid)
        cached = scoreboard.snapshot(self.cid, private=True)
        status, _ = self.req(s, '/admin/groups', dict(action='update', gid=self.g['gid'],
            group_name='二班', names='甲\n乙', campus='广州', teacher='李老师'))
        self.assertEqual(status, 302)
        after = store.load_roster(self.cid)
        self.assertEqual(set(before), set(after))
        for kh, r in before.items():
            for key in ('pw','uname','uid','at'): self.assertEqual(after[kh][key], r[key])
        self.assertEqual(self.query(s)['results'], [])
        row = self.query(s,campus='广州',teacher='李老师')['results'][0]
        self.assertIn(row['kaohao'], before)
        for path in ('/admin/print?c='+self.cid, '/api/scoreboard?private=1&c='+self.cid):
            status, body = self.req(s,path); self.assertEqual(status,200)
            self.assertIn('广州',body); self.assertIn('李老师',body)
        self.assertNotEqual(cached, scoreboard.snapshot(self.cid, private=True))

    def test_group_add_remove_empty_and_add_again_preserve_link(self):
        old = store.load_roster(self.cid); kh = next(k for k,r in old.items() if r['name']=='甲')
        store.update_group(self.g['gid'], names=['甲','丙'])
        current = store.load_roster(self.cid)
        self.assertEqual({r['name'] for r in current.values()}, {'甲','丙'})
        self.assertEqual(current[kh],old[kh])
        store.update_group(self.g['gid'],names=[])
        self.assertEqual(store.load_roster(self.cid),{})
        store.update_group(self.g['gid'],names=['丁'])
        self.assertEqual({r['name'] for r in store.load_roster(self.cid).values()},{'丁'})

    def test_flexible_open_window_can_sync_until_first_attempt(self):
        cid = store.create_contest('自主考试',exam_mode='flexible',duration_min=180,
                                  start_at=self.now-10,end_at=self.now+3000)['id']
        self.enroll(cid);store.update_group(self.g['gid'],teacher='李老师')
        before = store.load_roster(cid)
        self.assertEqual({r['teacher'] for r in before.values()},{'李老师'})
        attempts.start(cid,next(iter(before)))
        clocks = attempts.all_for(cid)
        store.update_group(self.g['gid'],teacher='王老师',names=['甲','丙'])
        self.assertEqual(store.load_roster(cid),before)
        self.assertEqual(attempts.all_for(cid),clocks)

    def test_fixed_running_ended_submitted_and_source_bearing_are_frozen(self):
        cids = [store.create_contest('进行中',start_at=self.now-100,end_at=self.now+100)['id'],
                store.create_contest('已结束',start_at=1,end_at=2)['id'],
                store.create_contest('已有结果',start_at=self.now+100,end_at=self.now+200)['id'],
                store.create_contest('遗留源码',start_at=self.now+100,end_at=self.now+200)['id']]
        for cid in cids:self.enroll(cid)
        store.save_results(cids[2],{'old':{'total':1}})
        source=self.root/'contests'/cids[3]/'uploads'/'old.cpp';source.parent.mkdir(parents=True);source.write_text('source')
        before = {cid:store.load_roster(cid) for cid in cids}
        store.update_group(self.g['gid'],campus='新校区',names=['新同学'])
        self.assertEqual({cid:store.load_roster(cid) for cid in cids},before)
        self.assertEqual(source.read_text(),'source')

    def test_shared_group_divergence_keeps_other_identity(self):
        other=store.create_group('二班',['甲'],campus='南山',teacher='张老师');self.enroll(self.cid,other)
        old=next(k for k,r in store.load_roster(self.cid).items() if r['name']=='甲')
        store.update_group(self.g['gid'],campus='广州')
        rows=store.load_roster(self.cid)
        self.assertEqual((rows[old]['name'],rows[old]['campus']),('甲','南山'))
        self.assertEqual(rows[old]['group_ids'],[other['gid']])
        self.assertEqual(len(rows),3)
        self.assertEqual(len([r for r in rows.values() if r['name']=='甲' and r['campus']=='广州']),1)

    def test_manual_enrollment_survives_group_withdrawal(self):
        store.assign_kaohaos(self.cid,['甲'],campus='南山',teacher='张老师')
        store.update_group(self.g['gid'],names=['乙'])
        rows=store.load_roster(self.cid)
        self.assertEqual({r['name'] for r in rows.values()},{'甲','乙'})
        self.assertEqual(next(r for r in rows.values() if r['name']=='甲')['group_ids'],[])

    def test_groups_with_equal_names_keep_labels_paired_with_ids(self):
        other=store.create_group(self.g['name'],['甲'],campus='南山',teacher='张老师');self.enroll(self.cid,other)
        row=next(r for r in store.load_roster(self.cid).values() if r['name']=='甲')
        self.assertEqual(row['group_ids'],[self.g['gid'],other['gid']])
        self.assertEqual(row['group_names'],['一班','一班'])
        store.update_group(self.g['gid'],name='改名班')
        row=next(r for r in store.load_roster(self.cid).values() if r['name']=='甲')
        self.assertEqual(dict(zip(row['group_ids'],row['group_names'])),{self.g['gid']:'改名班',other['gid']:'一班'})

    def test_explicit_contest_correction_is_not_overwritten(self):
        s=self.server();kh=next(k for k,r in store.load_roster(self.cid).items() if r['name']=='甲')
        status,_=self.req(s,'/admin/roster-affiliation?c='+self.cid,
            dict(kaohao=kh,name='更正姓名',campus='指定校区',teacher='指定老师'))
        self.assertEqual(status,302)
        store.update_group(self.g['gid'],campus='广州',teacher='李老师')
        self.assertEqual(self.query(s,name='更正姓名',campus='指定校区',teacher='指定老师')['results'][0]['kaohao'],kh)
        self.assertEqual(self.query(s)['results'],[])
        self.assertEqual(len(store.load_roster(self.cid)),2)

    def test_legacy_contest_correction_without_marker_is_preserved(self):
        rows=store.load_roster(self.cid);kh=next(k for k,r in rows.items() if r['name']=='甲')
        rows[kh].update(campus='原来单独更正的校区',teacher='指定老师')
        store.save_roster(self.cid,rows)  # Old version did not record an override marker.
        store.update_group(self.g['gid'],campus='广州',teacher='李老师')
        current=store.load_roster(self.cid)
        self.assertEqual((current[kh]['campus'],current[kh]['teacher']),('原来单独更正的校区','指定老师'))
        self.assertTrue(current[kh]['group_sync_exempt']);self.assertEqual(len(current),2)

    def test_name_correction_after_start_keeps_clock_source_and_score(self):
        cid=store.create_contest('个人计时',exam_mode='flexible',duration_min=180,
                                 start_at=self.now-10,end_at=self.now+1000)['id']
        self.enroll(cid);rows=store.load_roster(cid);kh=next(iter(rows));attempts.start(cid,kh)
        clock=attempts.all_for(cid);store.save_results(cid,{kh:{'total':42}})
        file=Path(store.upload_dir(cid,kh))/'sum.cpp';file.parent.mkdir(parents=True);file.write_bytes(b'code')
        changed=store.update_roster_affiliation(cid,kh,name='新姓名',campus='新校区',teacher='新老师')
        self.assertEqual(changed['uname'],rows[kh]['uname']);self.assertEqual(clock,attempts.all_for(cid))
        self.assertEqual(store.load_results(cid),{kh:{'total':42}});self.assertEqual(file.read_bytes(),b'code')

    def test_group_transaction_failure_rolls_back_all_contests(self):
        cid=store.create_contest('第二场')['id'];self.enroll(cid)
        original=store.save_roster;before={c:store.load_roster(c) for c in (self.cid,cid)}
        def fail(c,rows):
            if c==cid:raise OSError('injected write failure')
            return original(c,rows)
        with patch.object(store,'save_roster',side_effect=fail),self.assertRaises(OSError):
            store.update_group(self.g['gid'],campus='新校区')
        self.assertEqual(store.get_group(self.g['gid']),self.g)
        self.assertEqual({c:store.load_roster(c) for c in before},before)

    def test_group_ids_are_not_reused_after_deletion(self):
        gid=store.create_group('旧组',[])['gid'];store.delete_group(gid)
        self.assertNotEqual(store.create_group('新组',[])['gid'],gid)
        self.assertTrue(store.delete_group(self.g['gid']))
        before=store.load_roster(self.cid)
        store.create_group('其他组',['甲'],campus='广州',teacher='李老师')
        self.assertEqual(store.load_roster(self.cid),before)

    def test_historical_exam_numbers_never_get_overwritten(self):
        kh='GD-S00001';old={'name':'历史学生','at':'2000-01-01','pw':'old','uname':'old','uid':11}
        store.save_roster(self.cid,{kh:old})
        with patch.object(store._RNG,'randint',side_effect=[1,2]):
            rows=store.assign_kaohaos(self.cid,['新增学生'])
        self.assertEqual(rows[kh],old);self.assertEqual(len(rows),2)
        self.assertEqual(store._taken_numbers({'GD-0001':{}}),{1})

    def test_number_capacity_counts_existing_enrollments(self):
        with self.assertRaises(ValueError):store._random_numbers(2,set(range(1,9)),width=1)

    def test_group_identity_collision_is_rejected_instead_of_reassigning_number(self):
        store.assign_kaohaos(self.cid,['甲'],campus='广州',teacher='张老师')
        before=store.load_roster(self.cid)
        with self.assertRaises(ValueError):store.update_group(self.g['gid'],campus='广州')
        self.assertEqual(store.load_roster(self.cid),before)
        self.assertEqual(store.get_group(self.g['gid']),self.g)

    def test_orphan_queued_job_also_freezes_group_sync(self):
        before=store.load_roster(self.cid);task_queue.put(self.cid,'orphan',{}, {},'old')
        store.update_group(self.g['gid'],campus='新校区')
        self.assertEqual(store.load_roster(self.cid),before)

    def test_start_and_group_edit_are_serialized_without_losing_clock(self):
        cid=store.create_contest('并发开考',exam_mode='flexible',duration_min=180,
                                start_at=self.now-10,end_at=self.now+1000)['id']
        self.enroll(cid);kh=next(iter(store.load_roster(cid)));barrier=threading.Barrier(2)
        def start():barrier.wait();return attempts.start(cid,kh)
        def edit():barrier.wait();return store.update_group(self.g['gid'],campus='广州')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            begin=pool.submit(start);change=pool.submit(edit);clock=begin.result();change.result()
        self.assertEqual(attempts.get(cid,kh),clock)
        rows=store.load_roster(cid)
        self.assertIn(kh,rows)
        self.assertIn({r['campus'] for r in rows.values()},({'南山'},{'广州'}))
        before=store.load_roster(cid);store.update_group(self.g['gid'],teacher='王老师')
        self.assertEqual(store.load_roster(cid),before)

    def test_lookup_reads_external_commits_without_restarting(self):
        s=self.server();self.assertTrue(self.query(s)['results'])
        with closing(sqlite3.connect(self.root/database.FILENAME)) as db, db:
            db.execute("UPDATE records SET payload=json_set(payload,'$.campus','实时校区') WHERE document_key=?",('contests/'+self.cid+'/roster.json',))
        self.assertEqual(self.query(s)['results'],[])
        self.assertEqual(len(self.query(s,campus='实时校区')['results']),1)

    def test_create_contest_and_publish_are_atomic(self):
        before=store.list_contests();original=store._save
        def fail(path,data):
            if str(path).endswith('results.json'):raise OSError('injected create failure')
            return original(path,data)
        with patch.object(store,'_save',side_effect=fail),self.assertRaises(OSError):store.create_contest('失败场')
        self.assertEqual(store.list_contests(),before)
        store.update_contest(self.cid,start_at=1,end_at=2)
        store.set_released(self.cid,True)
        published=(store.load_exam(self.cid),store.get_contest(self.cid))
        def fail_index(items):raise OSError('injected publish failure')
        with patch.object(store,'save_contests',side_effect=fail_index),self.assertRaises(OSError):
            store.set_released(self.cid,False)
        self.assertEqual((store.load_exam(self.cid),store.get_contest(self.cid)),published)

    def ended_with_files(self):
        store.update_contest(self.cid,start_at=1,end_at=2)
        file=self.root/'contests'/self.cid/'uploads'/'saved.cpp';file.parent.mkdir(parents=True);file.write_bytes(b'real source')
        with database.connection(store.DATA_DIR) as db:
            db.execute('INSERT INTO exam_attempts VALUES(?,?,?,?)',(self.cid,next(iter(store.load_roster(self.cid))),1,2))
        return file

    def test_contest_delete_clears_jobs_clocks_query_and_cache(self):
        file=self.ended_with_files();scoreboard.snapshot(self.cid,private=True)
        task_queue.put(self.cid,'orphan',{}, {},'old')
        task_queue.put(self.cid+'0','other',{}, {},'keep')
        self.assertTrue(store.delete_contest(self.cid))
        self.assertFalse(file.parent.parent.exists());self.assertEqual(attempts.all_for(self.cid),{})
        self.assertFalse(exam_numbers.lookup({'name':'甲'})['matched'])
        self.assertFalse(scoreboard.snapshot(self.cid,private=True)['ok'])
        self.assertEqual(task_queue.pending(),1)
        self.assertEqual(task_queue.claim()[1][0],self.cid+'0')
        self.assertFalse(store.delete_contest(self.cid))

    def test_delete_partial_file_failure_restores_db_files_and_queue(self):
        file=self.ended_with_files();before=store.load_roster(self.cid)
        task_queue.put(self.cid,'orphan',{}, {},'old')
        original=shutil.rmtree
        def fail(path,*args,**kwargs):
            if Path(path)==file.parent.parent:
                file.unlink();raise PermissionError('injected partial removal')
            return original(path,*args,**kwargs)
        with patch.object(shutil,'rmtree',side_effect=fail),self.assertRaises(PermissionError):store.delete_contest(self.cid)
        self.assertEqual(file.read_bytes(),b'real source');self.assertEqual(store.load_roster(self.cid),before)
        self.assertIsNotNone(store.get_contest(self.cid));self.assertEqual(task_queue.pending(),1)
        self.assertTrue(attempts.all_for(self.cid))
        self.assertFalse(list(self.root.glob('.contest-delete-*')))

    def test_delete_db_failure_keeps_everything_and_running_job_is_refused(self):
        file=self.ended_with_files();task_queue.put(self.cid,'orphan',{}, {},'old')
        with patch.object(database,'delete',side_effect=OSError('injected SQLite failure')),self.assertRaises(OSError):
            store.delete_contest(self.cid)
        self.assertEqual(file.read_bytes(),b'real source');self.assertIsNotNone(store.get_contest(self.cid))
        self.assertEqual(task_queue.pending(),1);task_queue.claim()
        with self.assertRaises(ValueError):store.delete_contest(self.cid)
        self.assertTrue(file.exists())

    def test_problem_metadata_refreshes_exam_statement_limits_and_score_gate(self):
        s=self.server();pid='audit-problem'
        created=problems.create_problem(pid,'原题名',{'1.in':b'1\n','1.out':b'1\n'},
            name='old',statement='## 题目描述\n\n原文',time_ms=1000,memory_mb=64)
        self.assertTrue(created['ok'])
        store.save_exam(self.cid,{'problems':[{'pid':pid,'no':1,'name':'old','title':'原题名','full':100}]})
        status,_=self.req(s,'/admin/problem-detail?pid='+pid,dict(title='更新题名',name='new',time_ms='2500',memory_mb='128',statement='## 题目描述\n\n更新正文 $a_i$'))
        self.assertEqual(status,302)
        p=store.load_exam(self.cid)['problems'][0]
        self.assertEqual((p['title'],p['name']),('更新题名','new'))
        self.assertEqual(judgelocal.limits_of(pid),(2500,128))
        self.assertIn('更新正文',localoj.problem_statement(pid))
        self.assertEqual(len(judgelocal.cases_of(pid)),1)
        for path in ('/admin/problems','/api/problems','/admin/problem-detail?pid='+pid):
            status,body=self.req(s,path);self.assertEqual(status,200);self.assertIn('更新题名',body)
        # Renaming the inherited contract and writing metadata must be one operation.
        with patch.object(problems,'save_problem_info',side_effect=OSError('injected metadata failure')),self.assertRaises(OSError):
            problems.remember_problem(pid,name='another')
        self.assertEqual(store.load_exam(self.cid)['problems'][0]['name'],'new')
        self.assertEqual(problems.load_problem_info()[pid]['name'],'new')

    def test_failed_problem_edit_does_not_partially_change_statement(self):
        s=self.server();pid='atomic-problem'
        self.assertTrue(problems.create_problem(pid,'原题名',{'1.in':b'1','1.out':b'1'},
            name='sum',statement='原题面',time_ms=1000,memory_mb=64)['ok'])
        before=(problems.load_codes(),problems.load_problem_info())
        status,_=self.req(s,'/admin/problem-detail?pid='+pid,dict(title='新标题',name='INVALID',
                            time_ms='2500',memory_mb='128',statement='不应保存的新题面'))
        self.assertEqual(status,302)
        self.assertEqual(localoj.problem_statement(pid),'原题面')
        self.assertEqual((problems.load_codes(),problems.load_problem_info()),before)
        with patch.object(problems,'save_problem_info',side_effect=OSError('injected write failure')):
            status,_=self.req(s,'/admin/problem-detail?pid='+pid,dict(title='新标题',name='sum',
                                time_ms='2500',memory_mb='128',statement='不应保存的新题面'))
        self.assertEqual(status,302);self.assertEqual(localoj.problem_statement(pid),'原题面')
        self.assertEqual((problems.load_codes(),problems.load_problem_info()),before)

    def test_upload_queue_failure_preserves_previous_receipt_and_source(self):
        s=self.server();cid=self.cid
        store.update_contest(cid,start_at=self.now-100,end_at=self.now+100)
        store.save_exam(cid,{'problems':[{'no':1,'pid':'P','name':'sum','full':100}]})
        kh=next(iter(store.load_roster(cid)));previous={'submission_id':'previous','total':75,'submission_state':'done'}
        store.save_results(cid,{kh:previous})
        old=Path(store.upload_dir(cid,kh))/'old.cpp';old.parent.mkdir(parents=True);old.write_bytes(b'previous code')
        boundary='IntegrationUploadBoundary'
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{kh}/sum/sum.cpp"\r\n\r\nint main(){{}}\r\n--{boundary}--\r\n').encode()
        conn=http.client.HTTPConnection(*s.server_address,timeout=8)
        with patch.object(grading,'enqueue_submission',side_effect=OSError('injected queue failure')):
            conn.request('POST','/upload?c='+cid,body,{'Content-Type':'multipart/form-data; boundary='+boundary,
                'Cookie':'csp='+security.make_cookie(cid,kh)})
            response=conn.getresponse();self.assertEqual(response.status,400);response.read()
        conn.close()
        self.assertEqual(store.load_results(cid)[kh],previous);self.assertEqual(old.read_bytes(),b'previous code')
        self.assertFalse(list((old.parent/'__submissions').glob('*')))


if __name__=='__main__':unittest.main()
