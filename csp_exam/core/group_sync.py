"""Propagate edited groups to unused exams while preserving enrollment accounts.

Historical snapshots and exams with any attempt/result/source are frozen. Group
deletion only deletes the reusable address book; it never withdraws enrollment.
"""
from pathlib import Path

from . import attempts, contest_time, store, task_queue


def remember_links(cid, records, *, replace=False):
    contest = store.get_contest(cid)
    if not contest:
        return
    ids = [] if replace else list(contest.get('roster_group_ids', []))
    for rec in records:
        ids.extend(rec.get('group_ids', []))
    ids = list(dict.fromkeys(ids))
    if ids != contest.get('roster_group_ids', []):
        store.update_contest(cid, roster_group_ids=ids)


def eligible(contest):
    state = contest_time.phase(contest)
    if state == 'ended' or (state == 'running' and not contest_time.flexible(contest)):
        return False
    cid = contest['id']
    if attempts.all_for(cid) or store.load_results(cid) or task_queue.has_contest_jobs(cid):
        return False
    # Lost/legacy metadata must not turn a source-bearing contest into a draft.
    root = Path(store.DATA_DIR) / 'contests' / cid
    return not any(p.is_file() and p.relative_to(root).as_posix() not in
                   ('exam.json', 'roster.json', 'results.json') for p in root.rglob('*'))


def sync_group(group, *, previous=None):
    """Called under the store lock and the same SQLite transaction as the edit."""
    gid = group['gid']
    names = set(group.get('students') or [])
    for contest in store.list_contests():
        cid = contest['id']
        roster = store.load_roster(cid)
        links = list(dict.fromkeys(list(contest.get('roster_group_ids', [])) +
                                   [g for r in roster.values() for g in r.get('group_ids', [])]))
        if gid not in links or not eligible(contest):
            continue
        # A sole group owns this account: retain its number when labels change.
        # Shared identities stay with the other group; divergent new identities
        # receive their own numbers, as in ordinary namesake enrollment.
        kept, reusable, exempt_names = {}, {}, set()
        for kh, original in roster.items():
            row = dict(original)
            ids = list(row.get('group_ids', []))
            if gid not in ids:
                kept[kh] = row
                continue
            legacy_override = previous is not None and any(
                str(row.get(field) or '') != str(previous.get(field) or '')
                for field in ('campus', 'teacher'))
            if row.get('group_sync_exempt') or legacy_override:
                # Old deployments had no explicit override marker. A snapshot
                # already differing from the pre-edit group must not be guessed away.
                if legacy_override:
                    row['group_sync_exempt'] = True
                kept[kh] = row
                exempt_names.add(row.get('name'))
                exempt_names.add(row.get('group_sync_original_name'))
                continue
            others = [g for g in ids if g != gid]
            if others or row.get('manual_enrollment'):
                row['group_ids'] = others
                # These are display snapshots, aligned by ID in new enrollments.
                _, labels = store.group_sources(row)
                row['group_names'] = [labels.get(g, '') for g in others]
                kept[kh] = row
            elif row.get('name') in names:
                reusable.setdefault(row['name'], []).append((kh, row))
        for name in group.get('students') or []:
            if name in exempt_names:
                continue
            target = (name, group.get('campus', ''), group.get('teacher', ''))
            kh = next((k for k, r in kept.items() if store.student_identity(r) == target), None)
            if kh is not None:
                if reusable.get(name) and any(store.student_identity(r) != target for _, r in reusable[name]):
                    raise ValueError(f'比赛「{contest["title"]}」中，修改后「{name}」会与另一个考号成为相同身份；请先在本场核对，未保存本次分组修改。')
                row = kept[kh]
            elif reusable.get(name):
                kh, row = reusable[name].pop(0)
                row.update(campus=target[1], teacher=target[2])
            else:
                taken = store._taken_numbers({**roster, **kept})
                number = store._random_numbers(1, taken)[0]
                kh = store.make_kaohao(number, contest.get('prefix'), store.level_of(contest))
                row = dict(store.new_account(cid, kh), name=name, campus=target[1], teacher=target[2],
                           at=group.get('at', ''), group_ids=[], group_names=[])
            ids = list(row.get('group_ids', []))
            _, labels = store.group_sources(row)
            if gid not in ids:
                ids.append(gid)
            labels[gid] = group['name']
            row.update(group_ids=ids, group_names=[labels.get(g, '') for g in ids])
            kept[kh] = row
        store.save_roster(cid, kept)
        store.update_contest(cid, roster_group_ids=links)
