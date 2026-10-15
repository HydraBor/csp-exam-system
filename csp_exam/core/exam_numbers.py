"""Exact-name lookup using enrollment snapshots; never read scores or start clocks."""
import json
import time
import urllib.parse

from . import database, store, contest_time


def _text(value, label, *, required=False, limit=100):
    if not isinstance(value, str):
        raise ValueError(label + '格式不正确。')
    value = value.strip()
    if len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(label + '过长或包含无效字符。')
    if required and not value:
        raise ValueError('请输入考生姓名。')
    return value


def lookup(request, *, now=None):
    if not isinstance(request, dict):
        raise ValueError('查询内容格式不正确。')
    name = _text(request.get('name', ''), '姓名', required=True)
    campus = _text(request['campus'], '校区') if 'campus' in request else None
    teacher = _text(request['teacher'], '指导老师') if 'teacher' in request else None
    now = time.time() if now is None else now
    with store._LOCK, database.connection(store.DATA_DIR) as db:
        contests = {c['id']: c for (raw,) in db.execute(
            "SELECT payload FROM records WHERE document_key='contests.json' ORDER BY position")
            if isinstance(c := json.loads(raw), dict) and c.get('id')}
        # Parameters stay separate from SQL. Only matching enrollment metadata is read.
        rows = db.execute("""
            SELECT document_key,item_key,payload FROM records
            WHERE document_key GLOB 'contests/*/roster.json'
              AND json_type(payload)='object'
              AND trim(CAST(json_extract(payload,'$.name') AS TEXT))=?
            UNION ALL
            SELECT r.document_key,json_extract(r.payload,'$'),s.payload
            FROM records r JOIN records s
              ON s.document_key='students.json' AND s.item_key=json_extract(r.payload,'$')
            WHERE r.document_key GLOB 'contests/*/roster.json'
              AND json_type(r.payload)='text'
              AND trim(CAST(json_extract(s.payload,'$.name') AS TEXT))=?
        """, (name, name)).fetchall()
    matches = []
    for key, kaohao, raw in rows:
        parts = key.split('/')
        if len(parts) != 3 or parts[1] not in contests:
            continue  # Deleted contests and orphan rows cannot return stale numbers.
        rec = json.loads(raw)
        if str(rec.get('name') or '').strip() != name:
            continue
        matches.append((contests[parts[1]], str(kaohao), str(rec.get('campus') or '').strip(),
                        str(rec.get('teacher') or '').strip()))
    campuses = sorted({row[2] for row in matches})
    teachers = sorted({row[3] for row in matches if campus is None or row[2] == campus})
    results = []
    if campus is not None and teacher is not None:
        for c, kaohao, row_campus, row_teacher in matches:
            if (row_campus, row_teacher) != (campus, teacher):
                continue
            start, end = contest_time.window(c)
            flexible = contest_time.flexible(c)
            state = contest_time.phase(c, now)
            label = {'pending': '尚未开始', 'running': '进行中', 'ended': '已结束',
                     'unscheduled': '时间待定'}[state]
            if flexible and state == 'running' and now >= end:
                label = '开考时段已结束'
            results.append({'contest_id': c['id'], 'title': str(c.get('title') or c['id']),
                'campus': row_campus, 'teacher': row_teacher, 'kaohao': kaohao,
                'status': state, 'status_label': label, 'exam_mode': 'flexible' if flexible else 'fixed',
                'start_at': start, 'end_at': end, 'finish_at': contest_time.finish_at(c),
                'duration_minutes': store.duration_of(c),
                'entry_url': '/enter?c=' + urllib.parse.quote(c['id'], safe='')})
        results.sort(key=lambda r: (-r['start_at'], r['contest_id'], r['kaohao']))
    return {'ok': True, 'name': name, 'matched': bool(matches), 'campuses': campuses,
            'teachers': teachers, 'selection_required': campus is None or teacher is None,
            'results': results}
