"""Two rule modes, legacy CSP migration, and immutable historical I/O contracts."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import database, grading, judgelocal, store, submissions, wrapper
from csp_exam.web.student_pages import StudentPages
from csp_exam.web.ui import rule_badge, rule_tip


class Rules(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='csp-rules-')
        self.addCleanup(temp.cleanup)
        for item in (patch.object(store, 'DATA_DIR', temp.name),
                     patch.object(grading, 'log', lambda *_: None)):
            item.start(); self.addCleanup(item.stop)
        self.cid, self.kh = 'test', 'GD-J12345'
        self.exam = {'problems': [{'no': 1, 'pid': 'P', 'name': 'sum', 'full': 100}], 'released': False}
        self.contest = {'id': self.cid, 'title': '测试', 'rule': 'OI', 'released': False,
                        'start_at': 1, 'end_at': 2}
        store.save_contests([self.contest])
        store.save_exam(self.cid, self.exam)
        store._save(store._cp(self.cid, 'roster.json'), {self.kh: {'name': '测试学生'}})

    def test_two_choices_and_file_io_default(self):
        self.assertEqual(list(store.RULES), ['OI', 'IOI'])
        self.assertEqual(store.DEFAULT_RULE, 'OI')
        self.assertEqual(store.rule_signature(store.rule_of({})), (False, True))
        self.assertEqual(store.rule_signature(store.rule_of({'rule': 'IOI'})), (True, False))

    def test_legacy_alias_create_and_update_are_persisted_as_oi(self):
        c = store.create_contest('旧客户端', 'CSP')
        self.assertEqual(c['rule'], 'OI')
        self.assertEqual(store.update_contest(c['id'], rule='CSP')['rule'], 'OI')
        self.assertEqual(store.rule_of({'rule': 'CSP'})['key'], 'OI')

    def test_migration_preserves_history_and_is_idempotent(self):
        old = dict(self.contest, rule='CSP', released=True, duration_min=180)
        record = {'submitted_at': 'before', 'total': 100, 'problems': {'T1': {'score': 100}},
                  'judge_rule': {'key': 'CSP', 'freopen': True, 'best_of': False}}
        store._save(store._index_path(), [old])
        store.save_results(self.cid, {self.kh: record})
        store.save_exam(self.cid, dict(self.exam, released=True))
        with database.connection(store.DATA_DIR) as db:
            db.execute('INSERT INTO exam_attempts VALUES(?,?,?,?)', (self.cid, self.kh, 100, 200))
        expected = copy.deepcopy(old); expected['rule'] = 'OI'
        self.assertEqual(store.list_contests(), [expected])
        self.assertEqual(store.load_results(self.cid)[self.kh], record)
        self.assertTrue(store.load_exam(self.cid)['released'])
        with database.connection(store.DATA_DIR) as db:
            self.assertEqual(db.execute('SELECT start_at,end_at FROM exam_attempts').fetchone(), (100, 200))
        before = database.signature(store.DATA_DIR, ['contests.json'])
        store.list_contests()
        self.assertEqual(database.signature(store.DATA_DIR, ['contests.json']), before)

    def test_strict_oi_directory_and_zip_root_paths(self):
        names = [f'{self.kh}/sum/sum.cpp', 'sum/sum.cpp', f'{self.kh}/SUM/sum.cpp',
                 f'{self.kh}/sum/sum.cc', 'OTHER/sum/sum.cpp']
        rule = store.rule_of(self.contest)
        picked, _ = wrapper.pick_sources(names, self.exam['problems'], strict=True,
                                         strict_layout=rule['freopen'], kaohao=self.kh)
        self.assertEqual(picked, {1: names[0]})
        for bad in names[1:]:
            self.assertEqual(wrapper.pick_sources([bad], self.exam['problems'], strict=True,
                strict_layout=rule['freopen'], kaohao=self.kh)[0], {})

    def test_legacy_csp_snapshot_can_publish_after_rename(self):
        store.save_results(self.cid, {self.kh: {'submission_exam': self.exam,
            'judge_rule': {'key': 'CSP', 'freopen': True, 'best_of': False},
            'problems': {'T1': {'score': 100}}, 'submission_state': 'done'}})
        store.set_released(self.cid, True)
        self.assertTrue(store.load_exam(self.cid)['released'])

    def test_old_stdio_oi_snapshot_is_not_equivalent_to_new_oi(self):
        store.save_results(self.cid, {self.kh: {'submission_exam': self.exam,
            'judge_rule': {'key': 'OI', 'freopen': False, 'best_of': False},
            'problems': {'T1': {'score': 100}}, 'submission_state': 'done'}})
        with self.assertRaises(ValueError): store.set_released(self.cid, True)

    def judge_with_snapshot(self, rule):
        sid, root = submissions.archive(self.cid, self.kh, {f'{self.kh}/sum/sum.cpp': b'int main(){}'})
        store.put_result(self.cid, self.kh, {'submission_id': sid, 'judge_rule': rule})
        with patch.object(judgelocal, 'limits_of', return_value=(1000, 256)), \
                patch.object(judgelocal, 'judge_source', return_value={'status': 1, 'score': 100}) as judge:
            self.assertFalse(grading.judge_csp(self.cid, self.kh,
                {1: root + f'/{self.kh}/sum/sum.cpp'}, self.exam, sid))
            return judge.call_args.kwargs['io_mode']

    def test_new_oi_uses_file_io(self):
        self.assertEqual(self.judge_with_snapshot(dict(store.RULES['OI'])), 'file')

    def test_recovered_old_stdio_snapshot_remains_stdio(self):
        self.assertEqual(self.judge_with_snapshot({'key': 'OI', 'freopen': False, 'best_of': False}), 'stdin')

    def test_ioi_still_uses_stdio(self):
        self.assertEqual(self.judge_with_snapshot(dict(store.RULES['IOI'])), 'stdin')

    def test_student_ui_has_correct_io_and_scoring_in_both_modes(self):
        current = dict(self.contest, start_at=1, end_at=4102444800)
        page = StudentPages()._submit_block(self.exam, current, self.cid, self.kh, '测试学生')
        self.assertIn('以最后一次完整交卷计分', page)
        self.assertNotIn('不需要 freopen', page)
        ioi = dict(current, rule='IOI')
        page = StudentPages()._submit_block(self.exam, ioi, self.cid, self.kh, '测试学生')
        self.assertIn('不需要 freopen', page)
        self.assertIn('每题保留历史最高分', page)
        self.assertNotIn('以最后一次', page)
        self.assertIn('OI 赛制', rule_badge({'rule': 'CSP'}))
        self.assertNotIn('CSP 赛制', rule_badge({'rule': 'CSP'}))
        self.assertIn('文件读写', rule_tip(current))


if __name__ == '__main__': unittest.main()
