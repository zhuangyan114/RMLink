"""An old synchronous heartbeat may finish after close and a new open."""
import threading
import unittest
from rmlink_connector.client import ApiClient


class Client(ApiClient):
    def __init__(self):
        self.session=None;self._heartbeat=None;self._stop=threading.Event()
        self.opened=0;self.entered=threading.Event();self.release=threading.Event()
    def request(self,method,path,body=None):
        if method=='POST' and path=='/v1/sessions':
            self.opened+=1;return {'session_id':str(self.opened),'packet_size':512}
        if path=='/v1/sessions/1/heartbeat':
            self.entered.set();self.release.wait(5);raise ConnectionError('old request finished late')
        return {}


class ClientLifecycleTests(unittest.TestCase):
    def test_close_error_does_not_mask_original_capture_failure(self):
        client=Client()
        def broken_close():raise ConnectionError('cleanup failed')
        client.close=broken_close
        with self.assertRaisesRegex(ValueError,'original failure'):
            with client:raise ValueError('original failure')
        with self.assertRaisesRegex(ConnectionError,'cleanup failed'):
            with client:pass
    def test_failed_close_clears_the_local_lease_without_replaying_a_request(self):
        client=Client();original=client.request;deleted=[]
        def request(method,path,body=None):
            if method=='DELETE' and path=='/v1/sessions/1':
                deleted.append(path);raise ConnectionError('lost session')
            return original(method,path,body)
        client.request=request;client.open('model')
        with self.assertRaises(ConnectionError):client.close()
        self.assertIsNone(client.session);self.assertEqual(deleted,['/v1/sessions/1'])
        try:client.open('model');self.assertEqual(client.session,'2')
        finally:client.close()

    def test_old_failed_heartbeat_cannot_stop_the_reopened_session(self):
        client=Client();client.open('model');old_thread=client._heartbeat
        try:
            self.assertTrue(client.entered.wait(6),'Old heartbeat did not start')
            client.close();client.open('model');client.release.set();old_thread.join(2)
            self.assertFalse(old_thread.is_alive());self.assertFalse(client._stop.is_set())
            self.assertEqual(client.session,'2')
            with self.assertRaises(RuntimeError):client.open('model')
            self.assertEqual(client.opened,2)
        finally:client.release.set();client.close();old_thread.join(2)
