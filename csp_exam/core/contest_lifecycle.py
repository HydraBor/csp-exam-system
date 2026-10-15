"""Contest deletion with explicit filesystem failures and transactional metadata."""
from pathlib import Path
import os
import shutil
import tempfile
import time

from . import attempts, contest_time, database, store, task_queue


def delete(cid):
    store._safe_component(cid)
    with store._LOCK, task_queue._DB_LOCK:
        items = store.list_contests()
        contest = next((c for c in items if c.get('id') == cid), None)
        if not contest:
            return False
        if (contest_time.phase(contest) == 'running' or
                any(time.time() < a['end_at'] for a in attempts.all_for(cid).values())):
            raise ValueError('比赛正在进行，不能删除。')
        if any(r.get('judging') or r.get('submission_state') in ('queued', 'judging')
               for r in store.load_results(cid).values()):
            raise ValueError('本场仍有排队或正在评测的提交，不能删除。')
        root = Path(store.DATA_DIR).resolve()
        folder = root / 'contests' / cid
        if folder.is_symlink() or not folder.resolve().is_relative_to(root / 'contests'):
            raise ValueError('比赛文件路径异常，已停止删除。')
        work = Path(tempfile.mkdtemp(prefix='.contest-delete-', dir=root))
        saved = work / 'files'
        had_files, touched, preserve = folder.exists(), False, False
        try:
            if had_files:
                shutil.copytree(folder, saved, symlinks=True)
            try:
                with database.transaction(root), database.connection(root) as db:
                    # Lock both database writers before checking/cleaning jobs.
                    # A running orphan is still an active job and must finish first.
                    queue = root / 'judge-queue.sqlite3'
                    if queue.exists():
                        db.execute('ATTACH DATABASE ? AS judge_queue', (str(queue),))
                        if db.execute("SELECT 1 FROM judge_queue.jobs WHERE json_extract(payload,'$[0]')=? AND state='running'", (cid,)).fetchone():
                            raise ValueError('本场仍有正在执行的评测任务，不能删除。')
                        db.execute("DELETE FROM judge_queue.jobs WHERE json_extract(payload,'$[0]')=?", (cid,))
                    store.save_contests([c for c in items if c.get('id') != cid])
                    database.delete(root, 'contests/' + cid)
                    attempts.delete_contest(cid)
                    if had_files:
                        touched = True
                        shutil.rmtree(folder)
            except Exception:
                if touched:
                    try:
                        if folder.exists():
                            os.replace(folder, work / 'partial')
                        os.replace(saved, folder)
                    except Exception as restore_error:
                        preserve = True
                        raise OSError(f'删除及恢复失败，完整文件副本保留在 {work}：{restore_error}') from restore_error
                raise
        finally:
            if not preserve:
                # Cleanup failure is an error, never a silently successful deletion.
                shutil.rmtree(work)
        return True
