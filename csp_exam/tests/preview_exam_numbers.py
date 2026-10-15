"""Loopback preview with fictional students, disposable data and explicit shutdown."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer


def main():
    root=Path(__file__).resolve().parents[2]/'tests/tmp'
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='exam-number-',dir=root) as work:
        os.environ['CSP_EXAM_DATA_DIR']=work+'/data'
        os.environ['CSP_EXAM_LOG_DIR']=work+'/logs'
        from csp_exam.core import store
        from csp_exam.web.server import Handler
        now=int(time.time())
        for i,title in enumerate(['往期联赛 · 界面验收临时数据','当前联赛 · 界面验收临时数据','未来自主联赛 · 界面验收临时数据']):
            start,end=[(now-3000,now-2000),(now-1000,now+1000),(now+2000,now+4000)][i]
            c=store.create_contest(title,rule='OI',level='J',start_at=start,end_at=end,
                exam_mode='flexible' if i==2 else 'fixed',duration_min=180)
            for campus,teacher in [('深圳南山','张老师'),('深圳南山','李老师'),('广州天河','张老师'),('','')]:
                store.assign_kaohaos(c['id'],['界面验收同学'],campus=campus,teacher=teacher)
        class Preview(Handler):
            def log_message(self,*args):pass
            def do_POST(self):
                if self.path=='/__preview-stop':
                    self._json({'ok':True});threading.Thread(target=server.shutdown,daemon=True).start()
                else:super().do_POST()
        server=ThreadingHTTPServer(('127.0.0.1',18085),Preview)
        print('Preview only: http://127.0.0.1:18085/exam-number',flush=True)
        try:server.serve_forever()
        finally:server.server_close()


if __name__=='__main__':main()
