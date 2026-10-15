import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from rmlink_connector.__main__ import main
from rmlink_connector.recording import Recording
from test_capture import record


class OfflineRecordingCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_checks_a_file_without_a_service_token_or_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            async def failed():raise AssertionError('No capture failure expected')
            rec=Recording(directory,'model',{1:{'capture_id':7,'addresses':[0x20000100,0x20000104]}},failed)
            await rec.start();rec.offer(0,record(0));status=await rec.stop()
            output=io.StringIO()
            with patch('sys.argv',['rmlink','inspect-recording',str(rec.path)]),patch(
                    'rmlink_connector.__main__.ApiClient',side_effect=AssertionError('Offline command opened API')),contextlib.redirect_stdout(output):
                main()
            parsed=json.loads(output.getvalue())
            self.assertEqual(parsed['sha256'],status['sha256']);self.assertEqual(parsed['frames'],1)
            rec.path.write_bytes(b'RMLR')
            with patch('sys.argv',['rmlink','inspect-recording',str(rec.path)]),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:main()
                self.assertEqual(raised.exception.code,2)
