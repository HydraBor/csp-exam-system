"""Exercise bounded admission with isolated sockets and no judge/database load."""
import http.client
from contextlib import closing
from http.server import BaseHTTPRequestHandler
import threading
import unittest
from unittest.mock import patch

from csp_exam.web.server import Handler, LimitedThreadingHTTPServer


class HttpCapacity(unittest.TestCase):
    def test_overload_is_bounded_and_accepts_again_after_request_finishes(self):
        entered=threading.Event();release=threading.Event()
        class SlowHandler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/slow':
                    entered.set();release.wait(4)
                self.send_response(200);self.send_header('Content-Length','2')
                self.end_headers();self.wfile.write(b'OK')
        server=LimitedThreadingHTTPServer(('127.0.0.1',0),SlowHandler,max_workers=1)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        first=http.client.HTTPConnection(*server.server_address,timeout=5)
        try:
            first.request('GET','/slow');self.assertTrue(entered.wait(2))
            for _ in range(5):
                with closing(http.client.HTTPConnection(*server.server_address,timeout=2)) as conn:
                    conn.request('GET','/health');response=conn.getresponse()
                    self.assertEqual(response.status,503)
                    self.assertEqual(response.getheader('Retry-After'),'2')
                    self.assertEqual(response.getheader('Cache-Control'),'no-store');response.read()
            release.set();response=first.getresponse()
            self.assertEqual(response.status,200);self.assertEqual(response.read(),b'OK')
            # Once this slot is released it must be reusable, rather than leak.
            self.assertTrue(server._request_slots.acquire(timeout=2));server._request_slots.release()
            with closing(http.client.HTTPConnection(*server.server_address,timeout=2)) as conn:
                conn.request('GET','/health');response=conn.getresponse()
                self.assertEqual(response.status,200);response.read()
        finally:
            release.set();first.close();server.shutdown();server.server_close();thread.join()

    def test_disconnected_client_does_not_trigger_another_error_response(self):
        handler=Handler.__new__(Handler)
        for method,route in ((handler.do_GET,'_route_get'),(handler.do_POST,'_route_post')):
            for error in (BrokenPipeError(),ConnectionResetError(),TimeoutError()):
                handler.path='/'
                with patch.object(handler,route,side_effect=error),patch.object(handler,'_send') as send:
                    method();send.assert_not_called()
                    self.assertTrue(handler.close_connection)

    def test_failed_thread_start_returns_the_slot(self):
        server=LimitedThreadingHTTPServer(('127.0.0.1',0),Handler,max_workers=1)
        try:
            with patch('threading.Thread.start',side_effect=RuntimeError('cannot start')):
                with self.assertRaises(RuntimeError):server.process_request(None,('127.0.0.1',0))
            self.assertTrue(server._request_slots.acquire(blocking=False))
            server._request_slots.release()
        finally:server.server_close()


if __name__=='__main__':unittest.main()
