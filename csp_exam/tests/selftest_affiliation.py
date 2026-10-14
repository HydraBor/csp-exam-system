"""Group snapshots, namesake enrollment, correction permissions and rank filters."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import attempts, judgelocal, scoreboard, security, store
from csp_exam.web.server import Handler


class Affiliation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='csp-affiliation-')
        self.addCleanup(self.temp.cleanup)
        for obj, key, val in ((store, 'DATA_DIR', self.temp.name),
                              (judgelocal, 'PROBLEMS_DIR', self.temp.name+'/problems'),
                              (Handler, 'log_message', lambda *a: None)):
            p = patch.object(obj, key, val); p.start(); self.addCleanup(p.stop)
        self.c = store.create_contest('联赛', rule='OI', start_at=1, end_at=2)
        self.cid = self.c['id']
        store.save_exam(self.cid, {'problems': [{'no': 1, 'pid': 'A', 'name': 'sum', 'full': 100}], 'released': True})
        self.g = store.create_group('南山提高班', ['同学甲', '同学乙'], campus='南山', teacher='张老师')

    def enroll(self, g=None):
        g = g or self.g
        return store.assign_kaohaos(self.cid, g['students'], campus=g.get('campus',''), teacher=g.get('teacher',''),
                                   group_id=g['gid'], group_name=g['name'])

    def server(self):
        s = ThreadingHTTPServer(('127.0.0.1', 0), Handler); s.daemon_threads=True
        t = threading.Thread(target=s.serve_forever,daemon=True);t.start()
        def stop():s.shutdown();s.server_close();t.join()
        self.addCleanup(stop)
        return s

    def request(self, s, path, form=None, admin=True):
        conn=http.client.HTTPConnection(*s.server_address,timeout=5)
        headers={'Cookie': security.ADMIN_COOKIE+'='+security.make_admin_cookie() if admin else ''}
        if form is not None: headers['Content-Type']='application/x-www-form-urlencoded'
        conn.request('POST' if form is not None else 'GET',path,urllib.parse.urlencode(form) if form is not None else None,headers)
        response=conn.getresponse();status,body=response.status,response.read();conn.close()
        return status,body

    def test_group_uniformity_and_historical_snapshot(self):
        original=self.enroll()
        self.assertEqual({(r['campus'],r['teacher']) for r in original.values()},{('南山','张老师')})
        store.update_group(self.g['gid'],campus='广州',teacher='李老师')
        self.assertEqual(store.load_roster(self.cid),original)
        changed=store.get_group(self.g['gid']);roster=self.enroll(changed)
        self.assertEqual(len(roster),4)
        self.assertEqual({(r['campus'],r['teacher']) for r in roster.values()},{('南山','张老师'),('广州','李老师')})

    def test_each_identity_component_and_idempotent_import(self):
        store.assign_kaohaos(self.cid,['同名'],campus='南山',teacher='张老师')
        before=store.load_roster(self.cid)
        self.assertEqual(store.assign_kaohaos(self.cid,['同名'],campus='南山',teacher='张老师'),before)
        store.assign_kaohaos(self.cid,['同名'],campus='广州',teacher='张老师')
        store.assign_kaohaos(self.cid,['同名'],campus='南山',teacher='李老师')
        self.assertEqual(len(store.load_roster(self.cid)),3)
        self.assertEqual(len({r['uname'] for r in store.load_roster(self.cid).values()}),3)

    def test_shared_identity_in_two_groups_keeps_one_account(self):
        roster=self.enroll();keys=set(roster)
        other=store.create_group('周末班',['同学甲'],campus='南山',teacher='张老师')
        self.enroll(other)
        self.assertEqual(set(store.load_roster(self.cid)),keys)
        row=next(r for r in store.load_roster(self.cid).values() if r['name']=='同学甲')
        self.assertEqual(set(row['group_ids']),{self.g['gid'],other['gid']})

    def test_reshuffle_preserves_namesakes_and_affiliation(self):
        self.enroll();store.assign_kaohaos(self.cid,['同学甲'],campus='广州',teacher='张老师')
        before={store.student_identity(r) for r in store.load_roster(self.cid).values()}
        changed=store.reshuffle_kaohaos(self.cid)
        self.assertEqual({store.student_identity(r) for r in changed.values()},before)
        self.assertEqual(len(changed),3)
        self.assertTrue(all(r['group_ids'] for r in changed.values() if r['campus']=='南山'))

    def test_correction_after_start_preserves_clock_and_scores(self):
        c=store.create_contest('进行中的个人考试',rule='OI',exam_mode='flexible',duration_min=180,start_at=time.time()-10,end_at=time.time()+100)
        roster=store.assign_kaohaos(c['id'],['甲'],campus='南山',teacher='张老师');kh=next(iter(roster))
        attempts.start(c['id'],kh);before=attempts.all_for(c['id'])
        score={'problems':{'T1':{'score':85}},'submitted_at':'now'}
        store.save_results(c['id'],{kh:score})
        updated=store.update_roster_affiliation(c['id'],kh,campus='广州',teacher='李老师')
        self.assertEqual(updated['uname'],roster[kh]['uname'])
        self.assertEqual(attempts.all_for(c['id']),before)
        self.assertEqual(store.load_results(c['id']),{kh:score})
        with self.assertRaises(ValueError):store.remove_from_roster(c['id'],[kh])
        with self.assertRaises(ValueError):store.replace_roster(c['id'],['新学生'])

    def test_correction_is_contest_specific_and_collision_is_rejected(self):
        roster=self.enroll();kh=next(iter(roster))
        old_group=store.get_group(self.g['gid'])
        second=store.create_contest('另一场');store.assign_kaohaos(second['id'],['同学甲'],campus='南山',teacher='张老师')
        second_before=store.load_roster(second['id'])
        store.update_roster_affiliation(self.cid,kh,campus='广州',teacher='李老师')
        self.assertEqual(store.get_group(self.g['gid']),old_group)
        self.assertEqual(store.load_roster(second['id']),second_before)
        name=roster[kh]['name'];store.assign_kaohaos(self.cid,[name],campus='南山',teacher='张老师')
        with self.assertRaises(ValueError):store.update_roster_affiliation(self.cid,kh,campus='南山',teacher='张老师')

    def test_legacy_records_are_not_rewritten_and_display_empty_values(self):
        store._save(store._cp(self.cid,'roster.json'),{'GD-J12345':{'name':'旧名单'}})
        before=store.load_roster(self.cid)
        public=scoreboard.snapshot(self.cid)
        self.assertEqual(store.load_roster(self.cid),before)
        self.assertEqual((public['rows'][0]['campus'],public['rows'][0]['teacher']),('',''))
        s=self.server();status,body=self.request(s,'/scoreboard?c='+self.cid,admin=False)
        self.assertEqual(status,200);self.assertIn('未填写',body.decode())

    def test_group_http_create_update_and_import_snapshot(self):
        s=self.server()
        form={'action':'create','group_name':'广州提高班','names':'重名\n学生乙','campus':'广州','teacher':'李老师'}
        self.assertEqual(self.request(s,'/admin/groups',form)[0],302)
        g=store.load_groups()[-1]
        self.assertEqual((g['campus'],g['teacher']),('广州','李老师'))
        self.assertEqual(self.request(s,'/admin/roster?c='+self.cid,{'gid':g['gid'],'mode':'append'})[0],302)
        before=store.load_roster(self.cid)
        self.assertEqual(self.request(s,'/admin/groups',{'action':'update','gid':g['gid'],'group_name':g['name'],'names':'重名\n学生乙','campus':'新校区','teacher':'王老师'})[0],302)
        self.assertEqual(store.load_roster(self.cid),before)
        status,body=self.request(s,'/admin?c='+self.cid)
        self.assertEqual(status,200);self.assertIn('更正本场学生归属',body.decode())
        self.assertIn('data-campus="广州"',body.decode())

    def test_roster_correction_authentication_and_unknown_student(self):
        s=self.server();path='/admin/roster-affiliation?c='+self.cid
        self.enroll();kh=next(iter(store.load_roster(self.cid)));before=store.load_roster(self.cid)
        self.assertEqual(self.request(s,path,{'kaohao':kh,'campus':'广州','teacher':'李老师'},admin=False)[0],403)
        self.assertEqual(store.load_roster(self.cid),before)
        self.assertEqual(self.request(s,path,{'kaohao':'missing','campus':'广州'})[0],400)
        self.assertEqual(self.request(s,path,{'kaohao':kh,'campus':'广州','teacher':'李老师'})[0],302)
        self.assertEqual(store.load_roster(self.cid)[kh]['campus'],'广州')

    def test_new_labels_respect_zero_feedback_and_private_projection(self):
        self.enroll();s=self.server();store.save_exam(self.cid,{'problems':[],'released':False})
        status,body=self.request(s,'/api/scoreboard?c='+self.cid,admin=False)
        self.assertEqual(status,403);self.assertNotIn('南山',body.decode());self.assertNotIn('张老师',body.decode())
        status,body=self.request(s,'/api/scoreboard?c='+self.cid+'&private=1')
        state=json.loads(body);self.assertEqual(status,200)
        self.assertEqual({r['campus'] for r in state['rows']},{'南山'})
        for row in state['rows']:self.assertNotIn('pw',row);self.assertNotIn('group_ids',row)

    def test_label_correction_changes_scoreboard_etag(self):
        self.enroll();s=self.server();kh=next(iter(store.load_roster(self.cid)))
        before=scoreboard.snapshot(self.cid)
        store.update_roster_affiliation(self.cid,kh,campus='广州',teacher='李老师')
        after=scoreboard.snapshot(self.cid)
        self.assertNotEqual(before,after)
        status,body=self.request(s,'/api/scoreboard?c='+self.cid,admin=False)
        self.assertEqual(status,200);self.assertIn('广州',body.decode())
        self.assertTrue(all('kaohao' not in r and 'pw' not in r for r in json.loads(body)['rows']))

    def test_validation_and_html_escaping(self):
        with self.assertRaises(ValueError):store.create_group('bad',['甲'],campus='x'*101)
        with self.assertRaises(ValueError):store.update_group(self.g['gid'],teacher='坏\n名字')
        s=self.server();label='<img src=x onerror=alert(1)>'
        store.update_group(self.g['gid'],campus=label,teacher="O'Reilly")
        status,body=self.request(s,'/admin/groups')
        self.assertEqual(status,200);text=body.decode()
        self.assertIn('&lt;img',text);self.assertNotIn('<img src=x',text)

    def test_printed_namesakes_show_affiliation_and_public_https_link(self):
        self.enroll();store.assign_kaohaos(self.cid,['同学甲'],campus='广州',teacher='李老师')
        s=self.server()
        with patch('csp_exam.config.PUBLIC_ORIGIN','https://csp.example.test'):
            status,body=self.request(s,'/admin/print?c='+self.cid)
        text=body.decode();self.assertEqual(status,200)
        self.assertIn('<th>校区</th><th>指导老师</th>',text)
        self.assertIn('南山',text);self.assertIn('广州',text);self.assertIn('李老师',text)
        self.assertIn('https://csp.example.test/enter?c='+self.cid,text)
        self.assertNotIn('http://csp.example.test:',text)

    def test_mutable_filter_script_revalidates_cache(self):
        s=self.server();conn=http.client.HTTPConnection(*s.server_address,timeout=5)
        conn.request('GET','/static/scoreboard-filters.js');response=conn.getresponse()
        self.assertEqual(response.status,200)
        self.assertEqual(response.getheader('Cache-Control'),'public, max-age=0, must-revalidate')
        response.read();conn.close()

    def test_filtering_and_competition_ties_in_actual_javascript(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node.js is required for frontend ranking checks')
        test=Path(__file__).with_name('selftest_scoreboard_filters.js')
        subprocess.run([node,str(test)],check=True,capture_output=True,text=True)


if __name__=='__main__':unittest.main()
