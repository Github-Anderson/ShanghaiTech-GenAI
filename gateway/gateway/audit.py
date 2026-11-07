"""Bounded private request and login journals; never capture raw bodies or headers."""
import asyncio
import json
import secrets
import sqlite3
import time


class AuditStore:
    REQUEST_LIMIT = 2000
    LOGIN_LIMIT = 500
    QUIET_PATHS = frozenset(('/v1/admin/status', '/v1/admin/logs', '/v1/admin/login-history', '/healthz', '/readyz'))

    def __init__(self, path):
        self.path = path
        self.active = {}
        self.available = False
        self.db = None
        try:
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(path) if path else ':memory:')
            if path:
                path.chmod(0o600)
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, time REAL NOT NULL, data TEXT NOT NULL)')
            self.db.execute('CREATE INDEX IF NOT EXISTS events_kind_time ON events(kind, time)')
            self.db.execute('CREATE TABLE IF NOT EXISTS metric_meta (name TEXT PRIMARY KEY, value REAL NOT NULL)')
            self.db.execute('INSERT OR IGNORE INTO metric_meta VALUES (?,?)', ('started_at', time.time()))
            self.db.execute('CREATE TABLE IF NOT EXISTS model_metrics (minute INTEGER PRIMARY KEY, complete INTEGER NOT NULL, failed INTEGER NOT NULL, cancelled INTEGER NOT NULL, duration_ms REAL NOT NULL)')
            self.prune()
            self.available = True
        except (OSError, sqlite3.Error):
            self.available = False

    def prune(self):
        for kind, days, limit in [('request', 7, self.REQUEST_LIMIT), ('login', 365, self.LOGIN_LIMIT)]:
            self.db.execute('DELETE FROM events WHERE kind=? AND time<?', (kind, time.time() - days * 86400))
            self.db.execute('DELETE FROM events WHERE kind=? AND id NOT IN (SELECT id FROM events WHERE kind=? ORDER BY id DESC LIMIT ?)', (kind, kind, limit))
        self.db.execute('DELETE FROM model_metrics WHERE minute<?', (int((time.time() - 7 * 86400) // 60) * 60,))
        self.db.commit()

    def add(self, kind, data, *, model_request=False):
        if not self.available:
            return
        try:
            now = time.time()
            self.db.execute('INSERT INTO events(kind,time,data) VALUES(?,?,?)', (kind, now, json.dumps(data, ensure_ascii=True)))
            if model_request and data.get('outcome') in ('complete', 'failed', 'cancelled'):
                outcome = data['outcome']
                self.db.execute('INSERT INTO model_metrics VALUES (?,?,?,?,?) ON CONFLICT(minute) DO UPDATE SET complete=complete+excluded.complete, failed=failed+excluded.failed, cancelled=cancelled+excluded.cancelled, duration_ms=duration_ms+excluded.duration_ms',
                    (int(now // 60) * 60, int(outcome == 'complete'), int(outcome == 'failed'), int(outcome == 'cancelled'), data.get('duration_ms', 0) if outcome == 'complete' else 0))
            self.prune()
        except sqlite3.Error:
            self.available = False

    def login(self, event, result, code=None):
        self.add('login', {'time': time.time(), 'event': event, 'result': result, 'error_code': code})

    def rows(self, kind, offset=0, limit=50, category='', outcome='', model=''):
        if not self.available:
            return {'items': [], 'total': 0, 'available': False, 'models': []}
        try:
            self.prune()
            rows = [dict(json.loads(raw), id=ident) for ident, raw in self.db.execute('SELECT id,data FROM events WHERE kind=? ORDER BY id DESC', (kind,))]
            if category:
                rows = [r for r in rows if r.get('category') == category]
            # Model choices reflect the category scope, before outcome and model narrowing.
            models = sorted({r['model'] for r in rows if isinstance(r.get('model'), str)})
            if outcome:
                rows = [r for r in rows if r.get('outcome') == outcome]
            if model:
                rows = [r for r in rows if r.get('model') == model]
            return {'items': rows[offset:offset + limit], 'total': len(rows), 'available': True, 'models': models}
        except sqlite3.Error:
            self.available = False
            return {'items': [], 'total': 0, 'available': False, 'models': []}

    def statistics(self, window='24h'):
        """Final outcomes, by completion minute; independent of bounded raw logs."""
        if window not in ('24h', '7d'):
            raise ValueError('Unsupported statistics window')
        empty = {'available': False, 'window': window}
        if not self.available:
            return empty
        try:
            now = time.time()
            start = int((now - (86400 if window == '24h' else 7 * 86400)) // 60) * 60
            since = self.db.execute("SELECT value FROM metric_meta WHERE name='started_at'").fetchone()[0]
            complete, failed, cancelled, duration = self.db.execute('SELECT COALESCE(SUM(complete),0),COALESCE(SUM(failed),0),COALESCE(SUM(cancelled),0),COALESCE(SUM(duration_ms),0) FROM model_metrics WHERE minute>=? AND minute<=?', (start, int(now // 60) * 60)).fetchone()
            total = complete + failed + cancelled
            return {'available': True, 'window': window, 'window_start': start, 'as_of': now,
                    'started_at': since, 'complete_window': since <= start,
                    'complete': complete, 'failed': failed, 'cancelled': cancelled, 'total': total,
                    'success_rate': round(complete / total * 100, 2) if total else None,
                    'average_duration_ms': round(duration / complete, 2) if complete else None}
        except sqlite3.Error:
            self.available = False
            return empty

    def close(self):
        if self.db:
            self.db.close()


API_PATHS = frozenset(('/v1/models', '/v1/capabilities', '/v1/files', '/v1/chat/completions'))
API_PREFIXES = ('/v1/files/', '/v1/conversations/')


def request_category(path):
    """Model API traffic is 'api'; console, monitoring, credentials and unmatched paths are 'management'."""
    return 'api' if path in API_PATHS or path.startswith(API_PREFIXES) else 'management'


def request_summary(data, models):
    """Only known catalog identifiers, booleans and counts enter the journal."""
    model = data.get('model')
    result = {'model': model if isinstance(model, str) and model in models else None}
    for name in ('stream', 'store'):
        if type(data.get(name)) is bool:
            result[name] = data[name]
    for name in ('max_tokens', 'max_completion_tokens'):
        if type(data.get(name)) is int and 0 < data[name] <= 10000000:
            result[name] = data[name]
    for name in ('messages', 'tools'):
        if isinstance(data.get(name), list):
            result[name + '_count'] = len(data[name])
    options = data.get('shanghaitech')
    if isinstance(options, dict):
        result['options'] = {k: v for k, v in options.items() if k in ('search', 'thinking') and type(v) is bool}
    usage = data.get('stream_options')
    if isinstance(usage, dict) and type(usage.get('include_usage')) is bool:
        result['include_usage'] = usage['include_usage']
    return result


class AuditMiddleware:
    """Pure ASGI wrapper preserves streaming/backpressure and disconnect semantics."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        service = getattr(scope['app'].state, 'service', None)
        if service is None:
            return await self.app(scope, receive, send)
        audit = service.audit
        started = time.monotonic()
        row = {'request_id': secrets.token_hex(8), 'time': time.time(), 'method': scope['method'] if scope['method'] in ('GET', 'POST', 'DELETE', 'PUT', 'PATCH', 'HEAD', 'OPTIONS') else 'OTHER', 'outcome': 'running'}
        path = scope.get('path', '')
        row['category'] = request_category(path)
        if path == '/v1/chat/completions':
            row['path'] = path
        scope.setdefault('state', {})['audit'] = row
        audit.active[row['request_id']] = row
        ended = False
        disconnected = False

        async def receiving():
            nonlocal disconnected
            message = await receive()
            if message['type'] == 'http.disconnect':
                disconnected = True
            return message

        async def sending(message):
            nonlocal ended
            if message['type'] == 'http.response.start':
                row['http_status'] = message['status']
                message = dict(message, headers=list(message.get('headers', [])) + [(b'x-request-id', row['request_id'].encode())])
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                ended = True

        try:
            await self.app(scope, receiving, sending)
        except asyncio.CancelledError:
            row['outcome'] = 'cancelled'
            raise
        except Exception:
            row.update(outcome='failed', error_code='internal_error')
            row.setdefault('http_status', 500)
            raise
        finally:
            route = scope.get('route')
            row['path'] = getattr(route, 'path', '<unmatched>')
            row['category'] = request_category(row['path'])
            if row['outcome'] == 'running':
                row['outcome'] = 'cancelled' if disconnected and not ended else 'complete' if ended and row.get('http_status', 500) < 400 else 'failed'
            row['duration_ms'] = round((time.monotonic() - started) * 1000, 2)
            audit.active.pop(row['request_id'], None)
            # Successful monitoring reads do not displace useful request history.
            quiet = (row['method'] == 'GET' and row['path'] in audit.QUIET_PATHS) or (row['path'] == '/v1/admin/probe' and scope['state'].get('monitoring'))
            if not (quiet and row['outcome'] == 'complete'):
                audit.add('request', row, model_request=bool(scope['state'].get('authorized')) and row['method'] == 'POST' and row['path'] == '/v1/chat/completions')
