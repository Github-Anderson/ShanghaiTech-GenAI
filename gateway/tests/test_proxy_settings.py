"""Explicit upstream proxy configuration; no real credentials or network calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from gateway.protocol import GatewayError
from gateway.settings import load_settings


class ProxySettingsTests(unittest.TestCase):
    def load(self, **extra):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'container.json'
            config.write_text(json.dumps({'gateway_key_file':'synthetic.key', 'models':{'test':{'root_ai_type':'xinference'}}, **extra}))
            with patch.dict(os.environ, {'SHANGHAITECH_GENAI_CONFIG':str(config), 'HTTPS_PROXY':'http://ignored.invalid:7897'}):
                return load_settings()

    def test_proxy_is_opt_in(self):
        self.assertIsNone(self.load().upstream_proxy_url)
        self.assertIsNone(self.load(upstream_proxy_url=None).upstream_proxy_url)

    def test_explicit_proxy(self):
        self.assertEqual(self.load(upstream_proxy_url='http://host.docker.internal:7897').upstream_proxy_url,
                         'http://host.docker.internal:7897')

    def test_invalid_proxy_does_not_leak_configuration(self):
        for value in ['', 123, 'file:///tmp/proxy', 'http://user:synthetic-secret@proxy:80',
                      'http://proxy:invalid', 'http://proxy/path', 'http://proxy?secret=synthetic-secret']:
            with self.subTest(value=value), self.assertRaises(GatewayError) as caught:
                self.load(upstream_proxy_url=value)
            self.assertEqual(caught.exception.code, 'configuration_required')
            self.assertNotIn('synthetic-secret', str(caught.exception))
