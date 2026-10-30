"""dry run 快照推送：Bearer 與內容、未設定時略過、被拒時非零、金鑰不外洩到輸出。"""
import http.server
import threading

from strategies._common.dryrun import push


def _serve(status=204):
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            got["auth"] = self.headers.get("Authorization")
            got["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(status)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def _conf(tmp_path, monkeypatch, port):
    (tmp_path / "u").write_text(f"http://127.0.0.1:{port}/x", encoding="utf-8")
    (tmp_path / "k").write_text("SECRET-KEY-123", encoding="utf-8")
    monkeypatch.setenv("DRYRUN_PUSH_URL_FILE", str(tmp_path / "u"))
    monkeypatch.setenv("DRYRUN_PUSH_KEY_FILE", str(tmp_path / "k"))
    f = tmp_path / "s.json"
    f.write_text("{}", encoding="utf-8")
    return f


def test_push_sends_bearer_and_body():
    srv, got = _serve(204)
    try:
        code = push.push_snapshot(f"http://127.0.0.1:{srv.server_port}/x", "KEY123", b'{"a":1}')
    finally:
        srv.shutdown()
    assert code == 204 and got["auth"] == "Bearer KEY123" and got["body"] == b'{"a":1}'


def test_cli_without_config_warns_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DRYRUN_PUSH_URL_FILE", str(tmp_path / "none_url"))
    monkeypatch.setenv("DRYRUN_PUSH_KEY_FILE", str(tmp_path / "none_key"))
    f = tmp_path / "s.json"
    f.write_text("{}", encoding="utf-8")
    assert push.main(["--file", str(f)]) == 0
    assert "未設定" in capsys.readouterr().out


def test_cli_success_and_key_never_printed(tmp_path, monkeypatch, capsys):
    srv, _ = _serve(204)
    try:
        f = _conf(tmp_path, monkeypatch, srv.server_port)
        assert push.main(["--file", str(f)]) == 0
    finally:
        srv.shutdown()
    out = capsys.readouterr()
    assert "SECRET-KEY-123" not in out.out + out.err


def test_cli_nonzero_on_rejected(tmp_path, monkeypatch, capsys):
    srv, _ = _serve(403)
    try:
        f = _conf(tmp_path, monkeypatch, srv.server_port)
        assert push.main(["--file", str(f)]) == 2
    finally:
        srv.shutdown()
    out = capsys.readouterr()
    assert "403" in out.out and "SECRET-KEY-123" not in out.out + out.err


def test_cli_nonzero_on_network_error(tmp_path, monkeypatch, capsys):
    f = _conf(tmp_path, monkeypatch, 1)          # 1 號埠沒有服務
    assert push.main(["--file", str(f)]) == 2
    assert "SECRET-KEY-123" not in capsys.readouterr().out
