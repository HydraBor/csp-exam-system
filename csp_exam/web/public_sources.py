"""Anonymous source previews/downloads; no file paths or account credentials in URLs."""
import html
import urllib.parse

from ..core import public_sources, store
from ..core.judge_result import status_text
from ..core.util import _fmt_ms, _fmt_kb
from .ui import code_pre, decode_text, page


def source_url(cid, token, no=None, *, download=False):
    query = {'c': cid, 'source': token}
    if no is not None:
        query['p'] = str(no)
    if download:
        query['download'] = '1'
    return '/scoreboard?' + urllib.parse.urlencode(query)


def point_details(result, problem):
    """A strict display projection: never copy case input/output, paths or logs."""
    got = (result.get('problems') or {}).get(store.problem_dir_name(int(problem['no']))) or {}
    cases = got.get('testcases') or []
    full = int(problem.get('full') or 100)
    score = got.get('score', 0)
    summary = f'<p>本题得分：<b>{html.escape(str(score))} / {full}</b> 分'
    if cases:
        passed = sum(c.get('status') == 1 for c in cases)
        summary += f'　测试点通过：{passed} / {len(cases)}'
    summary += '</p>'
    if not cases:
        message = '编译错误，未运行测试点。' if got.get('status') == 7 else '本题没有逐点评测记录。'
        return '<h3 class="h4">逐点评测详情</h3>' + summary + f'<p class="text-secondary">{message}</p>'
    rows = []
    for i, case in enumerate(cases, 1):
        accepted = case.get('status') == 1
        label = status_text(case.get('status'))
        tone = 'green' if accepted else 'red'
        when = _fmt_ms(case.get('time')) if case.get('time') is not None else '—'
        memory = _fmt_kb(case.get('memory')) if case.get('memory') is not None else '—'
        earned = case.get('score')
        earned = '—' if earned is None else str(earned)
        rows.append(f'<tr><th scope="row">#{i}</th><td><span class="badge bg-{tone}-lt">{html.escape(label)}</span></td>'
            f'<td>{html.escape(earned)}</td><td>{html.escape(when)}</td><td>{html.escape(memory)}</td></tr>')
    return ('<h3 class="h4">逐点评测详情</h3>' + summary
        + '<div class="table-responsive mb-4"><table class="table table-vcenter table-sm mb-0">'
        '<thead><tr><th scope="col">测试点</th><th scope="col">判定</th><th scope="col">得分</th>'
        '<th scope="col">用时</th><th scope="col">内存</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')


def route(handler, q):
    cid, token = q.get('c', ''), q.get('source', '')
    try:
        with store._LOCK:
            store.maybe_auto_release(cid)
            contest, exam, kh, student, result = public_sources.submission(cid, token)
            problems = exam.get('problems') or []
            if q.get('p'):
                problems = [p for p in problems if str(p['no']) == q['p']]
                if not problems:
                    raise FileNotFoundError('题目不存在。')
            if q.get('download'):
                if len(problems) != 1 or not q.get('p'):
                    raise FileNotFoundError('请指定需要下载的题目。')
                filename, data = public_sources.source_file(cid, kh, result, problems[0])
                handler.send_response(200)
                handler.send_header('Content-Type', 'application/octet-stream')
                handler.send_header('Content-Length', str(len(data)))
                handler.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
                handler.send_header('Cache-Control', 'no-store')
                handler.send_header('X-Robots-Tag', 'noindex, nofollow')
                handler.end_headers()
                handler.wfile.write(data)
                return
            blocks = []
            for problem in problems:
                no = int(problem['no'])
                heading = f'第 {no} 题 · {store.code_of(problem)}'
                try:
                    filename, data = public_sources.source_file(cid, kh, result, problem)
                except FileNotFoundError:
                    content = '<p class="text-secondary mb-0">这道题没有可公开的源码。</p>'
                else:
                    download = source_url(cid, token, no, download=True)
                    content = (f'<div class="d-flex justify-content-between align-items-center mb-3">'
                        f'<span class="font-monospace">{html.escape(filename)}</span>'
                        f'<a class="btn btn-sm btn-outline-primary" href="{html.escape(download, quote=True)}">下载源码</a></div>'
                        + code_pre(decode_text(data)))
                details = point_details(result, problem)
                blocks.append(f'<section class="card card-body mb-3" id="T{no}">'
                    f'<h2 class="h3 mb-3">{html.escape(heading)}</h2>{details}'
                    f'<h3 class="h4">提交源码</h3>{content}</section>')
            back = '/scoreboard?' + urllib.parse.urlencode({'c': cid})
            tabs = ''.join(f'<a class="btn btn-sm btn-outline-secondary" href="{html.escape(source_url(cid, token, p["no"]), quote=True)}">第 {int(p["no"])} 题</a>'
                           for p in exam.get('problems') or [])
            body = (f'<p><a href="{html.escape(back, quote=True)}">返回公开排行榜</a></p>'
                f'<div class="card card-body mb-3"><h1 class="h2">{html.escape(student.get("name") or "学生")}的提交源码</h1>'
                f'<p class="text-secondary mb-2">{html.escape(contest["title"])}</p>'
                f'<p class="mb-3">校区：{html.escape(student.get("campus") or "未填写")}　'
                f'指导老师：{html.escape(student.get("teacher") or "未填写")}</p>'
                '<p class="text-secondary small">展示与本次成绩对应的提交代码。</p>'
                f'<nav class="d-flex flex-wrap gap-2" aria-label="选择题目">{tabs}</nav></div>' + ''.join(blocks))
            handler._send(page('公开提交源码', body, show_title=False,
                               extra_head='<meta name="robots" content="noindex,nofollow">'))
    except PermissionError as error:
        handler._send(page('源码尚未开放', '<div class="card card-body">' + html.escape(str(error)) + '</div>'), 403)
    except (FileNotFoundError, ValueError):
        handler._send(page('源码不存在', '<div class="card card-body">没有可访问的提交源码。</div>'), 404)
