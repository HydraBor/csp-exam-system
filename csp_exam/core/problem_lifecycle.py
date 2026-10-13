"""Delete unused library entries with SQLite rollback and recoverable file copies."""
from pathlib import Path
import os
import shutil
import stat
import tempfile

from . import database, judgelocal, localoj, problems, store


def purge(pid):
    if not judgelocal.valid_pid(pid):
        raise ValueError("题目标识无效。")
    with store._LOCK:
        if pid not in localoj.problem_pids():
            raise ValueError("题目不存在或已彻底删除，请刷新题库。")
        used = [f'{c["title"]} 第 {p.get("no")} 题'
                for c in store.list_contests()
                for p in store.load_exam(c["id"]).get("problems", [])
                if p.get("pid") == pid]
        if used:
            raise ValueError("题目仍在比赛中使用，请先移除：" + "、".join(used))
        # Snapshot only this problem; submissions and historical scores are retained.
        roots = [(Path(judgelocal.PROBLEMS_DIR), Path(judgelocal.cases_dir(pid)).parent),
                 (Path(store.DATA_DIR) / "samples", Path(problems._samples_root(pid))),
                 (Path(store.DATA_DIR) / "statements", Path(store.DATA_DIR) / "statements" / (pid + ".md"))]
        work = Path(tempfile.mkdtemp(prefix=".problem-purge-", dir=store.DATA_DIR))
        copies, removed, preserve = [], [], False
        try:
            for i, (root, source) in enumerate(roots):
                if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
                    raise ValueError("题目文件路径异常，已停止删除。")
                if not source.exists():
                    continue
                saved = work / str(i)
                modes = {}
                if source.is_dir():
                    shutil.copytree(source, saved, symlinks=True)
                    # Private snapshots must remain removable even for read-only input trees.
                    for folder, _, _ in os.walk(saved, followlinks=False):
                        p = Path(folder); modes[str(p.relative_to(saved))] = stat.S_IMODE(p.stat().st_mode)
                        p.chmod(0o700)
                else:
                    shutil.copy2(source, saved)
                copies.append((source, saved, modes))
            try:
                with database.transaction(store.DATA_DIR):
                    removed.extend(judgelocal.drop_problem_data(pid))
                    removed.extend(problems.drop_problem_cache(pid))
                    codes = problems.load_codes()
                    if pid in codes:
                        codes.pop(pid); problems.save_codes(codes); removed.append("题目编号")
                    info = problems.load_problem_info()
                    if pid in info:
                        info.pop(pid); problems.save_problem_info(info); removed.append("题目信息")
                    store.save_catalog(localoj.list_problems())
            except Exception as original:
                # Restore complete copies, including files already removed before an I/O error.
                try:
                    for i, (source, saved, modes) in enumerate(copies):
                        if source.exists():
                            os.replace(source, work / ("incomplete-" + str(i)))
                        for relative, mode in reversed(list(modes.items())):
                            (saved / relative).chmod(mode)
                        os.replace(saved, source)
                except Exception as restore_error:
                    preserve = True
                    raise OSError(f"删除及文件恢复失败；恢复副本保留在 {work}：{restore_error}") from original
                raise
        except Exception as error:
            if preserve:
                raise
            try:
                shutil.rmtree(work)
            except OSError:
                raise OSError(f"删除失败；恢复资料保存在 {work}：{error}") from error
            raise
        try:
            shutil.rmtree(work)
        except OSError as error:
            return {"removed": list(dict.fromkeys(removed)),
                    "cleanup_error": f"题库条目已移除，但临时恢复副本清理失败：{work}（{error}）"}
        return {"removed": list(dict.fromkeys(removed)), "cleanup_error": ""}
