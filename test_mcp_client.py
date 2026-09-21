import io
import json
import unittest
from email.message import Message

from mcp_client import decode_response, result_text


class McpResponseTest(unittest.TestCase):
    def test_decodes_sse_jsonrpc_result(self):
        headers = Message()
        headers["Content-Type"] = "text/event-stream"
        body = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'

        self.assertEqual(decode_response(headers, body), {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})

    def test_extracts_text_and_reports_tool_errors(self):
        result = {"content": [{"type": "text", "text": "hello"}], "isError": False}
        self.assertEqual(result_text(result), "hello")

        result["isError"] = True
        with self.assertRaisesRegex(RuntimeError, "hello"):
            result_text(result)


if __name__ == "__main__":
    unittest.main()
