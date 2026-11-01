"""Reject mismatched binary content while admitting verified website formats."""
import unittest

from gateway.attachments import Attachments, DOCUMENT_MIMES, MAX_FILE_BYTES
from gateway.protocol import GatewayError

MODELS = {'deepseek-pro': {'supports_files': True}}


class DocumentFormatTests(unittest.TestCase):
    def validate(self, suffix, data, mime=None):
        return Attachments.validate_file('probe' + suffix, mime or DOCUMENT_MIMES[suffix],
                                         data, 'deepseek-pro', MODELS)

    def test_binary_content_checks(self):
        for suffix, signature in [('.xls', bytes.fromhex('d0cf11e0a1b11ae1')),
                                  ('.ppt', bytes.fromhex('d0cf11e0a1b11ae1')),
                                  ('.xlsx', b'PK\x03\x04'), ('.pptx', b'PK\x03\x04')]:
            with self.subTest(suffix=suffix):
                self.assertEqual(self.validate(suffix, signature)[0], 'document')
                self.assertEqual(self.validate(suffix, signature, 'application/octet-stream')[0], 'document')
                for invalid in (b'plain text', b'%PDF-synthetic'):
                    with self.assertRaises(GatewayError):
                        self.validate(suffix, invalid)
                with self.assertRaises(GatewayError):
                    self.validate(suffix, signature, 'text/plain')

    def test_text_documents_and_size_boundary(self):
        for suffix, raw in [('.html', b'<p>marker</p>'), ('.json', b'{"marker":1}')]:
            with self.subTest(suffix=suffix):
                self.assertEqual(self.validate(suffix.upper(), raw, DOCUMENT_MIMES[suffix])[0], 'document')
                for invalid in (b'', b'\x00binary', b'x' * MAX_FILE_BYTES):
                    with self.assertRaises(GatewayError):
                        self.validate(suffix, invalid)

    def test_rejected_formats_stay_closed(self):
        for suffix, mime in [('.doc', 'application/msword'), ('.xml', 'application/xml')]:
            with self.subTest(suffix=suffix), self.assertRaises(GatewayError):
                self.validate(suffix, b'synthetic', mime)


if __name__ == '__main__':
    unittest.main()
