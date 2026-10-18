import concurrent.futures
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import database, localoj, store
from csp_exam.tools.backup_data import backup
from csp_exam.tools.migrate_storage import migrate


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir()
    def tearDown(self): self.tmp.cleanup()

    def legacy(self):
        documents = {"contests.json": [{"id": "c1", "title": "旧比赛"}],
          "problem_info.json": {"items": {"P": {"title": "题目", "name": "sum", "time_ms": 1500}}},
          "contests/c1/exam.json": {"problems": [{"pid": "P", "no": 1}]},
          "contests/c1/roster.json": {"GD-J12345": {"name": "张三"}},
          "contests/c1/results.json": {"GD-J12345": {"total": 100, "history": [{"score": 80}]}},
          "samples/P/manifest.json": {"pid": "P", "items": [{"label": "样例"}]}}
        for key, data in documents.items():
            p=self.root/key;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data),encoding="utf-8")
        p=self.root/"statements/P.md";p.parent.mkdir();p.write_bytes("# 原题面\n\n$a_i$\n".encode())
        return documents

    def test_migration_integrity_archive_no_stale_fallback(self):
        expected=self.legacy()
        report=migrate(self.root, Path(self.tmp.name)/"before.tgz", Path(self.tmp.name)/"legacy")
        self.assertEqual(report['verified_documents'],len(expected))
        self.assertEqual(report['verified_statements'],1)
        for key,value in expected.items():self.assertEqual(database.load(self.root,key,None),value)
        self.assertFalse((self.root/"contests.json").exists())
        database.save_statement(self.root,"P","# 已修改")
        (self.root/"statements/P.md").write_text("# 旧文件不得复活",encoding="utf-8")
        self.assertEqual(database.statement(self.root,"P"),"# 已修改")
        with database.connection(self.root) as db:
            row=db.execute("SELECT id,title,description,time_limit FROM problem_library").fetchone()
            self.assertEqual(row,("P","题目","# 已修改",1500))

    def test_corrupt_legacy_aborts_without_partial_migration(self):
        self.legacy();(self.root/"problem_info.json").write_text("{bad")
        with self.assertRaises(json.JSONDecodeError):migrate(self.root)
        (self.root/"problem_info.json").write_text('{"items":{}}')
        self.assertEqual(migrate(self.root)['integrity'],'ok')

    def test_transaction_rollback_and_restart(self):
        database.save(self.root,"contests.json",[])
        with self.assertRaises(RuntimeError):
            with database.transaction(self.root):
                database.save(self.root,"contests.json",[{"id":"bad"}])
                database.save_statement(self.root,"P","bad")
                raise RuntimeError("abort")
        self.assertEqual(database.load(self.root,"contests.json",None),[])
        self.assertEqual(database.statement(self.root,"P"),"")

    def test_concurrent_writes_and_backup_round_trip(self):
        def save(i):database.save(self.root,f"contests/c{i}/results.json",{"K":{"total":i}})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(save,range(100)))
        output=Path(self.tmp.name)/"snapshot.tgz";backup(self.root,output)
        restored=Path(self.tmp.name)/"restore";restored.mkdir()
        with tarfile.open(output) as tar:tar.extractall(restored,filter="data")
        for i in range(100):self.assertEqual(database.load(restored,f"contests/c{i}/results.json",None),{"K":{"total":i}})
        with database.connection(restored) as db:self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")

    def test_contest_deletion_removes_database_rows(self):
        with patch.object(store,"DATA_DIR",str(self.root)):
            cid=store.create_contest("测试",start_at=1,end_at=2)['id']
            self.assertTrue(store.delete_contest(cid))
            self.assertFalse(database.exists(self.root,f"contests/{cid}/exam.json"))
            self.assertEqual(store.create_contest("新比赛")['id'],cid)
            self.assertEqual(store.load_results(cid),{})

    def test_only_business_paths_are_routed(self):
        self.assertIsNone(database.key_for(self.root,self.root/"contests/c1/rejudge/uuid/results.json"))
        self.assertIsNone(database.key_for(self.root,self.root/"../secret.json"))
        self.assertEqual(database.key_for(self.root,self.root/"samples/P/manifest.json"),"samples/P/manifest.json")

    def test_single_result_read_does_not_decode_the_whole_contest(self):
        with patch.object(store,'DATA_DIR',str(self.root)):
            store.save_results('c1',{'K':{'score':3},'OTHER':{'source':'PRIVATE OTHER STUDENT'}})
            # A single student's request must never fall back to the bulk loader.
            with patch.object(database,'_get',side_effect=AssertionError('bulk result read')):
                self.assertEqual(store.get_result('c1','K'),{'score':3})
                self.assertIsNone(store.get_result('c1','MISSING'))
                self.assertEqual(store.get_result('c1','MISSING',{}),{})
                self.assertEqual(store.get_result('c1','K\' OR 1=1 --',{}),{})

    def test_single_result_update_preserves_other_rows_position_and_invalidates(self):
        key='contests/c1/results.json'
        with patch.object(store,'DATA_DIR',str(self.root)):
            store.put_result('c1','K',{'score':1})
            store.put_result('c1','OTHER',{'score':8})
            before=database.signature(self.root,[key])
            with database.connection(self.root) as db:
                other=db.execute('SELECT position,payload FROM records WHERE document_key=? AND item_key=?',(key,'OTHER')).fetchone()
            with patch.object(database,'_get',side_effect=AssertionError('bulk result write')):
                store.put_result('c1','K',{'score':100})
            self.assertNotEqual(database.signature(self.root,[key]),before)
            self.assertEqual(store.load_results('c1'),{'K':{'score':100},'OTHER':{'score':8}})
            with database.connection(self.root) as db:
                self.assertEqual(db.execute('SELECT position,payload FROM records WHERE document_key=? AND item_key=?',(key,'OTHER')).fetchone(),other)

    def test_single_update_rollback_and_wrapped_header(self):
        key='contests/c1/results.json'
        database.save(self.root,key,{'items':{'K':{'score':1}},'note':'keep header'})
        with self.assertRaises(RuntimeError):
            with database.transaction(self.root):
                database.put_item(self.root,key,'K',{'score':99})
                database.put_item(self.root,key,'NEW',{'score':3})
                raise RuntimeError('abort')
        self.assertEqual(database.load(self.root,key,None),{'items':{'K':{'score':1}},'note':'keep header'})
        database.put_item(self.root,key,'NEW',{'score':3})
        self.assertEqual(database.load(self.root,key,None),{'items':{'K':{'score':1},'NEW':{'score':3}},'note':'keep header'})

    def test_concurrent_single_writes_do_not_drop_siblings(self):
        with patch.object(store,'DATA_DIR',str(self.root)):
            def write(index):store.put_result('c1',str(index),{'score':index})
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                list(pool.map(write,range(50)))
            self.assertEqual(store.load_results('c1'),{str(i):{'score':i} for i in range(50)})


if __name__ == '__main__':unittest.main()
