"""Public namesake lookup, private-field projection and unchanged personal clocks."""
import http.client
from http.server import ThreadingHTTPServer
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from csp_exam.core import database, exam_numbers, store, attempts
from csp_exam.web.server import Handler


class ExamNumbers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='csp-number-test-')
        self.addCleanup(self.temp.cleanup)
        for obj, key, val in [(store,'DATA_DIR',self.temp.name), (Handler,'log_message',lambda *a: None)]:
            p=patch.object(obj,key,val);p.start();self.addCleanup(p.stop)
        self.now=int(time.time())
        self.old=store.create_contest('往期考试',rule='OI',start_at=self.now-3000,end_at=self.now-2000)
        self.live=store.create_contest('进行中考试',rule='OI',start_at=self.now-1000,end_at=self.now+1000)
        self.future=store.create_contest('未来自主考试',rule='OI',exam_mode='flexible',duration_min=180,
                                         start_at=self.now+2000,end_at=self.now+4000)
        for c in (self.old,self.live,self.future):
            store.assign_kaohaos(c['id'],['测试学员'],campus='深圳南山',teacher='张老师')
        store.assign_kaohaos(self.live['id'],['测试学员'],campus='深圳南山',teacher='李老师')
        store.assign_kaohaos(self.live['id'],['测试学员'],campus='广州天河',teacher='张老师')
        store.assign_kaohaos(self.old['id'],['历史同学'])
        store.assign_kaohaos(self.live['id'],['其他秘密姓名'],campus='秘密校区',teacher='秘密老师')
        store.save_results(self.live['id'],{'SECRET-RESULT':{'score':99,'source':'SECRET-SOURCE'}})

    def query(self, **fields):
        return exam_numbers.lookup({'name':'测试学员',**fields},now=self.now)

    def test_name_only_has_all_options_and_no_numbers(self):
        result=self.query()
        self.assertEqual(set(result['campuses']),{'深圳南山','广州天河'})
        self.assertEqual(set(result['teachers']),{'张老师','李老师'})
        self.assertTrue(result['selection_required'])
        self.assertEqual(result['results'],[])
        self.assertNotIn('秘密',json.dumps(result,ensure_ascii=False))

    def test_campus_controls_teacher_options_and_invalid_pair_cannot_match(self):
        self.assertEqual(self.query(campus='广州天河')['teachers'],['张老师'])
        self.assertEqual(self.query(campus='广州天河',teacher='李老师')['results'],[])
        self.assertEqual(len(self.query(campus='广州天河',teacher='张老师')['results']),1)

    def test_exact_name_and_trim_and_no_sql_wildcards(self):
        self.assertEqual(self.query(),exam_numbers.lookup({'name':' 测试学员 '},now=self.now))
        for name in ('测试','测试学员甲','%',"' OR 1=1 --"):
            result=self.query(name=name)
            self.assertFalse(result['matched']);self.assertEqual(result['campuses'],[])

    def test_all_contest_phases_are_returned_newest_first(self):
        rows=self.query(campus='深圳南山',teacher='张老师')['results']
        self.assertEqual([r['contest_id'] for r in rows],[self.future['id'],self.live['id'],self.old['id']])
        self.assertEqual([r['status'] for r in rows],['pending','running','ended'])
        self.assertTrue(all(r['entry_url']=='/enter?c='+r['contest_id'] for r in rows))
        self.assertEqual(rows[0]['duration_minutes'],180)

    def test_snapshot_uses_enrollment_not_changed_current_group(self):
        g=store.create_group('原分组',['测试学员'],campus='深圳南山',teacher='张老师')
        store.update_group(g['gid'],campus='新校区',teacher='新老师')
        self.assertEqual(len(self.query(campus='深圳南山',teacher='张老师')['results']),3)
        kh=next(k for k,r in store.load_roster(self.future['id']).items() if r['name']=='测试学员')
        store.update_roster_affiliation(self.future['id'],kh,campus='新校区',teacher='新老师')
        self.assertIn('新校区',self.query()['campuses'])
        self.assertEqual(len(self.query(campus='深圳南山',teacher='张老师')['results']),2)

    def test_missing_legacy_affiliation_is_selectable(self):
        data=self.query(name='历史同学')
        self.assertEqual(data['campuses'],['']);self.assertEqual(data['teachers'],[''])
        self.assertTrue(data['selection_required'])
        self.assertEqual(len(self.query(name='历史同学',campus='',teacher='')['results']),1)

    def test_deleted_and_orphan_contests_are_absent(self):
        old=self.old['id']
        store.save_contests([c for c in store.list_contests() if c['id']!=old])
        self.assertEqual(len(self.query(campus='深圳南山',teacher='张老师')['results']),2)

    def test_legacy_list_roster_is_read_without_migration(self):
        database.save(store.DATA_DIR,'students.json',{'GD-J12345':{'name':'老格式同学','pw':'SECRET-PW'}})
        database.save(store.DATA_DIR,'contests/'+self.old['id']+'/roster.json',['GD-J12345'])
        data=self.query(name='老格式同学',campus='',teacher='')
        self.assertEqual(data['results'][0]['kaohao'],'GD-J12345')
        self.assertIsInstance(database.load(store.DATA_DIR,'contests/'+self.old['id']+'/roster.json',dict),list)
        self.assertNotIn('SECRET-PW',json.dumps(data))

    def test_sensitive_fields_are_never_projected(self):
        result=self.query(campus='深圳南山',teacher='张老师')
        for row in result['results']:
            self.assertFalse({'pw','uname','uid','group_ids','group_names','score','source','results','submitted_at'} & row.keys())
        self.assertNotIn('SECRET-',json.dumps(result))

    def test_personal_clock_and_database_are_unchanged(self):
        def snapshot():
            with database.connection(store.DATA_DIR) as db:
                return list(db.iterdump())
        before=snapshot()
        self.query();self.query(campus='深圳南山',teacher='张老师')
        self.assertEqual(snapshot(),before)
        self.assertEqual(attempts.all_for(self.future['id']),{})

    def test_malformed_requests_are_rejected(self):
        for body in ([],None,{}, {'name':''},{'name':' '*3},{'name':'a'*101},
                     {'name':3},{'name':'甲\n乙'}, {'name':'甲','campus':[]}, {'name':'甲','teacher':None}):
            with self.subTest(body=body),self.assertRaises(ValueError):exam_numbers.lookup(body)

    def http(self, method, path, body=None, content_type='application/json'):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            connection=http.client.HTTPConnection(*server.server_address,timeout=5)
            headers={'Content-Type':content_type}
            connection.request(method,path,body,headers)
            response=connection.getresponse();result=(response.status,dict(response.getheaders()),response.read())
            connection.close();return result
        finally:server.shutdown();server.server_close();worker.join()

    def test_http_anonymous_lookup_and_cache_and_page(self):
        status,headers,body=self.http('POST','/api/exam-numbers',json.dumps({'name':'测试学员'},ensure_ascii=False).encode())
        self.assertEqual(status,200);self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertTrue(json.loads(body)['matched'])
        status,_,body=self.http('GET','/exam-number')
        self.assertEqual(status,200);self.assertIn(b'noindex,nofollow',body)
        self.assertNotIn('测试学员'.encode(),body)

    def test_http_get_and_bad_bodies_and_size_are_rejected(self):
        self.assertEqual(self.http('GET','/api/exam-numbers?name=x')[0],405)
        self.assertEqual(self.http('POST','/api/exam-numbers',b'{')[0],400)
        self.assertEqual(self.http('POST','/api/exam-numbers',b'x'*4097)[0],400)
        self.assertEqual(self.http('POST','/api/exam-numbers',b'name=x','application/x-www-form-urlencoded')[0],415)

    def test_login_includes_lookup_and_escapes_invalid_number(self):
        _,_,body=self.http('GET','/enter?c='+self.future['id'])
        text=body.decode()
        self.assertIn('href="/exam-number"',text);self.assertIn('name="c"',text)
        self.assertIn('登录不开始个人计时',text)
        _,_,body=self.http('POST','/enter',b'c='+self.future['id'].encode()+b'&kaohao=%3Cimg%20src%3Dx%3E','application/x-www-form-urlencoded')
        self.assertNotIn(b'<IMG SRC=X>',body);self.assertIn(b'&lt;IMG SRC=X&gt;',body)

    def test_new_script_revalidates_and_no_client_data_bank(self):
        status,headers,body=self.http('GET','/static/exam-number.js')
        self.assertEqual(status,200)
        self.assertIn('must-revalidate',headers['Cache-Control'])
        self.assertNotIn('测试学员'.encode(),body)


if __name__=='__main__':unittest.main()
