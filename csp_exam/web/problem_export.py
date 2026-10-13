"""Authenticated, bounded, streaming downloads of private teacher problem ZIPs."""
from pathlib import Path
import tempfile
import threading
import urllib.parse
import html

from ..config import MAX_PS_UPLOAD, MAX_LIBRARY_EXPORT
from ..core import judgelocal, localoj, problems
from ..core.problem_export import build_zip
from .ui import page
from .urls import admin_url

_SLOT = threading.BoundedSemaphore(1)


def route(handler, query):
    if not handler._check_admin(query.get("key", "")):
        return handler._admin_login()
    key = query.get("key", "")
    def error(message, status=400):
        body = handler._flash("err", message)
        body += '<p><a class="btn btn-outline-secondary" href="' + html.escape(admin_url(key, path="/admin/problems"), quote=True) + '">返回题库</a></p>'
        return handler._send(page("导出失败", body), status)
    batch = handler.command == "POST" or "pids" in query
    if batch:
        try:
            raw = handler._form(32 * 1024).get("pids", "") if handler.command == "POST" else query["pids"]
        except ValueError as e:
            return error(str(e))
        selected = list(dict.fromkeys(p.strip() for p in raw.split(",")))
        if not raw.strip() or len(raw) > 32 * 1024 or len(selected) > 500:
            return error("请勾选要导出的题目，每次最多导出500道。")
        if any(not judgelocal.valid_pid(p) for p in selected):
            return error("选中的题目标识无效。")
    else:
        selected = [query.get("pid", "").strip()]
    known = set(localoj.problem_pids())
    if any(not judgelocal.valid_pid(p) or p not in known or problems.is_deleted(p) for p in selected):
        return error("选中的题目不存在或已删除，请返回题库刷新后重试。", 400 if batch else 404)
    if not _SLOT.acquire(blocking=False):
        return error("另一份题包正在导出，请稍后重试。", 429)
    try:
        with tempfile.TemporaryDirectory(prefix="csp-download-") as tmp:
            output = Path(tmp) / "problem.zip"
            try:
                build_zip(output, selected, max_bytes=(MAX_LIBRARY_EXPORT if batch else MAX_PS_UPLOAD) - 4096)
            except (ValueError, OSError) as e:
                return error(str(e))
            limit = MAX_LIBRARY_EXPORT if batch else MAX_PS_UPLOAD
            if output.stat().st_size > limit - 4096:
                return error("题包超过本部署的导出上限，请减少所选题目。")
            pid = selected[0]
            filename = "csp-problems-" + str(len(selected)) + ".zip" if batch else (problems.code_of_pid(pid) or pid) + "-" + pid + ".zip"
            handler.send_response(200)
            handler.send_header("Content-Type", "application/zip")
            handler.send_header("Content-Disposition", "attachment; filename=problem.zip; filename*=UTF-8''" + urllib.parse.quote(filename))
            handler.send_header("Content-Length", str(output.stat().st_size))
            handler.send_header("Cache-Control", "private, no-store")
            handler.send_header("Referrer-Policy", "same-origin")
            handler.end_headers()
            try:
                with output.open("rb") as stream:
                    while chunk := stream.read(256 * 1024):
                        handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass  # Disconnected clients still release the slot and remove private files.
    finally:
        _SLOT.release()
