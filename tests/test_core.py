"""Same functional guarantees as test_api.py, but calling app.services in-process (desktop flow).

Reuses the test functions from test_api.py by overriding the `server` fixture with an adapter that
translates the old HTTP calls into direct app.services calls. HTTP-only tests are removed.
"""
import os
import subprocess
import sys
import time

import pytest

from app import services
from conftest import ROOT, make_jpeg

import test_api
from test_api import *  # noqa: F401,F403  (re-collect functional tests under this module)

HTTP_ONLY = {"test_health", "test_listens_only_on_loopback", "test_rejects_foreign_host",
             "test_rejects_foreign_origin_on_mutation", "test_rejects_non_json_post",
             "test_same_origin_allowed", "test_static_ui_served", "test_dialog_without_picker",
             "test_job_404", "test_ffmpeg_missing"}
for _n in HTTP_ONLY:
    globals().pop(_n, None)


class _Resp:
    def __init__(self, code, data):
        self.status_code, self._data = code, data
        self.text = repr(data)

    def json(self):
        return self._data


class _Client:
    def get(self, path, **kw):
        if path.startswith("/api/jobs/"):
            try:
                return _Resp(200, services.get_job(path.rsplit("/", 1)[1]))
            except services.ServiceError as e:
                return _Resp(404, {"detail": str(e)})
        raise AssertionError(path)


class InProcess:
    """Adapter exposing the subset of the old HTTP API that test_api uses."""
    client = _Client()

    def post(self, path, body):
        try:
            if path == "/api/scan":
                return _Resp(200, services.scan(body["input_dir"], body.get("output_dir"),
                                                       body.get("recursive", False)))
            if path == "/api/jobs":
                b = dict(body)
                jid = services.start_job(b.pop("input_dir"), b.pop("output_dir"), **b)
                assert isinstance(jid, str)
                return _Resp(202, {"job_id": jid})
            if path.endswith("/cancel"):
                st = services.cancel_job(path.split("/")[-2])
                return _Resp(200, {"status": st})
        except services.ServiceError as e:
            code = 409 if "andamento" in str(e).lower() else 400
            return _Resp(code, {"detail": str(e)})
        raise AssertionError(path)

    def run_job(self, body, timeout=180):
        r = self.post("/api/jobs", body)
        assert r.status_code == 202, r.text
        jid = r.json()["job_id"]
        snaps, deadline = [], time.time() + timeout
        while time.time() < deadline:
            j = services.get_job(jid)
            snaps.append(j)
            if j["status"] in services.FINISHED_STATUSES:
                return j, snaps
            time.sleep(0.05)
        raise AssertionError(f"job timeout {j}")


@pytest.fixture(scope="module")
def server():
    yield InProcess()
    services.shutdown()


# ---------------------------------------------------------------- desktop-specific service checks
def test_health_inprocess():
    h = services.health()
    assert h["ffmpeg"]["available"] is True
    assert ".jpg" in h["formats"]["image"] and ".mp4" in h["formats"]["video"]


def test_unknown_job():
    with pytest.raises(services.ServiceError):
        services.get_job("nope")
    with pytest.raises(services.ServiceError):
        services.cancel_job("nope")


@pytest.mark.parametrize("kw", [{"noise_strength": 0}, {"noise_strength": 4}, {"metadata_mode": "x"}])
def test_invalid_options(media_dir, tmp_path, kw):
    make_jpeg(media_dir / "a.jpg")
    with pytest.raises(services.ServiceError) as e:
        services.start_job(str(media_dir), str(tmp_path / "o"), **kw)
    assert str(e.value)
    assert not (tmp_path / "o").exists() or not any((tmp_path / "o").iterdir())


def test_wait_job_and_progress_fields(media_dir, tmp_path):
    make_jpeg(media_dir / "a.jpg")
    jid = services.start_job(str(media_dir), str(tmp_path / "o"))
    f = services.wait_job(jid, timeout=60)
    assert f["status"] == "done" and f["processed"] == f["total"] == 1
    assert f["results"][0]["kind"] == "image"


def test_ffmpeg_missing_inprocess(media_dir, tmp_path):
    """Fresh interpreter: env must be honoured by services (videos fail per file, images work)."""
    make_jpeg(media_dir / "a.jpg")
    (media_dir / "b.mp4").write_bytes(b"x" * 100)
    code = f"""
import json
from app import services
h = services.health()
j = services.start_job({str(media_dir)!r}, {str(tmp_path / 'o')!r})
j = services.wait_job(j, 60)
print(json.dumps({{'h': h['ffmpeg'], 'r': {{r['source']: r for r in j['results']}}}}))
"""
    env = dict(os.environ, METAEDIT_FFMPEG="/nao/existe", METAEDIT_FFPROBE="/nao/existe")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    import json
    d = json.loads(out.stdout.strip().splitlines()[-1])
    assert d["h"]["available"] is False and d["h"]["error"]
    assert d["r"]["a.jpg"]["ok"] is True
    assert d["r"]["b.mp4"]["ok"] is False and "ffmpeg" in d["r"]["b.mp4"]["error"].lower()


def test_no_network_listener_in_desktop_services():
    """Importing/using services must not open sockets (desktop flow has no HTTP server)."""
    code = ("from app import services; services.health(); import time; time.sleep(1.5); "
            "import os; print(os.getpid())")
    p = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    time.sleep(1.0)
    ss = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
    p.wait(10)
    assert f"pid={p.pid}," not in ss, ss
