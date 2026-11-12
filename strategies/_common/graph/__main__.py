"""python -m strategies._common.graph {run,serve,types}."""
import argparse
import json
from pathlib import Path

from .api import GraphHTTPServer
from .service import GraphService, json_value, user_message
from .core import GraphError
from strategies._common.validation.decision import UnderdeterminedError


def serve_ui(args):
    from .fixture import fixture_service, close_fixture
    from .output_guard import quiet_worker_output
    service = server = None
    try:
        with quiet_worker_output():
            if args.fixture:
                service = fixture_service(Path(__file__).resolve().parents[3], args.graph)
            else:
                service = GraphService(args.root or Path.cwd(), cache_dir=args.cache_dir, ledger_path=args.ledger)
                try:
                    service.initial_graph_path = service.confined(args.graph).relative_to(service.root).as_posix()
                    service.load(args.graph)
                except GraphError:
                    # The UI retries the validated path through the existing load
                    # endpoint so its controlled edge error remains reviewable.
                    pass
            server = GraphHTTPServer(('127.0.0.1', args.port), service, public_hosts=args.public_host,
                                     ui_dist=Path(__file__).parent / 'ui' / 'dist')
        print(f'Live Strategy Graph UI: http://127.0.0.1:{server.server_port}', flush=True)
        server.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print(json.dumps({'error': '策略圖介面無法啟動或繼續執行'}, ensure_ascii=False))
        return 1
    finally:
        if server is not None:
            server.server_close()
        if service is not None:
            if service.active:
                service._token.cancel()
                service._thread.join()
            if args.fixture:
                try:
                    with quiet_worker_output():
                        close_fixture(service)
                except Exception:
                    print(json.dumps({'error': '測試資料清理未能完成'}, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Live Strategy Graph (CPU)')
    parser.add_argument('command', choices=['run','serve','types','ui'])
    parser.add_argument('--graph', default='strategies/tsmom_tx_mtx/graph.json')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--port', type=int, default=9102)
    parser.add_argument('--fixture', action='store_true')
    parser.add_argument('--public-host', action='append', default=[], metavar='NAME',
                        help='name a trusted reverse proxy serves this UI under (still binds 127.0.0.1)')
    args = parser.parse_args(argv)
    import re
    if any(not re.fullmatch(r'[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+', h.lower())
           for h in args.public_host):
        parser.error('--public-host 必須是主機名稱（不含 port 與 scheme）')
    if args.fixture and (args.command != 'ui' or args.root or args.cache_dir or args.ledger):
        parser.error('測試資料模式僅適用於 ui，且不可覆寫儲存位置')
    if args.command == 'ui':
        return serve_ui(args)
    service = GraphService(args.root or Path.cwd(), cache_dir=args.cache_dir, ledger_path=args.ledger)
    if args.command == 'types':
        print(json.dumps(service.registry.describe(),ensure_ascii=False))
        return 0
    if args.command == 'serve':
        server = GraphHTTPServer(('127.0.0.1',args.port),service,public_hosts=args.public_host)
        print(f'Live Strategy Graph API: http://127.0.0.1:{server.server_port}',flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            if service.active:
                service._token.cancel()
        finally:
            server.server_close()
        return 0
    try:
        service.load(args.graph)
        job = service.start(preview=args.preview)
        try:
            service._thread.join()
        except KeyboardInterrupt:
            service.cancel(job['id'])
            service._thread.join()
        states = {node_id: {**state, **({'message': user_message(state['message'])} if 'message' in state else {})}
                  for node_id, state in service.engine.states.items()}
        result = {**service.job(job['id']), 'nodes': states, 'ledger':service.ledger.summary()}
        print(json.dumps(json_value(result),ensure_ascii=False,sort_keys=True))
        return 0 if result['status'] == 'complete' else 1
    except (GraphError, UnderdeterminedError) as exc:
        print(json.dumps({'error': user_message(exc)}, ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps({'error':'策略圖無法開始執行'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
