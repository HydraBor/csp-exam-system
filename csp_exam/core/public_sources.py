"""Public access to graded source files, gated by contest and score publication."""
import hashlib
import hmac
import json
from pathlib import Path
import re

from . import contest_time, store, submissions

SOURCE_EXTENSIONS = {'.cpp', '.cc', '.cxx', '.c++', '.c', '.pas'}
MAX_PUBLIC_BYTES = 1024 * 1024


def enabled(contest, exam):
    return bool(contest and contest.get('public_sources') and exam.get('released')
                and contest_time.phase(contest) == 'ended')


def student_token(cid, kaohao):
    # A separate signing namespace prevents exposing the login exam number.
    payload = 'public-source-v1:' + json.dumps([cid, kaohao], separators=(',', ':'))
    return hmac.new(store.admin_key().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def submission(cid, token):
    contest = store.get_contest(cid)
    if not contest:
        raise FileNotFoundError('比赛不存在。')
    exam = store.load_exam(cid)
    if not enabled(contest, exam):
        raise PermissionError('整场比赛结束、成绩公布且管理员开放源码后才能查看。')
    if not re.fullmatch(r'[0-9a-f]{32}', str(token or '')):
        raise FileNotFoundError('提交不存在。')
    for kh, student in store.load_roster(cid).items():
        if hmac.compare_digest(student_token(cid, kh), token):
            result = store.load_results(cid).get(kh)
            if not result or store.result_pending(result):
                raise FileNotFoundError('没有可公开的提交。')
            return contest, exam, kh, student, result
    raise FileNotFoundError('提交不存在。')


def source_file(cid, kaohao, result, problem):
    """Resolve only the server-recorded file for this problem, including legacy paths.

    IOI's retained best result can refer to an earlier immutable submission. The
    per-problem graded file takes precedence over the latest upload's mapping.
    """
    no = int(problem['no'])
    got = (result.get('problems') or {}).get(store.problem_dir_name(no)) or {}
    rel = str(got.get('file') or '')
    if not rel:
        if store.rule_of(store.get_contest(cid)).get('best_of'):
            # A latest upload cannot prove which code earned a retained best score.
            raise FileNotFoundError('这道题没有记录与成绩对应的源码。')
        rel = str((result.get('picked') or result.get('structure_picked') or {}).get(str(no)) or '')
    if not rel or Path(rel).suffix.lower() not in SOURCE_EXTENSIONS:
        raise FileNotFoundError('这道题没有可公开的源码。')
    base = Path(store.upload_dir(cid, kaohao))
    candidates = [rel, kaohao + '/' + rel]
    prefix = kaohao + '/'
    if rel.startswith(prefix):
        candidates.append(rel[len(prefix):])
    for candidate in candidates:
        try:
            target = Path(submissions.confined_file(str(base), candidate))
        except ValueError:
            continue
        if target.suffix.lower() not in SOURCE_EXTENSIONS or not target.is_file():
            continue
        # Neither a symlink nor a symlinked directory may select another upload.
        raw = base / candidate
        if any(p.is_symlink() for p in [raw, *raw.parents] if p != base.parent):
            continue
        try:
            with target.open('rb') as stream:
                data = stream.read(MAX_PUBLIC_BYTES + 1)
        except OSError:
            continue
        if len(data) > MAX_PUBLIC_BYTES or b'\x00' in data:
            raise FileNotFoundError('源码无法公开预览或下载。')
        return store.code_of(problem) + target.suffix.lower(), data
    raise FileNotFoundError('这道题没有可公开的源码。')
