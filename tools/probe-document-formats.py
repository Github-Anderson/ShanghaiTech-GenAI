"""Opt-in real uploads of synthetic fixtures; never print credentials or response bodies.

Supply genuine Office fixtures containing FORMAT_PROBE_<EXT>_20261101.
Direct mode probes the website allowlist; gateway mode verifies admitted formats.
"""
import argparse
import json
import uuid
from pathlib import Path

import httpx

MIMES = {
    'doc': 'application/msword',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'ppt': 'application/vnd.ms-powerpoint',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'html': 'text/html', 'json': 'application/json', 'xml': 'application/xml',
    'txt': 'text/plain',
}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--gateway', action='store_true')
    parser.add_argument('--chat', action='store_true', help='Also verify DeepSeek reads each gateway attachment')
    parser.add_argument('--extensions', default=','.join(ext for ext in MIMES if ext != 'txt'))
    args = parser.parse_args()
    if args.chat and not args.gateway:
        parser.error('--chat requires --gateway')
    for ext, template in {
        'html': '<!DOCTYPE html><html><body><p>{}</p></body></html>',
        'json': '{{"marker":"{}"}}', 'xml': '<?xml version="1.0"?><probe>{}</probe>',
        'txt': '{}',
    }.items():
        (args.fixtures / ('probe.' + ext)).write_text(
            template.format('FORMAT_PROBE_' + ext.upper() + '_20261101'), encoding='utf-8')
    if args.gateway:
        key = (args.private / 'gateway.key').read_text(encoding='utf-8-sig').strip()
        headers = {'Authorization': 'Bearer ' + key}
    else:
        credentials = json.loads((args.private / 'browser-credentials/credentials.json').read_text(encoding='utf-8-sig'))
        headers = {'X-Access-Token': credentials['main']['headers']['X-Access-Token'],
                   'Origin': 'https://genai.shanghaitech.edu.cn',
                   'Referer': 'https://genai.shanghaitech.edu.cn/dashboard/analysis',
                   'X-Requested-With': 'XMLHttpRequest'}
    with httpx.Client(timeout=180, trust_env=False, follow_redirects=False) as client:
        failures = 0
        for ext in dict.fromkeys(['txt'] + args.extensions.split(',')):
            raw = (args.fixtures / ('probe.' + ext)).read_bytes()
            marker = 'FORMAT_PROBE_' + ext.upper() + '_20261101'
            try:
                response = client.post(
                    'http://127.0.0.1:8000/v1/files' if args.gateway else
                    'https://genai.shanghaitech.edu.cn/htk/chat/upload/chat/file',
                    headers=headers,
                    params=None if args.gateway else {'chatGroupId': uuid.uuid4().hex, 'aiType': 'deepseek-pro'},
                    data={'model': 'deepseek-pro', 'purpose': 'user_data'} if args.gateway else {'biz': 'temp'},
                    files={'file': ('probe.' + ext, raw, MIMES[ext])})
                body = response.json()
                result = body if args.gateway else body.get('result')
                result = result if isinstance(result, dict) else {}
                text = result.get('parsed_content' if args.gateway else 'content', '')
                ready = result.get('status') == 'ready' if args.gateway else body.get('success') is True and body.get('code') in (0, 200)
                row = {'extension': ext, 'mode': 'gateway' if args.gateway else 'website',
                       'http': response.status_code, 'code': body.get('code'), 'ready': ready,
                       'file_id': bool(result.get('id' if args.gateway else 'fileId')),
                       'text_chars': len(text) if isinstance(text, str) else 0,
                       'marker_found': isinstance(text, str) and marker in text}
                row['passed'] = response.status_code == 200 and ready and row['file_id'] and row['marker_found']
                if args.chat and row['passed']:
                    chat = client.post('http://127.0.0.1:8000/v1/chat/completions', headers=headers, json={
                        'model': 'deepseek-pro', 'max_tokens': 128,
                        'messages': [{'role': 'user', 'content': [
                            {'type': 'text', 'text': 'Return only the exact FORMAT_PROBE marker found in the attached document.'},
                            {'type': 'file', 'file': {'file_id': result['id']}}]}]})
                    answer = chat.json().get('choices', [{}])[0].get('message', {}).get('content') or ''
                    row['chat_http'] = chat.status_code
                    row['chat_marker_found'] = marker in answer
                    row['passed'] = chat.status_code == 200 and row['chat_marker_found']
                # Classify messages without exposing arbitrary response text or secrets.
                message = body.get('message', '')
                if isinstance(message, str):
                    if 'token' in message.lower() and any(s in message for s in ('失效', '过期', '登录')):
                        row['rejection'] = 'website_credentials_expired'
                    elif any(s in message for s in ('不支持', '文件类型', '文件格式')):
                        row['rejection'] = 'website_format_rejected'
                if args.gateway and result.get('id'):
                    client.delete('http://127.0.0.1:8000/v1/files/' + result['id'], headers=headers)
            except Exception as error:
                row = {'extension': ext, 'passed': False, 'error_type': type(error).__name__}
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if not row['passed']:
                failures += 1
                if ext == 'txt':
                    print(json.dumps({'aborted': 'TXT control failed; format support cannot be determined'}), flush=True)
                    return 2
        return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
