"""Loopback-only UI fixture; all accounts/results live in TemporaryDirectory."""
import os
from pathlib import Path
import sys
import tempfile
from http.server import ThreadingHTTPServer


def main():
    with tempfile.TemporaryDirectory(prefix='csp-affiliation-ui-') as root:
        os.environ['CSP_EXAM_DATA_DIR']=root+'/data'
        os.environ['CSP_EXAM_LOG_DIR']=root+'/logs'
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
        from csp_exam.core import store
        from csp_exam.web.server import Handler
        groups=[('南山提高班','深圳南山','张老师',['同名学员','林同学','陈同学']),
                ('福田提高班','深圳福田','李老师',['同名学员','李同学','王同学']),
                ('广州提高班','广州天河','张老师',['同名学员','吴同学','赵同学'])]
        c=store.create_contest('校区联赛 · 界面验收（临时数据）',rule='OI',start_at=1,end_at=2)
        problems=[{'no':i,'pid':'UI'+str(i),'name':n,'title':'界面测试题','full':100} for i,n in enumerate(['panel','beacon','frame','archive','contact','project'],1)]
        store.save_exam(c['id'],{'problems':problems,'released':True})
        for title,campus,teacher,names in groups:
            g=store.create_group(title,names,campus=campus,teacher=teacher)
            store.assign_kaohaos(c['id'],names,campus=campus,teacher=teacher,group_id=g['gid'],group_name=title)
        store.assign_kaohaos(c['id'],['历史未填写学员'])
        roster=store.load_roster(c['id']);results={}
        for i,(kh,row) in enumerate(roster.items()):
            if i>=8:continue
            values=[100,100,max(0,100-i*15),80,50 if i%2 else 100,20]
            results[kh]={'submitted_at':'界面验收','problems':{f'T{j}':{'score':v} for j,v in enumerate(values,1)}}
        store.save_results(c['id'],results)
        class Preview(Handler):
            def _check_admin(self,key=''):return True
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',18084),Preview)
        print('Preview: http://127.0.0.1:18084/scoreboard?c='+c['id'],flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()


if __name__=='__main__':main()
