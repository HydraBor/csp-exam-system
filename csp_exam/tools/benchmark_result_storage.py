"""Measure Python allocation peaks using synthetic records in a temporary DB.

This is not a production load test or an estimate of the server's capacity.
"""
import gc
import json
import tempfile
import time
import tracemalloc
from unittest.mock import patch

from ..core import store


def main():
    with tempfile.TemporaryDirectory(prefix='csp-result-memory-') as directory:
        with patch.object(store,'DATA_DIR',directory):
            store.save_results('bench',{str(i):{'score':0,'testcases':[{'input':'x'*400000}]} for i in range(45)})
            def measure(action):
                gc.collect();tracemalloc.start();started=time.perf_counter()
                action()
                _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
                return {'python_peak_bytes':peak,'wall_seconds':round(time.perf_counter()-started,4)}
            def bulk_write():
                rows=store.load_results('bench');rows['0']['score']=1
                store.save_results('bench',rows)
            def point_write():
                row=store.get_result('bench','0');row['score']=2
                store.put_result('bench','0',row)
            report={'synthetic_students':45,'payload_bytes_per_student':400000,
                    'bulk_read':measure(lambda:store.load_results('bench').get('0')),
                    'single_read':measure(lambda:store.get_result('bench','0')),
                    'bulk_write':measure(bulk_write),'single_write':measure(point_write)}
            assert store.get_result('bench','0')['score']==2
            assert all(row['score']==0 for key,row in store.load_results('bench').items() if key!='0')
            report['sibling_records_preserved']=True
            print(json.dumps(report,indent=2))


if __name__=='__main__':main()
