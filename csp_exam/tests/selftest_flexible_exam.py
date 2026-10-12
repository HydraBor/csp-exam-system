"""Personal clocks, HTTP resource gates, restart persistence and publication."""
import concurrent.futures
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import attempts, contest_time as ct, database, grading, scoreboard, store
from csp_exam.web.server import Handler
from csp_exam.tests import selftest_contest_schedule_zip as scheduled_tests


class FlexibleHTTP(unittest.TestCase):
    request = scheduled_tests.HTTPWindows.request
    tearDown = scheduled_tests.HTTPWindows.tearDown

    def setUp(self):
        scheduled_tests.HTTPWindows.setUp(self)
        store.update_contest(self.cid, exam_mode='flexible', duration_min=30)
        self.start = self.now + 60
        self.close = self.now + 3600
        self.finish = self.close + 1800
        roster = store.load_roster(self.cid)
        roster['GD-J54321'] = {'name': '李四'}
        store.save_roster(self.cid, roster)

    def at(self, now):
        return patch('csp_exam.core.contest_time.time.time', return_value=now)

    def login(self, kh=None):
        return self.request('/enter', 'POST', ('kaohao=' + (kh or self.kh) + '&c=' + self.cid).encode(), cookie='')

    def test_prelogin_and_opening_do_not_auto_start_or_reveal_questions(self):
        self.assertEqual(self.login()[0], 302)
        self.assertIsNone(attempts.get(self.cid, self.kh))
        self.assertIn('点击', self.request('/hall')[2])
        with self.at(self.start):
            self.assertEqual(json.loads(self.request('/api/contest-state')[2])['phase'], 'ready')
            text = self.request('/hall')[2]
            self.assertIn('action="/start?', text)
            self.assertNotIn('secret_slug', text)
            self.assertIsNone(attempts.get(self.cid, self.kh))
            for route in ('/problem?p=1', '/sample?p=1', '/file?f=a.cpp', '/submit'):
                self.assertEqual(self.request(route)[0], 403, route)
            self.assertEqual(self.request('/upload', 'POST', b'')[0], 403)
            self.assertEqual(self.request('/start', 'POST', b'')[0], 302)
            self.assertIn('secret_slug', self.request('/hall')[2])
            self.assertEqual(attempts.get(self.cid, self.kh)['end_at'], self.start + 1800)

    def test_late_login_has_full_duration_and_reconnect_never_resets_it(self):
        with self.at(self.close - .25):
            self.assertEqual(self.login()[0], 302)
            clock = attempts.get(self.cid, self.kh)
        self.assertEqual(clock['end_at'] - clock['start_at'], 1800)
        with self.at(self.close + 600):
            self.assertEqual(self.request('/logout')[0], 302)
            self.assertEqual(self.login()[0], 302)
            self.assertEqual(self.request('/start', 'POST', b'')[0], 302)
            self.assertEqual(attempts.get(self.cid, self.kh), clock)
            self.assertIn('secret_slug', self.request('/hall')[2])
            state = json.loads(self.request('/api/contest-state')[2])
            self.assertEqual(state['end_at'], clock['end_at'])
            # A session for another contest/number cannot retrieve this clock.
            self.assertNotEqual(json.loads(self.request('/api/contest-state', cookie='')[2])['end_at'], clock['end_at'])
        with self.at(clock['end_at']):
            self.assertNotIn('secret_slug', self.request('/hall')[2])
            self.assertEqual(self.request('/upload', 'POST', b'')[0], 403)
            self.assertEqual(attempts.get(self.cid, self.kh), clock)

    def test_exact_window_end_rejects_first_start_and_invalid_sessions(self):
        with self.at(self.close):
            self.assertEqual(self.request('/start', 'POST', b'')[0], 403)
            self.assertEqual(self.login()[0], 302)  # May authenticate to the closed notice, never open questions.
            self.assertIsNone(attempts.get(self.cid, self.kh))
        with self.at(self.start):
            self.assertEqual(self.request('/start', 'POST', b'', cookie='')[0], 403)
            self.assertEqual(self.request('/start', 'POST', b'', origin='https://other.invalid')[0], 403)
            self.assertIsNone(attempts.get(self.cid, self.kh))

    def test_concurrent_tabs_and_sqlite_reopen_grant_exactly_one_clock(self):
        with self.at(self.start + 123.5):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                clocks = list(pool.map(lambda _: attempts.start(self.cid, self.kh), range(16)))
        self.assertTrue(all(clock == clocks[0] for clock in clocks))
        with closing(sqlite3.connect(Path(self.tmp.name) / database.FILENAME)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM exam_attempts').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT end_at-start_at FROM exam_attempts').fetchone()[0], 1800)
        self.assertEqual(attempts.get(self.cid, self.kh), clocks[0])

    def test_personal_upload_deadline_crossed_during_body_read_is_rejected(self):
        with self.at(self.start):
            attempts.start(self.cid, self.kh)
        clock = attempts.get(self.cid, self.kh)
        with self.at(clock['end_at'] - 1) as now:
            def crossing(handler):
                now.return_value = clock['end_at']
                return {self.kh + '/张三.txt': b'x', self.kh + '/secret_slug/secret_slug.cpp': b'int main(){}'}
            with patch.object(Handler, '_read_upload', crossing), patch.object(grading, 'reserve_submission', return_value=True), patch.object(grading, 'release_submission') as release, patch.object(grading, 'enqueue_submission') as enqueue:
                self.assertEqual(self.request('/upload', 'POST', b'')[0], 403)
                release.assert_called_once(); enqueue.assert_not_called()
        self.assertEqual(store.load_results(self.cid), {})

    def test_after_window_upload_is_accepted_until_personal_deadline(self):
        with self.at(self.close - 1):
            attempts.start(self.cid, self.kh)
        with self.at(self.close + 600), patch.object(Handler, '_read_upload', return_value={self.kh + '/张三.txt': b'x', self.kh + '/secret_slug/secret_slug.cpp': b'int main(){}'}), patch.object(grading, 'reserve_submission', return_value=True), patch.object(grading, 'enqueue_submission') as enqueue:
            self.assertEqual(self.request('/upload', 'POST', b'')[0], 200)
            enqueue.assert_called_once()
            self.assertIn('submission_id', store.load_results(self.cid)[self.kh])

    def test_finished_student_cannot_see_scores_until_global_deadline(self):
        with self.at(self.start):
            attempts.start(self.cid, self.kh)
        store.put_result(self.cid, self.kh, {'problems': {'T1': {'score': 100}}})
        # Even an inconsistent legacy released flag cannot reveal early results.
        store.save_exam(self.cid, {**store.load_exam(self.cid), 'released': True})
        with self.at(self.start + 1800):
            for route in ('/score', '/scoreboard', '/api/scoreboard', '/file?f=a.cpp'):
                self.assertEqual(self.request(route)[0], 403, route)
            self.assertNotIn('scores', scoreboard.snapshot(self.cid))
            self.assertNotIn('href="/scoreboard?', self.request('/hall')[2])
        with self.at(self.finish - .01):
            self.assertEqual(self.request('/api/scoreboard')[0], 403)
        with self.at(self.finish):
            self.assertEqual(self.request('/api/scoreboard')[0], 200)

    def test_auto_release_waits_for_judgement_and_explicit_withdrawal_stays_private(self):
        store.put_result(self.cid, self.kh, {'judging': True, 'submission_state': 'queued'})
        with self.at(self.finish):
            self.assertEqual(self.request('/api/scoreboard')[0], 403)
            store.put_result(self.cid, self.kh, {'problems': {'T1': {'score': 100}}})
            self.assertEqual(self.request('/api/scoreboard')[0], 200)
            self.assertTrue(store.load_exam(self.cid)['released'])
            self.assertEqual(self.request('/admin/release', 'POST', b'released=0', admin=True)[0], 302)
            self.assertEqual(self.request('/api/scoreboard')[0], 403)
            self.assertFalse(store.load_exam(self.cid)['released'])
            self.assertEqual(self.request('/admin/release', 'POST', b'released=1', admin=True)[0], 302)
            self.assertEqual(self.request('/api/scoreboard')[0], 200)

    def test_settings_and_identity_cannot_reset_started_attempt(self):
        with self.at(self.start):
            attempts.start(self.cid, self.kh)
        for fields in ({'duration_min': 31}, {'end_at': self.close + 1}, {'exam_mode': 'fixed'}):
            with self.assertRaises(ValueError): store.update_contest(self.cid, **fields)
        for action in (lambda: store.replace_roster(self.cid, ['李四']), lambda: store.reshuffle_kaohaos(self.cid), lambda: store.remove_from_roster(self.cid, [self.kh])):
            with self.assertRaises(ValueError): action()
        with self.assertRaises(ValueError): store.delete_contest(self.cid)
        before = store.get_contest(self.cid)
        with self.at(self.start + 1):
            self.assertEqual(self.request('/admin/release', 'POST', b'action=settings&duration=31&prefix=NEW', admin=True)[0], 400)
        self.assertEqual(store.get_contest(self.cid), before)
        text = self.request('/admin', admin=True)[2]
        self.assertIn('个人截止时间', text)
        self.assertNotIn('重新随机分配考号</button>', text)

    def test_flexible_schedule_is_independent_of_window_and_old_fixed_exams_stay_fixed(self):
        c = store.get_contest(self.cid)
        schedule = ct.parse_schedule({'start_at': self.start, 'end_at': self.close, 'duration': '180', 'exam_mode': 'flexible'})
        self.assertEqual(schedule['duration_min'], 180)
        self.assertEqual(ct.phase(c, self.close), 'running')
        self.assertEqual(ct.phase(c, self.finish), 'ended')
        fixed = {'start_at': self.start, 'end_at': self.close}
        self.assertEqual(ct.student_phase(fixed, self.kh, self.close), 'ended')
        for duration in ('0', '-1', 'nan', '180.5', ''):
            with self.assertRaises(ValueError): ct.parse_schedule({'start_at': self.start, 'end_at': self.close, 'duration': duration, 'exam_mode': 'flexible'})
        status, _, page = self.request('/admin/new', admin=True)
        self.assertEqual(status, 200); self.assertIn('value="flexible"', page)

    def test_schema_one_upgrade_preserves_original_documents(self):
        with closing(sqlite3.connect(Path(self.tmp.name) / database.FILENAME)) as db:
            db.execute('DROP TABLE exam_attempts'); db.execute('PRAGMA user_version=1')
            db.commit()
        original = store.get_contest(self.cid)
        self.assertEqual(original['exam_mode'], 'flexible')
        self.assertEqual(attempts.all_for(self.cid), {})
        with database.connection(self.tmp.name) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_legacy_fixed_mode_save_does_not_withdraw_existing_publication(self):
        legacy = store.get_contest(self.cid)
        legacy.pop('exam_mode')
        store.save_contests([legacy])
        store.save_exam(self.cid, {**store.load_exam(self.cid), 'released': True})
        store.update_contest(self.cid, exam_mode='fixed')
        self.assertTrue(store.load_exam(self.cid)['released'])


if __name__ == '__main__': unittest.main()
