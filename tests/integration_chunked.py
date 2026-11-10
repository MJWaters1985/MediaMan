"""End-to-end check of resumable chunked encodes, pausing and Dolby Vision, on synthetic media.

Needs the image's ffmpeg, mkvmerge and dovi_tool, so it runs inside the container (about 3 minutes):

    docker run --rm -v "$PWD":/src:ro --entrypoint python mediaman:dev /src/tests/integration_chunked.py

Not collected by pytest (no test_ prefix); everything it writes stays in the container's /tmp."""
import asyncio, json, os, signal, subprocess, sys, time

os.environ["MEDIAMAN_CONFIG"] = "/tmp/mmcfg"
os.makedirs("/tmp/mmcfg", exist_ok=True)
json.dump({"movies_dir": "/tmp/lib", "tv_dir": "/tmp/tv", "downloads_dir": "/tmp/nodl", "watcher_enabled": False,
           "min_savings_pct": 0, "settings_version": 2, "vmaf_target": 0, "av1_preset": 10, "x265_preset": "ultrafast"},
          open("/tmp/mmcfg/settings.json", "w"))
sys.path.insert(0, "/src")
from app import db, probe, transcode  # noqa: E402

transcode.CHUNK_SECONDS = 3
FF = probe.FFMPEG
os.makedirs("/tmp/lib", exist_ok=True)
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)
    if not cond:
        fails.append(name)


def make_source(path, secs=10):
    # moving test pattern (any frame shift at a chunk boundary shows up as a PSNR drop), HDR10 signalling,
    # 5.1 + stereo audio, a subtitle track and chapters
    srt = "/tmp/s.srt"
    open(srt, "w").write("1\n00:00:01,000 --> 00:00:03,000\nhello\n\n2\n00:00:05,000 --> 00:00:07,000\nworld\n")
    meta = "/tmp/ch.txt"
    open(meta, "w").write(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=5000\ntitle=One\n"
                          "[CHAPTER]\nTIMEBASE=1/1000\nSTART=5000\nEND=10000\ntitle=Two\n")
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=24000/1001:d={secs}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={secs}", "-i", srt, "-i", meta,
                    "-map", "0:v", "-map", "1:a", "-map", "1:a", "-map", "2:s", "-map_chapters", "3",
                    "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
                    "-x265-params", "log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:"
                    "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400:"
                    "keyint=48",
                    "-c:a:0", "ac3", "-ac:a:0", "6", "-c:a:1", "aac", "-ac:a:1", "2", "-c:s", "srt",
                    "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fra", path], check=True)


def frames(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                        "stream=nb_read_frames", "-of", "csv=p=0", path], capture_output=True, text=True)
    return int(r.stdout.strip().strip(",") or 0)


def min_psnr(a, b):
    r = subprocess.run([FF, "-v", "error", "-i", a, "-i", b, "-lavfi",
                        "[0:v]format=yuv420p10le[x];[1:v]format=yuv420p10le[y];[x][y]psnr=stats_file=/tmp/psnr.txt",
                        "-f", "null", "-"], capture_output=True, text=True)
    vals = [float(l.split("psnr_avg:")[1].split()[0]) for l in open("/tmp/psnr.txt") if "psnr_avg:" in l]
    return (min(vals), len(vals)) if vals else (0, 0)


async def run_job(path, cancel_after_chunks=None):
    jid = db.x("INSERT INTO jobs(path,status,created) VALUES(?,?,?)", (path, "running", time.time()))
    task = asyncio.create_task(transcode.worker.run({"id": jid, "path": path, "plan": db.jdump({"force": True})}))
    if cancel_after_chunks:
        wd = None
        while not task.done():
            await asyncio.sleep(0.2)
            plan = db.jload(db.one("SELECT plan FROM jobs WHERE id=?", (jid,))["plan"]) or {}
            wd = plan.get("workdir")
            if wd and os.path.exists(os.path.join(wd, "state.json")):
                st = json.load(open(os.path.join(wd, "state.json")))
                if len(st.get("chunks", [])) >= cancel_after_chunks:
                    task.cancel()      # what a server shutdown does
                    break
        try:
            await task
        except asyncio.CancelledError:
            pass
        return jid, wd
    await task
    return jid, None


async def main():
    transcode.detect_hardware()
    for codec in ("av1", "hevc"):
        from app import config
        config.update({"target_codec": codec})
        src = f"/tmp/lib/src-{codec}.mkv"
        # like a real excerpt: an mkvmerge file (statistics tags) cut with ffmpeg stream copy → stale tags
        make_source("/tmp/long.mkv", 30)
        subprocess.run(["mkvmerge", "-q", "-o", "/tmp/long-mkvmerge.mkv", "/tmp/long.mkv"])
        subprocess.run([FF, "-v", "error", "-y", "-ss", "5", "-i", "/tmp/long-mkvmerge.mkv", "-t", "10", "-map", "0",
                        "-c", "copy", src], check=True)
        ref = f"/tmp/ref-{codec}.mkv"
        subprocess.run(["cp", src, ref], check=True)
        s = probe.summarize(probe.ffprobe(src, False))
        check(f"{codec}: source is cfr", s["cfr"], f"frames tag={s['frames']} (stale) vduration={s['vduration']:.2f}")
        plan = transcode.make_plan(src, s, force=True)
        check(f"{codec}: plan is chunked", plan.get("chunked"), plan.get("note", ""))

        # 1) interrupted after 2 chunks, like a restart
        jid, wd = await run_job(src, cancel_after_chunks=2)
        if not wd:
            print(db.one("SELECT status, message FROM jobs WHERE id=?", (jid,))); fails.append("job"); continue
        st = json.load(open(os.path.join(wd, "state.json")))
        check(f"{codec}: work kept after shutdown", len(st["chunks"]) >= 2 and os.path.exists(src), f"chunks={st['chunks']}")
        db.x("UPDATE jobs SET status='queued' WHERE id=?", (jid,))
        # 2) resumed
        jid2 = db.x("INSERT INTO jobs(path,status,created) VALUES(?,?,?)", (src, "running", time.time()))
        t = time.time()
        await transcode.worker.run({"id": jid2, "path": src, "plan": db.jdump({"force": True})})
        job = db.one("SELECT * FROM jobs WHERE id=?", (jid2,))
        check(f"{codec}: resumed job done", job["status"] == "done", job["message"])
        resumed = any("resuming at chunk" in r["msg"] for r in db.q("SELECT msg FROM log WHERE ts>=?", (t - 1,)))
        check(f"{codec}: log says it resumed", resumed)
        out = job["path"]
        if job["status"] != "done":
            continue
        n_src, n_out = frames(ref), frames(out)
        check(f"{codec}: frame count identical", n_src == n_out, f"{n_src} vs {n_out}")
        mp, n = min_psnr(ref, out)
        check(f"{codec}: no frame shift at chunk joins (min PSNR {mp:.1f} dB over {n} frames)", mp > 30)
        o = probe.summarize(probe.ffprobe(out, False))
        check(f"{codec}: HDR10 kept", o["color_transfer"] == "smpte2084" and o["mastering"], f"cll={o['cll']}")
        check(f"{codec}: audio kept", [a["channels"] for a in o["audio"]] == [6, 2] and
              [{"fre": "fra"}.get(a.get("lang"), a.get("lang")) for a in o["audio"]] == ["eng", "fra"], json.dumps(o["audio"]))
        check(f"{codec}: subtitles kept", len(o["subs"]) == len(s["subs"]))
        chap = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", out],
                                         capture_output=True, text=True).stdout)["chapters"]
        check(f"{codec}: chapters kept", len(chap) >= 1)
        check(f"{codec}: tagged as transcoded", o.get("mediaman"))
        check(f"{codec}: work folder removed", not os.path.exists(wd))

    # 2b) Dolby Vision (synthetic profile 8.1 RPUs made with dovi_tool) through the chunked HEVC path
    from app import config
    config.update({"target_codec": "auto"})
    make_source("/tmp/dvbase.mkv", 10)
    sh = lambda c: subprocess.run(["sh", "-c", c], check=True)
    sh(f"{FF} -v error -y -i /tmp/dvbase.mkv -map 0:v -c copy -bsf:v hevc_mp4toannexb -f hevc /tmp/dv.hevc")
    n = frames("/tmp/dvbase.mkv")
    json.dump({"cm_version": "V40", "length": n, "level6": {"max_display_mastering_luminance": 1000,
               "min_display_mastering_luminance": 1, "max_content_light_level": 1000,
               "max_frame_average_light_level": 400}}, open("/tmp/gen.json", "w"))
    sh("dovi_tool generate -j /tmp/gen.json -o /tmp/rpu.bin && "
       "dovi_tool inject-rpu -i /tmp/dv.hevc --rpu-in /tmp/rpu.bin -o /tmp/dv-inj.hevc")
    src = "/tmp/lib/src-dv.mkv"
    sh(f"mkvmerge -q -o {src} --default-duration 0:24000/1001fps /tmp/dv-inj.hevc -D /tmp/dvbase.mkv; [ $? -le 1 ]")
    sd = probe.summarize(probe.ffprobe(src, False))
    check("dv: synthetic source is Dolby Vision 8", sd["dovi"] and sd["dv_profile"] == 8, f"profile={sd['dv_profile']}")
    jid, wd = await run_job(src, cancel_after_chunks=1)
    jid2 = db.x("INSERT INTO jobs(path,status,created) VALUES(?,?,?)", (src, "running", time.time()))
    await transcode.worker.run({"id": jid2, "path": src, "plan": db.jdump({"force": True})})
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid2,))
    check("dv: chunked + resumed job done", job["status"] == "done", job["message"])
    if job["status"] == "done":
        o = probe.summarize(probe.ffprobe(job["path"], False))
        check("dv: output is HEVC Dolby Vision 8 with HDR10 base", o["vcodec"] == "hevc" and o["dovi"] and
              o["dv_profile"] == 8 and o["mastering"], f"profile={o['dv_profile']} transfer={o['color_transfer']}")
        check("dv: frame count identical", frames(job["path"]) == n, f"{n}")
        check("dv: HDR10 light levels kept", o["cll"] == sd["cll"] and sd["cll"] != [0, 0],
              f"source cll={sd['cll']} output cll={o['cll']}")

    # 2c) light levels only in the Matroska container (zeros in the stream), as on many disc remuxes
    config.update({"target_codec": "hevc"})
    sh(f"{FF} -v error -y -f lavfi -i testsrc2=s=640x360:r=24000/1001:d=6 -c:v libx265 -preset ultrafast "
       "-pix_fmt yuv420p10le -x265-params log-level=error:hdr10=1:colorprim=bt2020:transfer=smpte2084:"
       "colormatrix=bt2020nc:master-display='G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)' "
       "/tmp/nocll.mkv")
    src = "/tmp/lib/src-container-cll.mkv"
    sh(f"mkvmerge -q -o {src} --max-content-light 0:1000 --max-frame-light 0:400 /tmp/nocll.mkv; [ $? -le 1 ]")
    sc = probe.summarize(probe.ffprobe(src, False))
    jid3, _ = await run_job(src)
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid3,))
    oc = probe.summarize(probe.ffprobe(job["path"], False)) if job["status"] == "done" else {}
    check("container-only light levels kept (HEVC)", job["status"] == "done" and oc.get("cll") == sc["cll"] == [1000, 400],
          f"source cll={sc['cll']} output cll={oc.get('cll')} — {job['message']}")
    config.update({"target_codec": "auto"})

    # 3) Pause queue freezes the running job; resume continues it
    from app import config
    config.update({"target_codec": "hevc"})
    src = "/tmp/lib/src-freeze.mkv"
    make_source(src, 20)
    w = transcode.worker
    db.x("DELETE FROM jobs")
    jid = db.x("INSERT INTO jobs(path,status,created,plan) VALUES(?,?,?,?)", (src, "queued", time.time(), db.jdump({"force": True})))
    await w.tick()
    for _ in range(100):
        await asyncio.sleep(0.2)
        p = w.procs.get(jid)
        if p and p.returncode is None:
            break
    w.paused = True
    await w.tick()
    st = db.one("SELECT status FROM jobs WHERE id=?", (jid,))["status"]
    p = w.procs.get(jid)
    state = open(f"/proc/{p.pid}/stat").read().split()[2] if p and p.returncode is None else "?"
    check("pause queue freezes running job", st == "paused" and state == "T", f"db={st} proc={state}")
    prog = db.one("SELECT progress FROM jobs WHERE id=?", (jid,))["progress"]
    await asyncio.sleep(3)
    check("frozen job makes no progress", db.one("SELECT progress FROM jobs WHERE id=?", (jid,))["progress"] == prog)
    check("pause survives restart (saved setting)", json.load(open("/tmp/mmcfg/settings.json")).get("queue_paused") is True)
    w.paused = False
    await w.tick()
    check("resume continues it", db.one("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "running")
    for _ in range(600):
        await w.tick()
        if jid not in w.tasks or w.tasks[jid].done():
            break
        await asyncio.sleep(0.5)
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    check("frozen-then-resumed job finishes", job["status"] == "done", job["message"])
    print("\n== RESULT:", "ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
    sys.exit(1 if fails else 0)


asyncio.run(main())
