"""Student-visible limits follow judge metadata and remain behind exam gates."""
from pathlib import Path
import re,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from csp_exam.core import judgelocal,localoj,problems,store
from csp_exam.tests import selftest_contest_schedule_zip as schedule_tests


class StudentLimits(unittest.TestCase):
    request=schedule_tests.HTTPWindows.request
    tearDown=schedule_tests.HTTPWindows.tearDown

    def setUp(self):
        schedule_tests.HTTPWindows.setUp(self)
        store.update_contest(self.cid,start_at=self.now-60)
        problems.remember_problem("SECRET_PID",title="资源限制验收",time_ms=1500,memory_mb=256)
        localoj.set_problem_statement("SECRET_PID","## 题目描述\n\n仅用于测试的题面。")

    def test_judge_limits_visible_in_problem_and_compact_hall_row(self):
        self.assertEqual(judgelocal.limits_of("SECRET_PID"),(1500,256))
        for route in ("/hall","/problem?p=1"):
            status,_,text=self.request(route)
            self.assertEqual(status,200)
            self.assertIn("1.5 秒",text)
            self.assertIn("256 MiB",text)
        body=self.request("/problem?p=1")[2]
        self.assertIn("时间限制",body);self.assertIn("内存限制",body)
        self.assertIn("100 分",body)

    def test_editing_only_limits_invalidates_student_refresh_version(self):
        status,_,before=self.request("/problem?p=1")
        original=localoj.problem_statement("SECRET_PID")
        problems.remember_problem("SECRET_PID",time_ms=2500,memory_mb=512)
        _,_,after=self.request("/problem?p=1")
        version=lambda text:re.search(r'data-version="([^"]+)"',text).group(1)
        self.assertNotEqual(version(before),version(after))
        self.assertIn("2.5 秒",after);self.assertIn("512 MiB",after)
        self.assertEqual(localoj.problem_statement("SECRET_PID"),original)

    def test_missing_limits_are_not_fabricated(self):
        problems.save_problem_info({})
        status,_,text=self.request("/problem?p=1")
        self.assertEqual(status,200)
        self.assertIn("未配置",text)
        self.assertNotIn("256 MiB",text)
        self.assertNotIn(">1 秒<",text)

    def test_limits_and_problem_stay_private_before_start_and_after_end(self):
        for start,end in [(self.now+60,self.now+3600),(self.now-3600,self.now-1)]:
            store.update_contest(self.cid,start_at=start,end_at=end)
            for route in ("/hall","/problem?p=1"):
                status,_,text=self.request(route)
                self.assertEqual(status,200 if route=="/hall" else 403)
                self.assertNotIn("problem-limit-grid",text)
                self.assertNotIn("problem-list-limits",text)
                self.assertNotIn("256 MiB",text)

    def test_unauthenticated_request_cannot_read_limits(self):
        status,headers,text=self.request("/problem?p=1",cookie="")
        self.assertEqual(status,302)
        self.assertIn("/enter",headers.get("Location",""))
        self.assertNotIn("256 MiB",text)


if __name__=="__main__":unittest.main()
