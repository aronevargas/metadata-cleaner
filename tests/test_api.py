"""Integration tests against the real local server (subprocess)."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image, ImageOps

from conftest import (FFMPEG, FFPROBE, Server, ffprobe, gradient, make_jpeg, make_mp4, make_png, sha256,
                      tree_hashes, video_frames)

SECRETS = [b"SecretCam", b"SecretModel", b"OrigSoftware", b"Original Artist", b"Orig Copyright",
           b"XMPSECRET", b"JPEGCOMSECRET", b"PNGSECRET", b"PNGCOMMENTSECRET", b"XMPPNGSECRET",
           b"PNGEXIFSECRET", b"VIDSECRET", b"+12.3456", b"2020:01:02", b"2020-01-02"]


def job(input_dir, output_dir, **kw):
    return {"input_dir": str(input_dir), "output_dir": str(output_dir), **kw}


def assert_no_secrets(p: Path):
    data = p.read_bytes()
    found = [s for s in SECRETS if s in data]
    assert not found, f"{p.name} still contains {found}"


def decoded(p):
    with Image.open(p) as im:
        im = ImageOps.exif_transpose(im)
        return np.asarray(im.convert("RGBA")).astype(int)


# ---------------------------------------------------------------- health / security
def test_health(server):
    j = server.client.get("/api/health").json()
    assert j["ok"] is True
    assert j["ffmpeg"]["available"] is True


def test_listens_only_on_loopback(server):
    out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
    lines = [l for l in out.splitlines() if f":{server.port} " in l]
    assert lines, out
    for l in lines:
        local = l.split()[3]
        assert local.startswith("127.0.0.1:") or local.startswith("[::1]:"), l


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example.com"},
    {"Host": "attacker.test:1234"},
])
def test_rejects_foreign_host(server, headers):
    r = server.client.get("/api/health", headers=headers)
    assert r.status_code == 403


def test_rejects_foreign_origin_on_mutation(server, media_dir, tmp_path):
    make_jpeg(media_dir / "a.jpg")
    r = server.client.post("/api/jobs", json=job(media_dir, tmp_path / "o"),
                           headers={"Origin": "http://evil.example.com"})
    assert r.status_code == 403
    assert not (tmp_path / "o").exists()


def test_rejects_non_json_post(server, media_dir, tmp_path):
    make_jpeg(media_dir / "a.jpg")
    body = json.dumps(job(media_dir, tmp_path / "o"))
    for ct in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
        r = server.client.post("/api/jobs", content=body, headers={"Content-Type": ct})
        assert r.status_code == 415, (ct, r.status_code)
    assert not (tmp_path / "o").exists()


def test_same_origin_allowed(server, media_dir, tmp_path):
    make_jpeg(media_dir / "a.jpg")
    r = server.client.post("/api/scan", json={"input_dir": str(media_dir)},
                           headers={"Origin": server.base})
    assert r.status_code == 200


def test_static_ui_served(server):
    r = server.client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()
    for id_ in ("input-dir", "output-dir", "scan-btn", "start-btn", "progress", "results"):
        assert f'id="{id_}"' in r.text, id_


def test_dialog_without_picker(server):
    r = server.post("/api/dialog/folder", {"title": "x"})
    assert r.status_code == 200
    assert r.json()["supported"] is False


# ---------------------------------------------------------------- scan / validation
def test_scan_lists_and_orders(server, media_dir, tmp_path):
    make_jpeg(media_dir / "img10.jpg")
    make_jpeg(media_dir / "img2.JPG")
    make_png(media_dir / "img1.PNG")
    (media_dir / "notes.txt").write_text("x")
    (media_dir / ".hidden.jpg").write_bytes(b"x")
    r = server.post("/api/scan", {"input_dir": str(media_dir), "output_dir": str(tmp_path / "o")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert [f["name"] for f in j["files"]] == ["img1.PNG", "img2.JPG", "img10.jpg"]
    assert j["counts"]["image"] == 3
    assert {s["name"] for s in j["skipped"]} >= {"notes.txt"}
    assert j["next_index"] == 1


@pytest.mark.parametrize("case", ["missing", "same", "input_inside_output", "file"])
def test_invalid_dirs(server, media_dir, tmp_path, case):
    make_jpeg(media_dir / "a.jpg")
    if case == "missing":
        b = job(tmp_path / "nope", tmp_path / "o")
    elif case == "same":
        b = job(media_dir, media_dir)
    elif case == "input_inside_output":
        b = job(media_dir, tmp_path)
    else:
        b = job(media_dir / "a.jpg", tmp_path / "o")
    before = tree_hashes(tmp_path)
    r = server.post("/api/jobs", b)
    assert r.status_code == 400, r.text
    assert isinstance(r.json()["detail"], str) and r.json()["detail"]
    assert tree_hashes(tmp_path) == before


def test_empty_input_dir(server, media_dir, tmp_path):
    (media_dir / "x.txt").write_text("x")
    r = server.post("/api/jobs", job(media_dir, tmp_path / "o"))
    assert r.status_code == 400


def test_output_nested_inside_input(server, media_dir):
    make_jpeg(media_dir / "a.jpg")
    make_png(media_dir / "b.png")
    out = media_dir / "out"
    before = tree_hashes(media_dir)
    j, _ = server.run_job(job(media_dir, out, recursive=True))
    assert j["status"] == "done" and j["succeeded"] == 2, j
    assert sorted(p.name for p in out.iterdir()) == ["1.jpg", "2.png"]
    assert {k: v for k, v in tree_hashes(media_dir).items() if not k.startswith("out/")} == before
    # second run must not pick up its own outputs
    j2, _ = server.run_job(job(media_dir, out, recursive=True))
    assert j2["total"] == 2, j2
    assert sorted(p.name for p in out.iterdir()) == ["1.jpg", "2.png", "3.jpg", "4.png"]


# ---------------------------------------------------------------- core images
def test_images_strip_full(server, media_dir, tmp_path):
    make_jpeg(media_dir / "a.jpg", orientation=1)
    make_jpeg(media_dir / "b.JPEG")
    make_png(media_dir / "c.png")
    make_png(media_dir / "d.PNG", alpha=False)
    originals = tree_hashes(media_dir)
    out = tmp_path / "out"
    j, snaps = server.run_job(job(media_dir, out))
    assert j["status"] == "done", j
    assert (j["total"], j["processed"], j["succeeded"], j["failed"]) == (4, 4, 4, 0)
    names = {r["source"]: r["output"] for r in j["results"]}
    assert names == {"a.jpg": "1.jpg", "b.JPEG": "2.jpeg", "c.png": "3.png", "d.PNG": "4.png"}
    assert sorted(p.name for p in out.iterdir()) == ["1.jpg", "2.jpeg", "3.png", "4.png"]
    assert tree_hashes(media_dir) == originals, "originals modified"
    for src, dst in names.items():
        s, d = media_dir / src, out / dst
        assert sha256(s) != sha256(d)
        assert_no_secrets(d)
        a, b = decoded(s), decoded(d)
        assert a.shape == b.shape
        diff = np.abs(a - b)
        assert (diff > 0).any(), f"{dst}: decoded pixels identical"
        assert diff.mean() < 8, f"{dst}: change too visible ({diff.mean():.2f})"
        with Image.open(d) as im:
            assert not im.getexif(), f"{dst} exif {dict(im.getexif())}"
            assert "xmp" not in im.info and "XML:com.adobe.xmp" not in im.info
            assert "comment" not in im.info
            assert im.format == ("JPEG" if dst.endswith(("jpg", "jpeg")) else "PNG")
    # progress observed sane
    for s in snaps:
        assert 0 <= s["processed"] <= s["total"]


def test_jpeg_pixels_differ_after_recompression(server, media_dir, tmp_path):
    """Noise must survive JPEG compression: compare decoded output vs decoded original."""
    for i in range(3):
        make_jpeg(media_dir / f"q{i}.jpg", w=256, h=256, quality=[60, 85, 100][i])
    j, _ = server.run_job(job(media_dir, tmp_path / "o", noise_strength=1))
    assert j["succeeded"] == 3, j
    for r in j["results"]:
        a, b = decoded(media_dir / r["source"]), decoded(tmp_path / "o" / r["output"])
        frac = (a != b).any(axis=2).mean()
        assert frac > 0.05, f"{r['source']}: only {frac:.3%} pixels changed"
        psnr = 10 * np.log10(255 ** 2 / max(((a - b) ** 2).mean(), 1e-9))
        assert psnr > 30, f"{r['source']}: PSNR {psnr:.1f} dB (too visible)"


def test_pixel_noise_off_still_new_hash(server, media_dir, tmp_path):
    make_png(media_dir / "a.png")
    j, _ = server.run_job(job(media_dir, tmp_path / "o", pixel_noise=False))
    assert j["succeeded"] == 1
    d = tmp_path / "o" / "1.png"
    assert_no_secrets(d)
    assert sha256(d) != sha256(media_dir / "a.png")


def test_runs_produce_distinct_outputs(server, media_dir, tmp_path):
    make_png(media_dir / "a.png")
    server.run_job(job(media_dir, tmp_path / "o1"))
    server.run_job(job(media_dir, tmp_path / "o2"))
    assert sha256(tmp_path / "o1" / "1.png") != sha256(tmp_path / "o2" / "1.png")


def test_replace_metadata(server, media_dir, tmp_path):
    make_jpeg(media_dir / "a.jpg")
    make_png(media_dir / "b.png")
    md = {"title": "NovoTitulo", "artist": "NovoAutor", "comment": "NovoComentario",
          "copyright": "NovoCopy", "software": ""}
    j, _ = server.run_job(job(media_dir, tmp_path / "o", metadata_mode="replace", metadata=md))
    assert j["succeeded"] == 2, j
    for name in ("1.jpg", "2.png"):
        p = tmp_path / "o" / name
        assert_no_secrets(p)
        data = p.read_bytes()
        for v in ("NovoTitulo", "NovoAutor", "NovoCopy"):
            assert v.encode() in data, f"{name} missing {v}"
    with Image.open(tmp_path / "o" / "1.jpg") as im:
        ex = im.getexif()
        assert ex.get(0x013B) == "NovoAutor"
        assert ex.get(0x8298) == "NovoCopy"
        assert 0x8825 not in ex or not ex.get_ifd(0x8825), "GPS survived"
        assert 0x010F not in ex and 0x0110 not in ex


def test_orientation_applied(server, media_dir, tmp_path):
    make_jpeg(media_dir / "rot.jpg", w=64, h=32, orientation=6)  # rotate 90 CW -> displayed 32x64
    j, _ = server.run_job(job(media_dir, tmp_path / "o"))
    assert j["succeeded"] == 1, j
    with Image.open(tmp_path / "o" / "1.jpg") as im:
        assert im.getexif().get(0x0112, 1) == 1
        assert im.size == (32, 64), im.size
    a = decoded(media_dir / "rot.jpg")
    b = decoded(tmp_path / "o" / "1.jpg")
    assert np.abs(a - b).mean() < 8, "orientation result looks different from displayed original"


def test_transparency_preserved(server, media_dir, tmp_path):
    make_png(media_dir / "t.png", alpha=True)
    j, _ = server.run_job(job(media_dir, tmp_path / "o"))
    assert j["succeeded"] == 1
    with Image.open(tmp_path / "o" / "1.png") as im:
        assert im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info, im.mode
        out_a = np.asarray(im.convert("RGBA"))[..., 3]
    with Image.open(media_dir / "t.png") as im:
        in_a = np.asarray(im.convert("RGBA"))[..., 3]
    assert np.array_equal(in_a, out_a), "alpha channel altered"


def test_corrupted_files_reported(server, media_dir, tmp_path):
    make_jpeg(media_dir / "a_good.jpg")
    (media_dir / "b_bad.jpg").write_bytes(b"\xff\xd8\xff\xe0garbage" * 10)
    (media_dir / "c_bad.png").write_bytes(b"not a png at all")
    (media_dir / "d_bad.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)
    make_png(media_dir / "e_good.png")
    j, _ = server.run_job(job(media_dir, tmp_path / "o"))
    assert j["status"] == "done", j
    assert (j["succeeded"], j["failed"]) == (2, 3), j
    res = {r["source"]: r for r in j["results"]}
    for bad in ("b_bad.jpg", "c_bad.png", "d_bad.mp4"):
        assert res[bad]["ok"] is False and res[bad]["status"] == "error"
        assert res[bad]["error"] and len(res[bad]["error"]) > 5
        assert res[bad]["output"] is None
    # numbers consumed only by successes, no leftover temp files
    assert sorted(p.name for p in (tmp_path / "o").iterdir()) == ["1.jpg", "2.png"]


def test_collisions_never_overwrite(server, media_dir, tmp_path):
    make_jpeg(media_dir / "a.jpg")
    make_png(media_dir / "b.png")
    out = tmp_path / "o"
    out.mkdir()
    (out / "1.jpg").write_bytes(b"EXISTING1")
    (out / "3.png").write_bytes(b"EXISTING3")
    (out / "7.txt").write_bytes(b"EXISTING7")
    (out / "notnumeric.jpg").write_bytes(b"X")
    pre = tree_hashes(out)
    r = server.post("/api/scan", {"input_dir": str(media_dir), "output_dir": str(out)})
    nxt = r.json()["next_index"]
    assert nxt == 8, nxt
    j, _ = server.run_job(job(media_dir, out))
    assert j["succeeded"] == 2, j
    for k, v in pre.items():
        assert sha256(out / k) == v, f"{k} overwritten"
    outs = [r["output"] for r in j["results"]]
    assert outs == ["8.jpg", "9.png"], outs
    # consecutive run continues numbering
    j2, _ = server.run_job(job(media_dir, out))
    assert [r["output"] for r in j2["results"]] == ["10.jpg", "11.png"]


def test_concurrent_jobs_same_output(server, media_dir, tmp_path):
    for i in range(6):
        make_jpeg(media_dir / f"{i}.jpg", w=800, h=800)
    out = tmp_path / "o"
    r1 = server.post("/api/jobs", job(media_dir, out))
    r2 = server.post("/api/jobs", job(media_dir, out))
    assert r1.status_code == 202
    assert r2.status_code in (409, 202)
    # wait for completion
    for r in (r1, r2):
        if r.status_code == 202:
            jid = r.json()["job_id"]
            for _ in range(600):
                if server.client.get(f"/api/jobs/{jid}").json()["status"] in ("done", "error", "cancelled"):
                    break
                time.sleep(0.1)
    names = sorted(p.name for p in out.iterdir())
    assert len(names) == len(set(names))
    assert all(n.split(".")[0].isdigit() for n in names), names


def test_cancel(server, media_dir, tmp_path):
    for i in range(15):
        make_jpeg(media_dir / f"{i:02d}.jpg", w=1600, h=1600)
    r = server.post("/api/jobs", job(media_dir, tmp_path / "o"))
    jid = r.json()["job_id"]
    time.sleep(0.2)
    c = server.post(f"/api/jobs/{jid}/cancel", {})
    assert c.status_code == 200
    for _ in range(300):
        j = server.client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.1)
    assert j["status"] in ("cancelled", "done"), j
    if j["status"] == "cancelled":
        assert any(r["status"] == "skipped" for r in j["results"]), j
    files = sorted((tmp_path / "o").iterdir())
    assert len(files) == j["succeeded"]
    for f in files:
        Image.open(f).verify()  # no partial output


def test_job_404(server):
    assert server.client.get("/api/jobs/doesnotexist").status_code == 404


def test_unreadable_source(server, media_dir, tmp_path):
    make_jpeg(media_dir / "a.jpg")
    p = make_jpeg(media_dir / "b.jpg")
    p.chmod(0)
    try:
        j, _ = server.run_job(job(media_dir, tmp_path / "o"))
    finally:
        p.chmod(0o644)
    res = {r["source"]: r for r in j["results"]}
    assert res["a.jpg"]["ok"] is True
    assert res["b.jpg"]["ok"] is False and res["b.jpg"]["error"]


# ---------------------------------------------------------------- video
needs_ff = pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg ausente")


@needs_ff
def test_video_full(server, media_dir, tmp_path):
    make_mp4(media_dir / "v.mp4", seconds=2)
    make_mp4(media_dir / "W.MP4", seconds=1, audio=False)
    originals = tree_hashes(media_dir)
    j, _ = server.run_job(job(media_dir, tmp_path / "o"), timeout=300)
    assert j["succeeded"] == 2, j
    outs = {r["source"]: r["output"] for r in j["results"]}
    assert outs == {"v.mp4": "1.mp4", "W.MP4": "2.mp4"}, outs
    assert tree_hashes(media_dir) == originals
    for src, dst in outs.items():
        s, d = media_dir / src, tmp_path / "o" / dst
        assert sha256(s) != sha256(d)
        assert_no_secrets(d)
        pi, po = ffprobe(s), ffprobe(d)
        tags = po["format"].get("tags", {})
        for k, v in tags.items():
            assert "VIDSECRET" not in v and "12.3456" not in v, (k, v)
        for st in po["streams"]:
            for k, v in st.get("tags", {}).items():
                assert "VIDSECRET" not in v, (k, v)
        assert abs(float(pi["format"]["duration"]) - float(po["format"]["duration"])) < 0.15
        ai = [x for x in pi["streams"] if x["codec_type"] == "audio"]
        ao = [x for x in po["streams"] if x["codec_type"] == "audio"]
        assert len(ai) == len(ao)
        if ai:
            assert ao[0]["codec_name"] == ai[0]["codec_name"]
            assert abs(float(ao[0]["duration"]) - float(ai[0]["duration"])) < 0.15
        fa, va = video_frames(s)
        fb, vb = video_frames(d)
        assert (va["width"], va["height"]) == (vb["width"], vb["height"])
        assert fa.shape == fb.shape
        diff = np.abs(fa.astype(int) - fb.astype(int))
        assert (diff > 0).mean() > 0.05, "decoded video frames nearly identical"
        assert diff.mean() < 10, f"video change too visible: {diff.mean():.2f}"


@needs_ff
def test_video_rotation(server, media_dir, tmp_path):
    make_mp4(media_dir / "r.mp4", seconds=1, rotate=90)
    j, _ = server.run_job(job(media_dir, tmp_path / "o"), timeout=300)
    assert j["succeeded"] == 1, j
    d = tmp_path / "o" / "1.mp4"
    fa, _ = video_frames(media_dir / "r.mp4", n=1)
    fb, _ = video_frames(d, n=1)
    assert fa.shape == fb.shape
    # displayed orientation equal (ffmpeg autorotates on decode)
    assert np.abs(fa.astype(int) - fb.astype(int)).mean() < 12


@needs_ff
def test_video_replace_metadata(server, media_dir, tmp_path):
    make_mp4(media_dir / "v.mp4", seconds=1)
    j, _ = server.run_job(job(media_dir, tmp_path / "o", metadata_mode="replace",
                              metadata={"title": "VTitulo", "artist": "VAutor"}), timeout=300)
    assert j["succeeded"] == 1, j
    tags = ffprobe(tmp_path / "o" / "1.mp4")["format"].get("tags", {})
    assert tags.get("title") == "VTitulo", tags
    assert tags.get("artist") == "VAutor", tags
    assert_no_secrets(tmp_path / "o" / "1.mp4")


def test_ffmpeg_missing(media_dir, tmp_path):
    s = Server({"METAEDIT_FFMPEG": "/nao/existe", "METAEDIT_FFPROBE": "/nao/existe"})
    try:
        h = s.client.get("/api/health").json()
        assert h["ffmpeg"]["available"] is False and h["ffmpeg"]["error"]
        make_jpeg(media_dir / "a.jpg")
        (media_dir / "b.mp4").write_bytes(b"x" * 100)
        sc = s.post("/api/scan", {"input_dir": str(media_dir)}).json()
        assert sc["warnings"]
        j, _ = s.run_job(job(media_dir, tmp_path / "o"))
        res = {r["source"]: r for r in j["results"]}
        assert res["a.jpg"]["ok"] is True
        assert res["b.mp4"]["ok"] is False and "ffmpeg" in res["b.mp4"]["error"].lower()
    finally:
        s.stop()


# ---------------------------------------------------------------- fixture sanity (tests are not vacuous)
def test_fixtures_contain_metadata(tmp_path):
    j = make_jpeg(tmp_path / "a.jpg", orientation=6)
    p = make_png(tmp_path / "b.png")
    for f, needles in ((j, [b"SecretCam", b"XMPSECRET", b"JPEGCOMSECRET", b"2020:01:02"]),
                       (p, [b"PNGSECRET", b"XMPPNGSECRET", b"PNGEXIFSECRET"])):
        data = f.read_bytes()
        for n in needles:
            assert n in data, (f.name, n)
    with Image.open(j) as im:
        assert im.getexif()[0x0112] == 6 and im.getexif().get_ifd(0x8825)
    if FFMPEG:
        v = make_mp4(tmp_path / "v.mp4", seconds=1, rotate=90)
        assert b"VIDSECRET" in v.read_bytes()
        st = [s for s in ffprobe(v)["streams"] if s["codec_type"] == "video"][0]
        assert any("rotation" in sd for sd in st.get("side_data_list", [])), st
        assert any(s["codec_type"] == "audio" for s in ffprobe(v)["streams"])


# ---------------------------------------------------------------- edge cases
@pytest.mark.parametrize("color", [(255, 255, 255), (0, 0, 0), (128, 128, 128)])
@pytest.mark.parametrize("fmt", ["png", "jpg"])
def test_flat_images_still_change(server, media_dir, tmp_path, color, fmt):
    """Clipping at 0/255 must not cancel the noise; JPEG noise must survive compression.
    Noise is random, so repeat 5 runs: every run must produce different decoded pixels."""
    Image.new("RGB", (128, 128), color).save(media_dir / f"flat.{fmt}", quality=90)
    for k in range(5):
        out = tmp_path / f"o{k}"
        j, _ = server.run_job(job(media_dir, out, noise_strength=1))
        assert j["succeeded"] == 1, j
        a, b = decoded(media_dir / f"flat.{fmt}"), decoded(out / f"1.{fmt}")
        assert (a != b).any(), f"run {k}: flat {color} {fmt} strength 1: decoded pixels identical"


@pytest.mark.parametrize("mode", ["L", "P", "LA", "I;16", "CMYK"])
def test_other_image_modes(server, media_dir, tmp_path, mode):
    ext = "jpg" if mode == "CMYK" else "png"
    im = Image.fromarray(gradient(64, 64)).convert("RGB")
    im = im.convert(mode) if mode != "I;16" else im.convert("L").convert("I;16")
    im.save(media_dir / f"m.{ext}")
    j, _ = server.run_job(job(media_dir, tmp_path / "o"))
    r = j["results"][0]
    assert r["ok"] is True, r
    out = tmp_path / "o" / r["output"]
    assert sha256(out) != sha256(media_dir / f"m.{ext}")
    with Image.open(out) as o:
        o.load()


def test_symlink_and_recursive(server, media_dir, tmp_path):
    (media_dir / "sub" / "deep").mkdir(parents=True)
    make_jpeg(media_dir / "z.jpg")
    make_png(media_dir / "sub" / "a.png")
    make_jpeg(media_dir / "sub" / "deep" / "b.JPG")
    outside = make_png(tmp_path / "outside.png")
    (media_dir / "link.png").symlink_to(outside)
    sc = server.post("/api/scan", {"input_dir": str(media_dir), "recursive": True}).json()
    names = [f["rel_path"] for f in sc["files"]]
    assert "link.png" not in names
    assert len(names) == 3, names
    j, _ = server.run_job(job(media_dir, tmp_path / "o", recursive=True))
    assert j["succeeded"] == 3
    assert sorted(p.name for p in (tmp_path / "o").iterdir()) == ["1.png", "2.jpg", "3.jpg"] or \
        len(list((tmp_path / "o").iterdir())) == 3
    assert all(p.parent == tmp_path / "o" for p in (tmp_path / "o").rglob("*"))
    j2, _ = server.run_job(job(media_dir, tmp_path / "o2", recursive=False))
    assert j2["total"] == 1


def test_path_with_spaces_and_unicode(server, tmp_path):
    d = tmp_path / "pasta com espaço ção"
    d.mkdir()
    make_jpeg(d / "fóto 1.jpg")
    j, _ = server.run_job(job(d, tmp_path / "saída ü"))
    assert j["succeeded"] == 1, j
    assert (tmp_path / "saída ü" / "1.jpg").exists()


@needs_ff
@pytest.mark.parametrize("color", ["black", "gray"])
def test_flat_video_changes(server, media_dir, tmp_path, color):
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=10:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media_dir / "f.mp4")], check=True)
    j, _ = server.run_job(job(media_dir, tmp_path / "o", noise_strength=1), timeout=120)
    assert j["succeeded"] == 1, j
    fa, _ = video_frames(media_dir / "f.mp4")
    fb, _ = video_frames(tmp_path / "o" / "1.mp4")
    assert fa.shape == fb.shape and (fa != fb).any()
