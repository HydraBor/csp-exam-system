"""管理业务边界：失败不损坏题目、改配置撤回成绩、路径与 zip 校验。"""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from csp_exam.core import importer,judgelocal,localoj,problems,store

class Management(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="csp-mgmt-unit-")
        self.patches=[patch.object(store,"DATA_DIR",self.temp.name),patch.object(judgelocal,"PROBLEMS_DIR",self.temp.name+"/problems")]
        for p in self.patches:p.start()
        self.cid=store.create_contest("测试","CSP")["id"]
        store.save_exam(self.cid,{"problems":[{"no":1,"pid":"P","name":"sum","full":100}],"released":False})
        self.files={"1.in":b"2 3\n","1.out":b"5\n"}
        self.assertTrue(problems.create_problem("P","加法",self.files,statement="# 原题面",time_ms=1000,memory_mb=256,name="sum")["ok"])
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def result(self):
        store.save_results(self.cid,{"K":{"submitted_at":"x","problems":{"T1":{"score":100}},"submission_exam":store.load_exam(self.cid),"judge_rule":{"key":"CSP"}}})
    def test_invalid_overwrite_preserves_original(self):
        before=problems.load_codes()
        result=problems.create_problem("P","坏版本",{"1.in":b"broken"},overwrite=True)
        self.assertFalse(result["ok"])
        self.assertEqual(judgelocal.cases_of("P")[0]["out"],b"5\n")
        self.assertEqual(localoj.problem_statement("P"),"# 原题面")
        self.assertEqual(problems.load_codes(),before)

    def test_private_library_export_round_trip(self):
        from csp_exam.tools.export_library import export
        target=Path(self.temp.name).parent/(Path(self.temp.name).name+'-library.zip')
        try:
            self.assertEqual(export(target,['P'])['problems'],1)
            with zipfile.ZipFile(target) as archive:
                config=json.loads(archive.read('P/liteoj-seed.json'))
                self.assertEqual((config['id'],config['timeLimit'],config['csp']['submissionName']),('P',1000,'sum'))
                self.assertEqual(sum(c['score'] for c in config['cases']),100)
                self.assertEqual(archive.read('P/data/1.out'),b'5\n')
                manifest=json.loads(archive.read('library-manifest.json'))
                self.assertIn('P/题目.md',manifest['sha256'])
                files={n:archive.read(n) for n in archive.namelist() if n.startswith('P/')}
            result=problems.create_problem('P','',files,overwrite=True)
            self.assertTrue(result['ok'],result)
            self.assertEqual(localoj.problem_statement('P'),'# 原题面')
            self.assertEqual(problems.name_of_pid('P'),'sum')
        finally:target.unlink(missing_ok=True)
    def test_limits_change_withdraws_and_blocks_release(self):
        self.result();store.set_released(self.cid,True)
        problems.remember_problem("P",time_ms=1200)
        self.assertFalse(store.load_exam(self.cid)["released"])
        self.assertTrue(store.load_results(self.cid)["K"].get("rejudge_error"))
        with self.assertRaises(ValueError):store.set_released(self.cid,True)
    def test_inflight_limits_change_is_rejected(self):
        self.result();r=store.load_results(self.cid);r["K"]["judging"]=True;store.save_results(self.cid,r)
        with self.assertRaises(OSError):problems.remember_problem("P",time_ms=1200)
        self.assertEqual(judgelocal.limits_of("P"),(1000,256))
        self.assertFalse(problems.create_problem("P","新数据",self.files,overwrite=True)["ok"])
    def test_changed_exam_and_rule_withdraw_publication(self):
        self.result();store.set_released(self.cid,True)
        e=store.load_exam(self.cid);e["problems"][0]["name"]="changed";store.save_exam(self.cid,e)
        self.assertFalse(store.load_exam(self.cid)["released"])
        with self.assertRaises(ValueError):store.set_released(self.cid,True)
        store.update_contest(self.cid,rule="IOI")
        with self.assertRaises(ValueError):store.set_released(self.cid,True)
    def test_replacing_roster_with_results_is_rejected(self):
        self.result()
        with self.assertRaises(ValueError):store.replace_roster(self.cid,["新学生"])
    def test_no_testcase_order_fallback_or_traversal(self):
        judgelocal.store_problem("P",[{"name":"alpha","in":b"input","out":b"answer"}])
        self.assertEqual(localoj.read_problem_case("P","1"),("",""))
        self.assertEqual(localoj.read_problem_case("P","../../outside"),("",""))
        self.assertEqual(localoj.read_problem_case("P","alpha"),("input","answer"))
    def test_zip_rejects_traversal_and_duplicates(self):
        for names in [["../x"],["same","same"]]:
            p=Path(self.temp.name)/"bad.zip"
            with zipfile.ZipFile(p,"w") as z:
                for name in names:z.writestr(name,b"x")
            with self.assertRaises(ValueError):importer.Bundle(str(p))
    def test_empty_import_is_not_success(self):
        p=Path(self.temp.name)/"empty.zip"
        with zipfile.ZipFile(p,"w"):pass
        self.assertFalse(importer.import_problemset(str(p),dry_run=True,log=lambda *args:None)["ok"])

    def test_title_edit_updates_running_contest_and_retains_history(self):
        self.result()
        old_snapshot=store.load_results(self.cid)['K']['submission_exam']
        problems.remember_problem('P',title='加法修订')
        self.assertEqual(store.load_exam(self.cid)['problems'][0]['title'],'加法修订')
        self.assertEqual(store.load_results(self.cid)['K']['submission_exam'],old_snapshot)
        self.assertFalse(store.load_results(self.cid)['K'].get('rejudge_error'))

    def test_overwrite_updates_contest_statement_data_and_keeps_number(self):
        number=problems.code_of_pid('P')
        report=problems.create_problem('P','加法重传',{'1.in':b'7 8\n','1.out':b'15\n'},
                                      overwrite=True,statement='# 新题面 $a_i$',name='sum')
        self.assertTrue(report['ok'])
        self.assertEqual(report['number'],number)
        self.assertEqual(store.load_exam(self.cid)['problems'][0]['title'],'加法重传')
        self.assertEqual(store.load_exam(self.cid)['problems'][0]['name'],'sum')
        self.assertEqual(localoj.problem_statement('P'),'# 新题面 $a_i$')
        self.assertEqual(judgelocal.cases_of('P')[0]['out'],b'15\n')

    def test_archived_qa_problem_does_not_survive_catalog_cache(self):
        from csp_exam.web.server import Handler
        store.save_catalog([{'pid':'_engine_acceptance','title':'_engine_acceptance'}])
        handler=object.__new__(Handler)
        rows,error=handler._catalog_rows()
        self.assertEqual(error,'')
        self.assertEqual([row['pid'] for row in rows],['P'])
        self.assertNotIn('_engine_acceptance',[p['pid'] for p in store.load_catalog()['items']])

if __name__=="__main__":unittest.main()
