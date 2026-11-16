"""Fixtures for integration tests: real server subprocess + synthetic media."""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self, env_extra=None):
        self.port = free_port()
        env = dict(os.environ, METAEDIT_NO_PICKER="1", **(env_extra or {}))
        self.proc = subprocess.Popen(
            [PY, "-m", "app", "--port", str(self.port), "--no-browser"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.base = f"http://127.0.0.1:{self.port}"
        self.client = httpx.Client(base_url=self.base, timeout=60)
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if self.client.get("/api/health").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        out = self.proc.stdout.read().decode() if self.proc.poll() is not None else ""
        self.stop()
        raise RuntimeError(f"server did not start: {out}")

    def stop(self):
        self.client.close()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def post(self, path, body):
        return self.client.post(path, json=body)

    def run_job(self, body, timeout=180):
        r = self.post("/api/jobs", body)
        assert r.status_code == 202, r.text
        jid = r.json()["job_id"]
        deadline = time.time() + timeout
        snapshots = []
        while time.time() < deadline:
            j = self.client.get(f"/api/jobs/{jid}").json()
            snapshots.append(j)
            if j["status"] in ("done", "error", "cancelled"):
                return j, snapshots
            time.sleep(0.1)
        raise AssertionError(f"job timeout: {j}")


@pytest.fixture(scope="session")
def server():
    s = Server()
    yield s
    s.stop()


def sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tree_hashes(d: Path) -> dict:
    return {str(p.relative_to(d)): sha256(p) for p in sorted(d.rglob("*")) if p.is_file()}


def gradient(w, h, alpha=False):
    y, x = np.mgrid[0:h, 0:w]
    r = (x * 255 // max(w - 1, 1)).astype(np.uint8)
    g = (y * 255 // max(h - 1, 1)).astype(np.uint8)
    b = ((x + y) * 127 // max(w + h - 2, 1)).astype(np.uint8)
    chans = [r, g, b]
    if alpha:
        a = np.zeros((h, w), np.uint8)
        a[:, w // 2:] = 255
        a[: h // 4, :] = 128
        chans.append(a)
    return np.dstack(chans)


def make_jpeg(path: Path, w=64, h=32, orientation=None, quality=95):
    im = Image.fromarray(gradient(w, h))
    exif = Image.Exif()
    exif[0x010F] = "SecretCam Maker"     # Make
    exif[0x0110] = "SecretModel X1"      # Model
    exif[0x0131] = "OrigSoftware 1.0"    # Software
    exif[0x013B] = "Original Artist"     # Artist
    exif[0x8298] = "Orig Copyright"      # Copyright
    exif[0x0132] = "2020:01:02 03:04:05"
    if orientation:
        exif[0x0112] = orientation
    gps = {1: "N", 2: (12.0, 34.0, 56.0), 3: "W", 4: (65.0, 43.0, 21.0)}
    exif[0x8825] = gps
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" dc:creator="XMPSECRET"/></rdf:RDF></x:xmpmeta>')
    im.save(path, "JPEG", quality=quality, exif=exif.tobytes(), xmp=xmp, comment=b"JPEGCOMSECRET")
    return path


def make_png(path: Path, w=40, h=30, alpha=True):
    im = Image.fromarray(gradient(w, h, alpha=alpha), "RGBA" if alpha else "RGB")
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "PNGSECRET")
    info.add_text("Comment", "PNGCOMMENTSECRET")
    info.add_itxt("XML:com.adobe.xmp", "<x:xmpmeta>XMPPNGSECRET</x:xmpmeta>")
    exif = Image.Exif()
    exif[0x013B] = "PNGEXIFSECRET"
    im.save(path, "PNG", pnginfo=info, exif=exif.tobytes())
    return path


def make_mp4(path: Path, seconds=2, rotate=None, audio=True):
    args = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=96x64:rate=15:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "15"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "64k"]
    args += ["-metadata", "title=VIDSECRETTITLE", "-metadata", "comment=VIDSECRETCOMMENT",
             "-metadata", "artist=VIDSECRETARTIST", "-metadata", "location=+12.3456-065.4321/",
             "-metadata", "creation_time=2020-01-02T03:04:05Z", "-shortest"]
    tmp = path.with_suffix(".tmp.mp4")
    subprocess.run(args + [str(tmp)], check=True)
    if rotate is not None:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-display_rotation", str(rotate), "-i", str(tmp),
                        "-c", "copy", str(path)], check=True)
        tmp.unlink()
    else:
        tmp.rename(path)
    return path


def ffprobe(path: Path) -> dict:
    r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                        str(path)], capture_output=True, check=True)
    return json.loads(r.stdout)


def video_frames(path: Path, n=5, size=None) -> np.ndarray:
    """Decode first n frames to RGB raw (autorotate applied by ffmpeg)."""
    info = ffprobe(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    args = [FFMPEG, "-v", "error", "-i", str(path), "-frames:v", str(n)]
    if size:
        args += ["-vf", f"scale={size[0]}:{size[1]}"]
    args += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(args, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8), v


@pytest.fixture
def media_dir(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    return d
