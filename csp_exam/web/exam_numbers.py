"""Public exam-number search. Names are submitted in a POST body, never a URL."""
import json
from pathlib import Path

from ..core.exam_numbers import lookup
from .ui import page


def screen(handler):
    body = (Path(__file__).with_name('templates') / 'exam-number.html').read_text(encoding='utf-8')
    handler._send(page('考号查询', body, show_title=False, contest_js=False,
        extra_head='<meta name="robots" content="noindex,nofollow">'
                   '<script defer src="/static/exam-number.js"></script>'))


def search(handler):
    if handler.headers.get_content_type() != 'application/json':
        return handler._json({'ok': False, 'error': '请使用查询页面提交姓名。'}, 415)
    try:
        length = handler._request_size(4096)
        request = json.loads(handler.rfile.read(length).decode('utf-8'))
        result = lookup(request)
    except (UnicodeError, ValueError) as error:
        message = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else '查询内容格式不正确。'
        return handler._json({'ok': False, 'error': message}, 400)
    handler._json(result)
