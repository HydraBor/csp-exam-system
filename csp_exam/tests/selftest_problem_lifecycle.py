"""Permanent deletion must not orphan metadata after failed file cleanup."""
from pathlib import Path
import json,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from csp_exam.core import database,judgelocal,localoj,problems,store
from csp_exam.web.server import Handler

class PurgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patches=[patch.object(store,'DATA_DIR',str(self.root)),patch.object(judgelocal,'PROBLEMS_DIR',str(self.root/'problems'))]
        for p in self.patches:p.start()
        self.pid='Q-delete'
        result=problems.create_problem(self.pid,'删除回归题',{'1.in':b'2 3\n','1.out':b'5\n'},statement='# 题面',name='sum',time_ms=1000,memory_mb=64)
        self.assertTrue(result['ok']);problems.set_deleted(self.pid)
        problems.save_samples(self.pid,[[{'label':'大样例','in':'big.in','out':'big.out'}],[]],{'big.in':b'9 8\n','big.out':b'17\n'})
        self.handler=Handler.__new__(Handler);self.responses=[]
        self.handler._finish_problem_action=lambda key,msg,ok=True:self.responses.append({'ok':ok,'message':msg})
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def state(self):
        return {'info':problems.load_problem_info(),'codes':problems.load_codes(),'statement':localoj.problem_statement(self.pid),
                'samples':database.load(str(self.root),'samples/'+self.pid+'/manifest.json',None),
                'files':{str(p.relative_to(self.root)):p.read_bytes() for folder in ['problems','samples','statements'] for p in (self.root/folder).rglob('*') if p.is_file()}}
    def test_file_error_keeps_registry_statement_and_samples(self):
        before=self.state()
        with patch.object(judgelocal,'drop_problem_data',side_effect=PermissionError('test denied')):
            self.handler._purge_problem(self.pid,'')
        self.assertFalse(self.responses[-1]['ok'])
        self.assertEqual(self.state(),before)
    def test_sample_error_restores_formal_files(self):
        before=self.state()
        with patch.object(problems,'drop_problem_cache',side_effect=OSError('sample cleanup failed')):
            self.handler._purge_problem(self.pid,'')
        self.assertFalse(self.responses[-1]['ok'])
        self.assertEqual(self.state(),before)
    def test_partial_file_removal_restores_all_files(self):
        before=self.state()
        def partial(pid):
            (Path(judgelocal.cases_dir(pid))/'1.in').unlink()
            raise PermissionError('failed halfway')
        with patch.object(judgelocal,'drop_problem_data',side_effect=partial):
            self.handler._purge_problem(self.pid,'')
        self.assertFalse(self.responses[-1]['ok']);self.assertEqual(self.state(),before)
    def test_sqlite_error_is_reported_and_restored(self):
        import sqlite3
        before=self.state()
        with patch.object(problems,'save_problem_info',side_effect=sqlite3.OperationalError('database locked')):
            self.handler._purge_problem(self.pid,'')
        self.assertFalse(self.responses[-1]['ok']);self.assertEqual(self.state(),before)
    def test_unknown_problem_is_not_reported_as_deleted(self):
        before=self.state();self.handler._purge_problem('unknown','')
        self.assertFalse(self.responses[-1]['ok']);self.assertEqual(self.state(),before)
    def test_database_error_restores_all_data(self):
        before=self.state()
        with patch.object(problems,'save_problem_info',side_effect=OSError('database error')):
            self.handler._purge_problem(self.pid,'')
        self.assertFalse(self.responses[-1]['ok']);self.assertEqual(self.state(),before)
    def test_success_clears_all_live_and_legacy_problem_content(self):
        legacy=self.root/'statements'/f'{self.pid}.md';legacy.parent.mkdir(exist_ok=True);legacy.write_text('# 旧题面')
        self.handler._purge_problem(self.pid,'')
        self.assertTrue(self.responses[-1]['ok']);self.assertNotIn(self.pid,localoj.problem_pids())
        self.assertEqual(localoj.problem_statement(self.pid),'')
        self.assertFalse(legacy.exists());self.assertFalse((self.root/'samples'/self.pid).exists())
        self.assertFalse(database.exists(str(self.root),'samples/'+self.pid+'/manifest.json'))
        with database.connection(self.root) as db:
            self.assertIsNone(db.execute('SELECT id FROM problem_library WHERE id=?',(self.pid,)).fetchone())
    def test_in_use_preserves_problem_and_old_results(self):
        cid=store.create_contest('引用检查','CSP')['id'];store.save_exam(cid,{'problems':[{'no':1,'pid':self.pid,'name':'sum','full':100}]})
        store.save_results(cid,{'K':{'total':100}});before=self.state()
        self.handler._purge_problem(self.pid,'')
        self.assertFalse(self.responses[-1]['ok']);self.assertEqual(self.state(),before)
        self.assertEqual(store.load_results(cid),{'K':{'total':100}})
    def test_invalid_identifier_preserves_all_files(self):
        before=self.state();self.handler._purge_problem('../outside','')
        self.assertFalse(self.responses[-1]['ok']);self.assertEqual(self.state(),before)
    def test_success_keeps_other_problem_with_same_prefix(self):
        other=self.pid+'-next';result=problems.create_problem(other,'另一题',{'1.in':b'1\n','1.out':b'1\n'},statement='# 不变',name='next')
        self.assertTrue(result['ok']);before=judgelocal.cases_of(other)
        self.handler._purge_problem(self.pid,'')
        self.assertTrue(self.responses[-1]['ok']);self.assertEqual(judgelocal.cases_of(other),before)
        self.assertEqual(localoj.problem_statement(other),'# 不变')

if __name__=='__main__':unittest.main()
