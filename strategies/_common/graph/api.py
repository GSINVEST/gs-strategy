"""Local JSON API; all mutations require same-origin application/json requests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, unquote

from . import agent_trader
from .core import GraphError
from .service import GraphConflict, json_value, restore_sidecar, user_message


class GraphHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, service, ui_dist=None):
        self.service = service
        self.ui_dist = Path(ui_dist).resolve() if ui_dist else None
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        # Request URLs/bodies may contain confidential input; do not log them.
        pass

    def reply(self, status, data):
        if isinstance(data, dict) and 'error' in data:
            data = {**data, 'error': user_message(data['error'])}
        body = json.dumps(json_value(data), ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def reply_raw(self, status, content_type, body):
        # agent_trader 頁面自帶嚴格 CSP（只允許同源外部腳本）。
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for name, value in agent_trader.SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, mutation):
        s = self.server.service
        try:
            host = self.headers.get('Host', '')
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if host not in allowed:
                return self.reply(403, {'error': 'local Host required'})
            origin = self.headers.get('Origin')
            if origin is not None and origin != 'http://' + host:
                return self.reply(403, {'error': 'same origin required'})
            body = {}
            if mutation:
                if self.headers.get_content_type() != 'application/json':
                    return self.reply(415, {'error': 'application/json required'})
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2_000_000:
                    return self.reply(413, {'error': 'JSON body required; maximum 2 MB'})
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise GraphError('body must be an object')
            url = urlsplit(self.path)
            parts = url.path.strip('/').split('/')
            query = parse_qs(url.query)
            if not mutation and parts[:1] == ['agent-trader']:
                return self.reply_raw(*agent_trader.handle_get(parts, query, self.headers))
            if mutation and parts == ['api','agent-trader','decide']:
                return self.reply(*agent_trader.handle_decide(body, self.headers))
            if not mutation and parts == ['api','session']:
                return self.reply(200, s.session())
            if not mutation and parts == ['api','run-estimate']:
                return self.reply(200, s.run_estimate())
            if parts == ['api','layout']:
                if mutation and not isinstance(body.get('positions'), dict):
                    raise GraphError('layout positions must be an object')
                return self.reply(200, s.layout(body['positions']) if mutation else s.layout())
            if not mutation and parts == ['api','node-types']:
                return self.reply(200, {'node_types': s.registry.describe()})
            if not mutation and parts == ['api','graph']:
                return self.reply(200, s.document())
            if not mutation and parts == ['api','fsm']:
                return self.reply(200, s.fsm_view())
            if not mutation and parts == ['api','replay']:
                return self.reply(200, s.replay())
            if not mutation and parts == ['api','ledger']:
                return self.reply(200, s.ledger.summary())
            if not mutation and parts == ['api','nodes']:
                return self.reply(200, {'nodes': [s.node(i) for i in list(s.engine.states)]})
            if not mutation and len(parts) == 3 and parts[:2] == ['api','nodes']:
                limit = min(1000, max(1, int(query.get('limit',['60'])[0])))
                offset = max(0, int(query.get('offset',['0'])[0]))
                return self.reply(200, s.node(parts[2],limit,offset))
            if not mutation and len(parts) == 3 and parts[:2] == ['api','jobs']:
                if parts[2] not in s.jobs:
                    return self.reply(404, {'error': 'unknown job'})
                return self.reply(200, s.job(parts[2]))
            if mutation and parts == ['api','graph','load']:
                return self.reply(200, s.load(body['path'], expected_revision=body.get('expected_revision')))
            if mutation and parts == ['api','graph']:
                return self.reply(200, s.set_graph(body['graph'], body.get('path'), expected_revision=body.get('expected_revision')))
            if mutation and parts == ['api','graph','save']:
                return self.reply(200, s.save(body.get('path'), expected_revision=body.get('expected_revision')))
            if mutation and parts == ['api','graph','from-sidecar']:
                with s.lock:
                    s.check_revision(body.get('expected_revision'))
                    doc = json.loads(s.confined(body['path'],sidecar=True).read_text(encoding='utf-8'))
                    restored = restore_sidecar(doc, s.registry)
                    document = s.set_graph(restored['graph'], body.get('graph_path'))
                return self.reply(200, {**document, 'recorded_graph_hash':restored['graph_hash'], 'warnings':restored['warnings']})
            if mutation and len(parts) == 4 and parts[:2] == ['api','nodes'] and parts[3] == 'params':
                return self.reply(200, s.parameters(parts[2], body['params'], expected_revision=body.get('expected_revision')))
            if mutation and parts in (['api','preview'],['api','run']):
                return self.reply(202, s.start(preview=True) if parts[-1] == 'preview' else s.start(expected_revision=body.get('expected_revision'), expected_backtest_key=body.get('expected_backtest_key')))
            if mutation and len(parts) == 4 and parts[:2] == ['api','jobs'] and parts[3] == 'cancel':
                result = s.cancel(parts[2])
                return self.reply(202 if result['accepted'] else 409, result)
            if not mutation and self.server.ui_dist and (url.path == '/' or url.path.startswith('/assets/')):
                root = self.server.ui_dist
                relative = 'index.html' if url.path == '/' else unquote(url.path).lstrip('/')
                target = (root / relative).resolve()
                if target.is_relative_to(root) and target.is_file():
                    data = target.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; script-src 'self'; frame-ancestors 'none'")
                    self.send_header('X-Frame-Options', 'DENY')
                    self.end_headers()
                    self.wfile.write(data)
                    return
            return self.reply(404, {'error': 'unknown endpoint'})
        except GraphConflict as exc:
            return self.reply(409, {'code': exc.code, 'error': str(exc), 'revision': exc.revision})
        except GraphError as exc:
            return self.reply(409 if 'active' in str(exc) else 400, {'error': str(exc)})
        except (KeyError, ValueError, TypeError, OSError):
            return self.reply(400, {'error': 'invalid request or unavailable file'})
        except Exception:
            return self.reply(500, {'error': 'operation failed'})
