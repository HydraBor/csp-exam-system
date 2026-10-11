"""Export private problem packages in CSP and LiteOJ seed formats (offline)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from csp_exam.core import database, judgelocal, localoj, problems, store
from csp_exam.core.runtime_lock import data_lock


def export(output, selected=None):
    output=Path(output).resolve()
    if output.is_relative_to(Path(store.DATA_DIR).resolve()):
        raise ValueError("导出包须保存在运行数据目录之外。")
    with data_lock(store.DATA_DIR), store._LOCK:
        rows=[r for r in localoj.list_problems() if not problems.is_deleted(r['pid'])]
        if selected:
            rows=[r for r in rows if r['pid'] in selected]
            if set(selected)!={r['pid'] for r in rows}:raise ValueError("指定题目不存在或已删除。")
        entries={}
        info=problems.load_problem_info()
        for row in rows:
            pid=row['pid'];rec=info.get(pid) or {};prefix=pid+'/'
            text=localoj.problem_statement(pid)
            entries[prefix+'题目.md']=text.encode()
            name=problems.name_of_pid(pid)
            config={'title':row['title'],'name':name,'time_ms':rec.get('time_ms',1000),'memory_mb':rec.get('memory_mb',256)}
            entries[prefix+'problem.json']=json.dumps(config,ensure_ascii=False,indent=2).encode()
            if rec.get('std'):entries[prefix+'标程.cpp']=rec['std'].encode()
            cases=[]
            testcases=judgelocal.cases_of(pid)
            for i,case in enumerate(testcases):
                stem=case['name']
                if case.get('out') is None:raise ValueError(f"题目 {pid} 缺答案，不能导出。")
                entries[prefix+'data/'+stem+'.in']=case['in']
                entries[prefix+'data/'+stem+'.out']=case['out']
                cases.append({'inputPath':f'problems/{pid}/testdata/{stem}.in','outputPath':f'problems/{pid}/testdata/{stem}.out','score':100//len(testcases)+(i<100%len(testcases)),'sort':i+1})
            samples=problems.load_samples(pid)
            for item in samples.get('items',[]):
                for file in item.get('files',[]):
                    source=problems.sample_path(pid,file['name'])
                    if source:entries[prefix+'samples/'+file['name']]=Path(source).read_bytes()
            seed={'id':pid,'title':row['title'],'description':text,'timeLimit':config['time_ms'],'memoryLimit':config['memory_mb'],
                  'checkerMode':'standard','isPublic':0,'tags':[],'difficulty':'unrated','cases':cases,
                  'csp':{'submissionName':name,'problemNumber':problems.code_of_pid(pid),'scoreMode':'independent-cases'}}
            entries[prefix+'liteoj-seed.json']=json.dumps(seed,ensure_ascii=False,indent=2).encode()
        manifest={'format':'csp-library/1','problems':[r['pid'] for r in rows],
                  'sha256':{k:hashlib.sha256(v).hexdigest() for k,v in entries.items()}}
        entries['library-manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
        output.parent.mkdir(parents=True,exist_ok=True)
        with os.fdopen(os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as stream,zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,data in entries.items():archive.writestr(name,data)
    return {'file':str(output),'problems':len(rows),'files':len(entries)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path);parser.add_argument('--pid',action='append')
    args=parser.parse_args()
    print(json.dumps(export(args.output,args.pid),ensure_ascii=False))


if __name__=='__main__':main()
