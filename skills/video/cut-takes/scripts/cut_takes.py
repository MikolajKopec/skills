#!/usr/bin/env python3
"""Cut retakes, false starts and long pauses out of a talking-head recording.

    python cut_takes.py doctor
    python cut_takes.py analyze VIDEO --work DIR [--script SCRIPT.txt] [--language en]
                                     [--model large-v3-turbo] [--device auto|cpu|cuda]
    python cut_takes.py transcribe VIDEO --work DIR --at FROM TO [FROM TO ...]
    python cut_takes.py frames  VIDEO --at 12.3 [--dur 2] --png frames.png
    python cut_takes.py render  DIR/cuts.json [--style natural|tight] [--mp4] [--name NAME]

analyze  transcribes every phrase (speech between pauses) separately, so retakes
         stay visible, flags retakes / false starts / repeated words and writes
         DIR/takes.md (to read), DIR/takes.json and a draft DIR/cuts.json.
render   turns cuts.json into frame-accurate segments with long pauses shortened
         and writes NAME.fcpxml (Final Cut Pro, DaVinci Resolve), NAME.xml
         (Premiere Pro, DaVinci Resolve), NAME.edl (CMX 3600, most editors),
         NAME-capcut/ (a CapCut desktop project folder), NAME-segments.json
         and, with --mp4, NAME.mp4. Then it checks that all exports describe
         the same timeline as the MP4.

cuts.json:
{
  "source": "my-video.mp4",        # relative to cuts.json or absolute
  "style": "natural",              # natural: pauses > 0.5 s shrink to 0.3 s; tight: every pause cut
  "clips": [
    {"from": 3.10, "to": 9.85, "note": "#2 intro"},
    {"from": 14.2, "to": 21.0, "skip": [[16.40, 17.15]], "note": "#5 stumble removed"}
  ]
}
from/to   window in source seconds, set in the silence before / after the take.
skip      ranges cut out of the middle of a clip (a stumble, a repeated word).
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import wave
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

SR = 16000          # analysis audio: 16 kHz mono
HOP = 0.01          # level frames: 10 ms
MERGE_GAP = 0.30    # pauses shorter than this stay inside one phrase
MIN_PHRASE = 0.12   # shorter blips are noise
WINDOW = 120.0      # a retake is looked for up to this many seconds later
STYLES = {          # pause handling and margins around speech, in seconds
    "natural": dict(max_pause=0.5, keep_pause=0.3, lead=0.12, tail=0.25, breaths=False),
    "tight": dict(max_pause=0.12, keep_pause=0.08, lead=0.05, tail=0.08, breaths=True),
}


def die(msg):
    sys.exit("error: " + msg)


def run(cmd):
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout
    except FileNotFoundError:
        die("%s not found. Install FFmpeg and make sure it is on PATH." % cmd[0])
    except subprocess.CalledProcessError as e:
        die("%s failed:\n%s" % (cmd[0], e.stderr[-2000:]))


# ---------------------------------------------------------------- media info
def probe(src):
    if not os.path.exists(src):
        die("file not found: %s" % src)
    info = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", src]))
    vs = [s for s in info["streams"] if s["codec_type"] == "video"
          and s.get("disposition", {}).get("attached_pic", 0) == 0]
    aus = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if not vs or not aus:
        die("%s needs both a video and an audio stream" % src)
    v, a = vs[0], aus[0]
    fps = Fraction(v["r_frame_rate"])
    avg = Fraction(v.get("avg_frame_rate", "0/1") or "0/1")
    tc = None
    for s in [v] + info["streams"] + [info["format"]]:
        tc = tc or s.get("tags", {}).get("timecode")
    dur = float(info["format"]["duration"])
    return dict(path=str(Path(src).resolve()), fps=fps, width=int(v["width"]),
                height=int(v["height"]), duration=dur, frames=int(round(dur * fps)),
                vfr=bool(avg) and abs(float(avg) - float(fps)) / float(fps) > 0.002,
                tc=tc or "00:00:00:00", channels=int(a.get("channels", 2)),
                rate=int(a.get("sample_rate", 48000)))


def load_audio(src, work):
    wav = os.path.join(work, Path(src).stem + ".wav")
    if not os.path.exists(wav) or os.path.getmtime(wav) < os.path.getmtime(src):
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-map", "0:a:0",
             "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", wav])
    with wave.open(wav) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    h = int(SR * HOP)
    n = len(a) // h
    db = 20 * np.log10(np.sqrt((a[:n * h].reshape(n, h) ** 2).mean(1)) + 1e-9)
    return a, db


def levels(db):
    """Thresholds relative to this recording (room noise and voice level differ)."""
    floor = float(max(np.percentile(db, 5), -90))
    speech = float(np.percentile(db, 95))
    span = max(speech - floor, 20)
    return dict(floor=floor, speech=speech, quiet=floor + max(6, 0.3 * span),
                pause=floor + max(8, 0.45 * span), on=floor + max(12, 0.5 * span),
                breath=floor + max(16, 0.65 * span), fade=floor + 3)


def runs(mask):
    """[(start_frame, end_frame)] of consecutive True values."""
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]))


def find_phrases(db, lv):
    rs = []
    for s, e in runs(db > lv["quiet"]):
        if rs and (s - rs[-1][1]) * HOP < MERGE_GAP:
            rs[-1] = (rs[-1][0], e)
        else:
            rs.append((s, e))
    return [(s * HOP, e * HOP) for s, e in rs
            if (e - s) * HOP >= MIN_PHRASE and db[s:e].max() > lv["on"]]


# ---------------------------------------------------------------- whisper
def _nvidia_dll_dirs():
    """Windows: make CUDA libraries from pip packages (nvidia-cublas-cu12,
    nvidia-cudnn-cu12) visible to CTranslate2."""
    if os.name != "nt":
        return
    try:
        import nvidia
    except ImportError:
        return
    for root in nvidia.__path__:
        for b in Path(root).glob("*/bin"):
            os.add_dll_directory(str(b))
            os.environ["PATH"] = str(b) + os.pathsep + os.environ["PATH"]


def load_model(name, device):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        die("faster-whisper is not installed: python -m pip install faster-whisper")
    if device in ("auto", "cuda"):
        _nvidia_dll_dirs()
        try:
            m = WhisperModel(name, device="cuda", compute_type="float16")
            list(m.transcribe(np.zeros(SR, np.float32))[0])   # CUDA libraries load here
            return m, "cuda"
        except Exception as e:
            if device == "cuda":
                die("GPU not usable: %s" % e)
            print("GPU not usable (%s) - using CPU." % str(e).splitlines()[0][:150])
    return WhisperModel(name, device="cpu", compute_type="int8"), "cpu"


def transcribe(model, audio, t0, t1, language):
    pad = np.zeros(int(SR * 0.3), np.float32)
    x = np.concatenate([pad, audio[int(t0 * SR):int(t1 * SR)], pad])
    segs, info = model.transcribe(x, language=language, beam_size=5, temperature=0.0,
                                  condition_on_previous_text=False, word_timestamps=True,
                                  vad_filter=False)
    words, lp = [], []
    for s in segs:
        lp.append(s.avg_logprob)
        for w in s.words or []:
            words.append({"w": w.word.strip(), "s": round(max(w.start - 0.3, 0) + t0, 2),
                          "e": round(w.end - 0.3 + t0, 2), "p": round(float(w.probability), 2)})
    return words, (min(lp) if lp else -9.0)


def unclear(words, logprob, voiced, dur):
    """Whisper invents words for coughs, clicks and breaths ("Thank you.",
    "Thanks for watching!"). Such phrases have almost no voiced audio, are too
    fast to be real speech, or are short and low-confidence."""
    if not toks(words) or voiced < 0.1:
        return True
    if len(" ".join(w["w"] for w in words)) / max(dur, 0.05) > 30:
        return True
    mean_p = sum(w["p"] for w in words) / len(words)
    return len(words) <= 3 and mean_p < 0.75 and logprob < -0.5


def norm(w):
    return re.sub(r"[^\w]", "", w.lower())


def toks(words):
    return [norm(w["w"]) for w in words if norm(w["w"])]


# ---------------------------------------------------------------- flags
def inner_flags(words, script_text):
    """Repeated words / phrases and false starts inside one phrase."""
    ws = [(w["s"], norm(w["w"])) for w in words if norm(w["w"])]
    in_script = script_text and (lambda g: (" " + g + " ") in script_text)
    out, used = [], set()
    for n in (4, 3, 2, 1):
        for i in range(len(ws) - 2 * n + 1):
            g = [x for _, x in ws[i:i + n]]
            if n == 1 and len(g[0]) < 3:
                continue
            for j in range(i + n, min(i + n + 3, len(ws) - n + 1)):
                if [x for _, x in ws[j:j + n]] == g and not set(range(i, j + n)) & used:
                    if in_script and in_script(" ".join(g + g)):
                        continue
                    out.append("repeated \"%s\" at %.2f and %.2f" % (" ".join(g), ws[i][0], ws[j][0]))
                    used |= set(range(i, j + n))
    for i in range(len(ws) - 1):
        a, b = ws[i][1], ws[i + 1][1]
        if i not in used and 1 <= len(a) < len(b) and b.startswith(a):
            out.append("false start \"%s... %s\" at %.2f" % (a, b, ws[i][0]))
    # Whisper often merges "word... word" into one long word: check the window
    inner = [w for w in words if norm(w["w"])][1:-1]
    if len(inner) >= 3:
        rate = float(np.median([(w["e"] - w["s"]) / len(norm(w["w"])) for w in inner]))
        for w in inner:
            d = w["e"] - w["s"]
            if d > max(0.8, 2.5 * rate * len(norm(w["w"]))):
                out.append("long word \"%s\" %.2f-%.2f (%.1f s) - maybe a repeat Whisper merged, "
                           "check with transcribe" % (w["w"], w["s"], w["e"], d))
    return out


def script_cover(words, script_words):
    """Script word positions this phrase says (matched runs of >= 2 words,
    1 word for very short phrases) -> {script_index: phrase_word_index}."""
    t = toks(words)
    if not t or not script_words:
        return {}
    sm = difflib.SequenceMatcher(None, t, script_words, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= (1 if len(t) <= 2 else 2)]
    if not blocks:
        return {}
    big = max(blocks, key=lambda b: b.size)
    cov = {}
    for b in blocks:
        if abs(b.b - big.b) <= 2 * len(t) + 5:
            for k in range(b.size):
                cov[b.b + k] = b.a + k
    return cov


def find_retakes(P, script_words):
    """Mark phrases that are said again later (the later take wins)."""
    for i, p in enumerate(P):
        a = toks(p["words"])
        if p["noise"] or not a:
            continue
        later = [q for q in P[i + 1:] if not q["noise"] and q["start"] - p["end"] < WINDOW]
        for q in later:
            b = toks(q["words"])
            if not b:
                continue
            head = b[:len(a) + 1]
            same = (a == b[:len(a)]) if len(a) <= 2 else \
                difflib.SequenceMatcher(None, a, head, autojunk=False).ratio() >= 0.6
            cut_word = len(a) <= 3 and len(b) >= len(a) and a[:-1] == b[:len(a) - 1] \
                and len(a[-1]) >= 2 and b[len(a) - 1].startswith(a[-1])
            if same or cut_word:
                p["retaken_in"] = q["id"]
                break
            # the ending of p is said again at the start of q: p is kept up to there
            for k in range(1, len(a) - 2):
                tail = a[k:]
                sm = difflib.SequenceMatcher(None, tail, b[:len(tail)], autojunk=False)
                if sm.ratio() >= 0.75:
                    k += next(m.a for m in sm.get_matching_blocks() if m.size)  # first repeated word
                    w = [w for w in p["words"] if norm(w["w"])][k]
                    p["ending_retaken"] = {"in": q["id"], "from_word": w["w"], "at": w["s"]}
                    break
            if "ending_retaken" in p:
                break
        if script_words and "retaken_in" not in p and p["cover"]:
            mine = set(p["cover"])
            again = set()
            for q in later:
                again |= set(q["cover"])
            if len(mine & again) >= 0.8 * len(mine):
                p["retaken_in"] = next(q["id"] for q in later if set(q["cover"]) & mine)


# ---------------------------------------------------------------- analyze
def cmd_doctor(a):
    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        print("%-15s %s" % (tool, path or "MISSING - install FFmpeg"))
        ok &= bool(path)
    try:
        import faster_whisper
        print("%-15s %s" % ("faster-whisper", faster_whisper.__version__))
        m, dev = load_model("tiny", "auto")
        print("%-15s %s" % ("device", dev))
    except SystemExit as e:
        print(e)
        ok = False
    print("OK" if ok else "Something is missing - see above.")
    sys.exit(0 if ok else 1)


def cmd_analyze(a):
    os.makedirs(a.work, exist_ok=True)
    info = probe(a.video)
    audio, db = load_audio(a.video, a.work)
    lv = levels(db)
    spans = find_phrases(db, lv)
    if not spans:
        die("no speech found (is the microphone track silent?)")
    script = open(a.script, encoding="utf-8").read() if a.script else ""
    script_words = [norm(w) for w in script.split() if norm(w)]
    model, device = load_model(a.model, a.device)
    lang = a.language
    if not lang:
        # detect once on up to 30 s of speech, then keep it for every phrase
        sample = np.concatenate([audio[int(s * SR):int(e * SR)] for s, e in spans])[:30 * SR]
        _, info_l = model.transcribe(sample, beam_size=1, without_timestamps=True)
        lang = info_l.language
        print("language: %s (detected, p=%.2f)" % (lang, info_l.language_probability))
    P = []
    for i, (s, e) in enumerate(spans, 1):
        words, logprob = transcribe(model, audio, max(0, s - 0.1), e + 0.1, lang)
        voiced = float((db[int(s / HOP):int(e / HOP)] > lv["on"]).sum() * HOP)
        P.append({"id": i, "start": round(s, 2), "end": round(e, 2),
                  "text": " ".join(w["w"] for w in words), "words": words,
                  "noise": bool(unclear(words, logprob, voiced, e - s)),
                  "flags": inner_flags(words, " " + " ".join(script_words) + " " if script else "")})
        P[-1]["cover"] = script_cover(words, script_words)
        P[-1]["off_script"] = bool(script_words) and not P[-1]["noise"] and not P[-1]["cover"]
        print("#%d %.2f-%.2f %s" % (i, s, e, P[-1]["text"]))
    find_retakes(P, script_words)
    write_takes(a, info, lv, lang, device, P, script_words)


def status(p):
    if p["noise"]:
        return "DROP unclear (noise?)"
    if "retaken_in" in p:
        return "DROP retaken in #%d" % p["retaken_in"]
    if p.get("off_script"):
        return "DROP not in script"
    return "KEEP"


def draft_clips(P):
    keep = [p for p in P if status(p) == "KEEP"]
    clips = []
    for p in keep:
        idx = p["id"] - 1
        prev_end = P[idx - 1]["end"] if idx > 0 else 0.0
        nxt = P[idx + 1]["start"] if idx + 1 < len(P) else p["end"] + 0.5
        end = p["end"]
        if "ending_retaken" in p:
            end = nxt = p["ending_retaken"]["at"] - 0.02
        frm, to = round(max(prev_end, p["start"] - 0.15), 2), round(min(nxt, end + 0.3), 2)
        c = clips[-1] if clips else None
        if c and c["phrases"][-1] == p["id"] - 1 and frm - c["to"] < 2.0 \
                and "ending_retaken" not in P[idx - 1]:
            c["to"] = to
            c["phrases"].append(p["id"])
        else:
            clips.append({"from": frm, "to": to, "phrases": [p["id"]]})
    for c in clips:
        first = next(p for p in P if p["id"] == c["phrases"][0])
        c["note"] = "#%s %s" % (",".join(map(str, c["phrases"])), " ".join(first["text"].split()[:6]))
        del c["phrases"]
    return clips


def write_takes(a, info, lv, lang, device, P, script_words):
    md = ["# Takes: %s" % Path(info["path"]).name, "",
          "%.1f s, %s fps, %dx%d | language %s | whisper %s on %s" % (
              info["duration"], round(float(info["fps"]), 3), info["width"], info["height"],
              lang, a.model, device),
          "levels: room %.0f dB, voice %.0f dB, silence below %.0f dB" % (
              lv["floor"], lv["speech"], lv["quiet"]), ""]
    if info["vfr"]:
        md += ["WARNING: variable frame rate source. Cuts may drift by a frame; "
               "a constant-frame-rate copy is safer.", ""]
    md += ["Status: KEEP = in the draft cuts.json. DROP = said again later, not in the script, "
           "or unclear (cough, click, breath that Whisper turned into words).", ""]
    for p in P:
        st = status(p)
        if "ending_retaken" in p:
            r = p["ending_retaken"]
            st += " (cut before \"%s\" %.2f, said again in #%d)" % (r["from_word"], r["at"], r["in"])
        md.append("- #%d `%.2f-%.2f` %s | %s" % (p["id"], p["start"], p["end"], st, p["text"]))
        md += ["  - !! " + f for f in p["flags"]]
    if script_words:
        said = set()
        for p in P:
            if status(p) == "KEEP":
                said |= set(p["cover"])
        miss, cur = [], []
        for k, w in enumerate(script_words):
            if k in said:
                if len(cur) >= 3:
                    miss.append(" ".join(cur))
                cur = []
            else:
                cur.append(w)
        if len(cur) >= 3:
            miss.append(" ".join(cur))
        md += ["", "Script words not found in KEEP phrases (3+ words in a row):"]
        md += ["- " + m for m in miss] or ["- none"]
    clips = draft_clips(P)
    src = info["path"]
    try:
        src = os.path.relpath(src, os.path.abspath(a.work))
    except ValueError:
        pass
    cuts = {"source": src.replace("\\", "/"), "style": "natural", "clips": clips}
    for p in P:
        p["cover"] = sorted(p["cover"])
    json.dump({"video": info["path"], "language": lang, "levels": lv, "phrases": P},
              open(os.path.join(a.work, "takes.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(cuts, open(os.path.join(a.work, "cuts.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(os.path.join(a.work, "takes.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n%d phrases, %d draft clips -> %s" % (
        len(P), len(clips), os.path.join(a.work, "takes.md")))


# ---------------------------------------------------------------- listen
def cmd_transcribe(a):
    """Transcribe one short window, e.g. to see a repeat Whisper merged in the full phrase."""
    takes = os.path.join(a.work, "takes.json")
    meta = json.load(open(takes, encoding="utf-8")) if os.path.exists(takes) else {}
    audio, _ = load_audio(a.video, a.work)
    model, _ = load_model(a.model, a.device)
    for t0, t1 in zip(a.at[::2], a.at[1::2]):
        words, _ = transcribe(model, audio, t0, t1, a.language or meta.get("language"))
        print("%.2f-%.2f: %s" % (t0, t1, " ".join("%s@%.2f" % (w["w"], w["s"]) for w in words) or "(nothing)"))


# ---------------------------------------------------------------- frames
def cmd_frames(a):
    cols = 6
    rows = int(np.ceil(a.dur * 10 / cols))
    run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(a.at), "-t", str(a.dur), "-i", a.video,
         "-vf", "fps=10,scale=240:-1,tile=%dx%d" % (cols, rows), "-frames:v", "1", a.png])
    print("%s: frames every 0.1 s from %.2f s, left to right" % (a.png, a.at))


# ---------------------------------------------------------------- render
def onset(db, lv, t0, t1):
    """First frame above 'on' that is followed by 0.15 s of mostly voiced audio."""
    n = int(0.15 / HOP)
    for i in range(int(t0 / HOP), int(t1 / HOP)):
        if db[i] > lv["on"] and (db[i:i + n] > lv["quiet"]).mean() > 0.8:
            return i * HOP
    return None


def plan(cuts, db, lv, st):
    segs = []
    for k, c in enumerate(cuts["clips"]):
        t0, t1 = float(c["from"]), float(c["to"])
        if t1 <= t0:
            die("clip %d: 'to' must be after 'from'" % (k + 1))
        s = onset(db, lv, t0, t1)
        if s is None:
            print("clip %d (%s): no speech between %.2f and %.2f - skipped" % (k + 1, c.get("note", ""), t0, t1))
            continue
        voiced = np.nonzero(db[int(t0 / HOP):int(t1 / HOP)] > lv["quiet"])[0]
        e = t0 + (voiced[-1] + 1) * HOP
        nxt = next((i * HOP for i in range(int(e / HOP), int(t1 / HOP)) if db[i] > lv["on"]), t1)
        f = e
        while f < min(e + st["tail"], nxt - 0.02, t1) and db[int(f / HOP)] > lv["fade"]:
            f += HOP
        start, end = max(t0, s - st["lead"]), min(t1, max(f, e + 0.03))
        keep, x = [], start
        for a_, b_ in sorted(tuple(map(float, g)) for g in c.get("skip", [])):
            if a_ > x:
                keep.append([x, min(a_, end)])
            x = max(x, b_)
        if end > x:
            keep.append([x, end])
        h = st["keep_pause"] / 2
        for x, y in keep:
            # pauses longer than max_pause shrink to keep_pause (cut in the middle).
            # tight: a pause is anything below the speech-free level, and short
            # islands between pauses that never reach voice level (breaths) go too
            thr = lv["pause"] if st["breaths"] else lv["quiet"]
            gaps = [(x + ps * HOP, x + pe * HOP) for ps, pe in runs(db[int(x / HOP):int(y / HOP)] < thr)]
            gaps = [(ps, pe) for ps, pe in gaps if pe - ps > st["max_pause"] and ps > x and pe < y]
            edges = [x] + [t for g in gaps for t in g] + [y]
            isl = [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]
            for i, (a_, b_) in enumerate(isl):
                if st["breaths"] and len(isl) > 1 and b_ - a_ < 0.6 \
                        and db[int(a_ / HOP):int(b_ / HOP) + 1].max() < lv["breath"]:
                    print("  dropped breath/noise %.2f-%.2f" % (a_, b_))
                    continue
                segs.append([a_ - h if i > 0 else a_, b_ + h if i < len(isl) - 1 else b_])
    return segs


def to_frames(segs, fps, total):
    out = []
    for x, y in segs:
        f0, f1 = int(np.floor(x * fps)), min(total, int(np.ceil(y * fps)))
        if f1 <= f0:
            continue
        if out and f0 <= out[-1][1]:
            out[-1][1] = max(out[-1][1], f1)
        else:
            out.append([f0, f1])
    return out


def render_mp4(info, F, out):
    fps = info["fps"]
    sel = "+".join("between(n\\,%d\\,%d)" % (f0, f1 - 1) for f0, f1 in F)
    fc = ["[0:v:0]select='%s',setpts=N/(FRAME_RATE*TB)[v]" % sel]
    for k, (f0, f1) in enumerate(F):
        d = float((f1 - f0) / fps)
        fc.append("[0:a:0]atrim=start=%.6f:end=%.6f,asetpts=PTS-STARTPTS,"
                  "afade=t=in:d=0.005,afade=t=out:st=%.6f:d=0.005[a%d]"
                  % (float(f0 / fps), float(f1 / fps), max(0.0, d - 0.005), k))
    fc.append("%sconcat=n=%d:v=0:a=1[a]" % ("".join("[a%d]" % k for k in range(len(F))), len(F)))
    script = out + ".filter.txt"
    open(script, "w", encoding="utf-8").write(";\n".join(fc))
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", info["path"],
         "-filter_complex_script", script, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
         "-r", str(fps), "-fps_mode", "cfr", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", out])
    os.remove(script)


# ---------------------------------------------------------------- timecode
def is_drop(tc, fps):
    return ";" in tc and fps.denominator == 1001 and round(fps) in (30, 60)


def tc_to_frames(tc, fps):
    h, m, s, f = map(int, re.split(r"[:;.]", tc))
    nom = round(fps)
    n = ((h * 3600 + m * 60 + s) * nom) + f
    if is_drop(tc, fps):
        d = round(nom / 15)                       # 2 at 29.97, 4 at 59.94
        tm = h * 60 + m
        n -= d * (tm - tm // 10)
    return n


def frames_to_tc(n, fps, drop):
    nom = round(fps)
    if drop:
        d = round(nom / 15)
        per10, per1 = nom * 600 - d * 9, nom * 60 - d
        q, r = divmod(n, per10)
        n += d * 9 * q + (d * ((r - d) // per1) if r > d else 0)
    sep = ";" if drop else ":"
    return "%02d:%02d:%02d%s%02d" % (n // (nom * 3600) % 24, n // (nom * 60) % 60,
                                     n // nom % 60, sep, n % nom)


# ---------------------------------------------------------------- exports
def rt(n, fps):
    """n frames as an FCPXML rational time."""
    return "0s" if n == 0 else "%d/%ds" % (n * fps.denominator, fps.numerator)


def export_fcpxml(info, F, name, path):
    fps, drop = info["fps"], is_drop(info["tc"], info["fps"])
    src0 = tc_to_frames(info["tc"], fps)
    rec0 = 0   # sequence starts at 00:00:00:00 (some readers ignore tcStart)
    total = sum(f1 - f0 for f0, f1 in F)
    tcf = "DF" if drop else "NDF"
    clips, off = [], 0
    for f0, f1 in F:
        clips.append('        <asset-clip ref="r2" offset=%s name=%s start=%s duration=%s tcFormat="%s"/>'
                     % (quoteattr(rt(rec0 + off, fps)), quoteattr(Path(info["path"]).stem),
                        quoteattr(rt(src0 + f0, fps)), quoteattr(rt(f1 - f0, fps)), tcf))
        off += f1 - f0
    x = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r1" frameDuration="%s" width="%d" height="%d"/>
    <asset id="r2" name=%s start="%s" duration="%s" hasVideo="1" format="r1" hasAudio="1" audioSources="1" audioChannels="%d" audioRate="%d">
      <media-rep kind="original-media" src=%s/>
    </asset>
  </resources>
  <library>
    <event name=%s>
      <project name=%s>
        <sequence format="r1" duration="%s" tcStart="%s" tcFormat="%s" audioLayout="stereo" audioRate="48k">
          <spine>
%s
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
""" % (rt(1, fps), info["width"], info["height"], quoteattr(Path(info["path"]).stem),
       rt(src0, fps), rt(info["frames"], fps), info["channels"], info["rate"],
       quoteattr(Path(info["path"]).as_uri()), quoteattr(name), quoteattr(name),
       rt(total, fps), rt(rec0, fps), tcf, "\n".join(clips).replace("        <asset", "            <asset"))
    open(path, "w", encoding="utf-8").write(x)


def export_xmeml(info, F, name, path):
    fps, drop = info["fps"], is_drop(info["tc"], info["fps"])
    ntsc = "TRUE" if fps.denominator == 1001 else "FALSE"
    rate = "<rate><timebase>%d</timebase><ntsc>%s</ntsc></rate>" % (round(fps), ntsc)
    fmt = "DF" if drop else "NDF"
    rec_tc = "01:00:00" + (";" if drop else ":") + "00"
    url = Path(info["path"]).as_uri().replace("file:///", "file://localhost/")
    fname = escape(Path(info["path"]).name)
    file_full = """<file id="file-1"><name>%s</name><pathurl>%s</pathurl>%s<duration>%d</duration>
<timecode>%s<string>%s</string><frame>%d</frame><displayformat>%s</displayformat></timecode>
<media><video><samplecharacteristics>%s<width>%d</width><height>%d</height></samplecharacteristics></video>
<audio><samplecharacteristics><depth>16</depth><samplerate>%d</samplerate></samplecharacteristics><channelcount>%d</channelcount></audio></media></file>""" % (
        fname, escape(url), rate, info["frames"], rate, info["tc"], tc_to_frames(info["tc"], fps), fmt,
        rate, info["width"], info["height"], info["rate"], info["channels"])
    v, au, t = [], [], 0
    for k, (f0, f1) in enumerate(F, 1):
        d = f1 - f0
        link = ("<link><linkclipref>clipitem-v%d</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>%d</clipindex></link>"
                "<link><linkclipref>clipitem-a%d</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>%d</clipindex></link>") % (k, k, k, k)
        common = "<name>%s</name><enabled>TRUE</enabled><duration>%d</duration>%s<start>%d</start><end>%d</end><in>%d</in><out>%d</out>" % (
            fname, info["frames"], rate, t, t + d, f0, f1)
        v.append('<clipitem id="clipitem-v%d">%s%s%s</clipitem>' % (
            k, common, file_full if k == 1 else '<file id="file-1"/>', link))
        au.append('<clipitem id="clipitem-a%d">%s<file id="file-1"/><sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>%s</clipitem>' % (
            k, common, link))
        t += d
    x = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
<sequence id="sequence-1"><name>%s</name><duration>%d</duration>%s
<timecode>%s<string>%s</string><frame>%d</frame><displayformat>%s</displayformat></timecode>
<media>
<video><format><samplecharacteristics>%s<width>%d</width><height>%d</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>
<track>
%s
</track></video>
<audio><numOutputChannels>2</numOutputChannels>
<track>
%s
</track></audio>
</media></sequence>
</xmeml>
""" % (escape(name), t, rate, rate, rec_tc, tc_to_frames(rec_tc, fps), fmt,
       rate, info["width"], info["height"], "\n".join(v), "\n".join(au))
    open(path, "w", encoding="utf-8").write(x)


def us(n, fps):
    """n frames in microseconds (CapCut's time unit)."""
    return int(round(Fraction(n) * 1000000 / fps))


def capcut_parts(info, F):
    """[(source_start, target_start, duration)] in microseconds. Source and
    target durations are equal (speed 1.0); clips sit back to back."""
    fps, out, t = info["fps"], [], 0
    for f0, f1 in F:
        d = us(f1, fps) - us(f0, fps)
        out.append((us(f0, fps), t, d))
        t += d
    return out


def export_capcut(info, F, name, folder):
    """A CapCut desktop project folder (draft_content.json + draft_meta_info.json):
    one video track, one clip per segment, all pointing at the source video."""
    os.makedirs(folder, exist_ok=True)
    new_id = lambda: str(uuid.uuid4()).upper()
    src = Path(info["path"]).as_posix()
    vid = new_id()
    mats = {k: [] for k in (
        "videos", "audios", "images", "texts", "effects", "stickers", "canvases", "transitions",
        "audio_effects", "audio_fades", "beats", "material_animations", "placeholders",
        "placeholder_infos", "speeds", "common_mask", "chromas", "text_templates",
        "realtime_denoises", "audio_pannings", "audio_pitch_shifts", "video_trackings", "hsl",
        "drafts", "color_curves", "hsl_curves", "primary_color_wheels", "log_color_wheels",
        "video_effects", "audio_balances", "handwrites", "manual_deformations", "manual_beautys",
        "plugin_effects", "sound_channel_mappings", "green_screens", "shapes", "material_colors",
        "smart_crops", "ai_translates", "audio_track_indexes", "loudnesses", "vocal_beautifys",
        "vocal_separations", "smart_relights", "time_marks", "multi_language_refs", "flowers",
        "tail_leaders", "digital_humans")}
    mats["videos"].append({
        "id": vid, "type": "video", "path": src, "media_path": "", "material_name": Path(src).name,
        "duration": us(info["frames"], info["fps"]), "width": info["width"], "height": info["height"],
        "has_audio": True, "category_name": "local", "category_id": "", "material_id": "",
        "local_material_id": str(uuid.uuid4()), "check_flag": 62978047, "source": 0,
        "source_platform": 0, "crop_ratio": "free", "crop_scale": 1.0, "audio_fade": None,
        "crop": {"upper_left_x": 0.0, "upper_left_y": 0.0, "upper_right_x": 1.0, "upper_right_y": 0.0,
                 "lower_left_x": 0.0, "lower_left_y": 1.0, "lower_right_x": 1.0, "lower_right_y": 1.0},
        "extra_type_option": 0, "reverse_path": "", "intensifies_path": "",
        "reverse_intensifies_path": "", "intensifies_audio_path": "", "cartoon_path": ""})
    segs = []
    for src_t, tgt_t, d in capcut_parts(info, F):
        refs = {"speeds": {"type": "speed", "mode": 0, "speed": 1.0, "curve_speed": None},
                "placeholder_infos": {"type": "placeholder_info", "meta_type": "none", "res_path": "",
                                      "res_text": "", "error_path": "", "error_text": ""},
                "canvases": {"type": "canvas_color", "color": "", "blur": 0.0, "image": "",
                             "album_image": "", "image_id": "", "image_name": "",
                             "source_platform": 0, "team_id": ""},
                "sound_channel_mappings": {"type": "none", "audio_channel_mapping": 0,
                                           "is_config_open": False},
                "vocal_separations": {"type": "vocal_separation", "choice": 0, "removed_sounds": [],
                                      "time_range": None, "production_path": "",
                                      "final_algorithm": "", "enter_from": ""}}
        ids = []
        for kind, m in refs.items():
            m["id"] = new_id()
            mats[kind].append(m)
            ids.append(m["id"])
        segs.append({
            "id": new_id(), "material_id": vid, "extra_material_refs": ids,
            "source_timerange": {"start": src_t, "duration": d},
            "target_timerange": {"start": tgt_t, "duration": d},
            "render_timerange": {"start": 0, "duration": 0},
            "speed": 1.0, "volume": 1.0, "last_nonzero_volume": 1.0, "visible": True,
            "reverse": False, "is_loop": False, "is_tone_modify": False, "intensifies_audio": False,
            "cartoon": False, "desc": "", "state": 0, "render_index": 0, "track_render_index": 0,
            "track_attribute": 0, "keyframe_refs": [], "common_keyframes": [], "group_id": "",
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0, "transform": {"x": 0.0, "y": 0.0},
                     "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": True, "value": 1.0}, "enable_lut": True, "enable_adjust": True,
            "enable_hsl": False, "enable_color_curves": True, "enable_hsl_curves": True,
            "enable_color_wheels": True, "enable_smart_color_adjust": False,
            "enable_color_match_adjust": False, "enable_color_correct_adjust": False,
            "enable_adjust_mask": False, "enable_video_mask": True, "is_placeholder": False,
            "template_id": "", "template_scene": "default", "source": "segmentsourcenormal",
            "hdr_settings": {"mode": 1, "intensity": 1.0, "nits": 1000}})
    total = segs[-1]["target_timerange"]["start"] + segs[-1]["target_timerange"]["duration"]
    draft_id = new_id()
    plat = {"os": "mac" if sys.platform == "darwin" else "windows", "app_id": 359289,
            "app_source": "cc", "app_version": "9.5.0"}
    content = {
        "id": draft_id, "version": 360000, "new_version": "187.0.0", "name": "",
        "duration": total, "create_time": 0, "update_time": 0, "fps": float(info["fps"]),
        "is_drop_frame_timecode": False, "color_space": 0,
        "config": {"video_mute": False, "record_audio_last_index": 1, "extract_audio_last_index": 1,
                   "original_sound_last_index": 1, "subtitle_recognition_id": "",
                   "subtitle_taskinfo": [], "lyrics_recognition_id": "", "lyrics_taskinfo": [],
                   "subtitle_sync": True, "lyrics_sync": True, "voice_change_sync": False,
                   "sticker_max_index": 1, "adjust_max_index": 1, "material_save_mode": 0,
                   "export_range": None, "maintrack_adsorb": True, "combination_max_index": 1,
                   "attachment_info": [], "zoom_info_params": None, "system_font_list": [],
                   "multi_language_mode": "none", "multi_language_main": "none",
                   "multi_language_current": "none", "multi_language_list": [],
                   "subtitle_keywords_config": None, "use_float_render": False},
        "canvas_config": {"ratio": "original", "width": info["width"], "height": info["height"],
                          "background": None},
        "tracks": [{"id": new_id(), "type": "video", "flag": 0, "attribute": 0, "name": "",
                    "is_default_name": True, "segments": segs}],
        "group_container": None, "materials": mats,
        "keyframes": {k: [] for k in ("videos", "audios", "texts", "stickers", "filters", "adjusts",
                                      "handwrites", "effects")},
        "keyframe_graph_list": [], "platform": plat, "last_modified_platform": plat,
        "mutable_config": None, "cover": None, "retouch_cover": None, "extra_info": None,
        "relationships": [], "render_index_track_mode_on": True, "free_render_index_mode_on": False,
        "static_cover_image_path": "", "source": "default", "time_marks": None, "path": "",
        "lyrics_effects": [], "draft_type": "video"}
    now = int(time.time() * 1000000)
    meta = {
        "draft_id": draft_id, "draft_name": Path(folder).name, "draft_fold_path": "",
        "draft_root_path": "", "draft_cover": "", "draft_materials": [], "draft_materials_copied_info": [],
        "draft_segment_extra_info": [], "draft_type": "", "draft_new_version": "",
        "draft_removable_storage_device": "", "draft_is_invisible": False,
        "draft_is_from_deeplink": "false", "draft_is_ai_shorts": False,
        "draft_is_cloud_temp_draft": False, "cloud_draft_cover": False, "cloud_draft_sync": False,
        "draft_timeline_materials_size_": 0, "tm_draft_create": now, "tm_draft_modified": now,
        "tm_draft_removed": 0, "tm_duration": total}
    for fn, data in (("draft_content.json", content), ("draft_meta_info.json", meta)):
        with open(os.path.join(folder, fn), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def export_edl(info, F, name, path):
    fps, drop = info["fps"], is_drop(info["tc"], info["fps"])
    src0 = tc_to_frames(info["tc"], fps)
    rec = tc_to_frames("01:00:00" + (";" if drop else ":") + "00", fps)
    tc = lambda n: frames_to_tc(n, fps, drop)
    lines = ["TITLE: %s" % name[:70], "FCM: %s" % ("DROP FRAME" if drop else "NON-DROP FRAME"), ""]
    for k, (f0, f1) in enumerate(F, 1):
        d = f1 - f0
        lines += ["%03d  AX       AA/V  C        %s %s %s %s" % (
            k, tc(src0 + f0), tc(src0 + f1), tc(rec), tc(rec + d)),
            "* FROM CLIP NAME: %s" % Path(info["path"]).name, ""]
        rec += d
    open(path, "w", encoding="utf-8", newline="\r\n").write("\n".join(lines))


# ---------------------------------------------------------------- check
def check(info, F, base, mp4):
    """Every export must describe the same timeline (and match the MP4)."""
    fps = info["fps"]
    want = [(f0, f1) for f0, f1 in F]
    total = sum(f1 - f0 for f0, f1 in F)
    errs = []
    # FCPXML
    r = ET.parse(base + ".fcpxml").getroot()
    src0 = tc_to_frames(info["tc"], fps)
    fr = lambda s: Fraction(s.rstrip("s")) * fps
    got = [(int(fr(c.get("start")) - src0), int(fr(c.get("start")) - src0 + fr(c.get("duration"))))
           for c in r.iter("asset-clip")]
    offs = [int(fr(c.get("offset"))) for c in r.iter("asset-clip")]
    if got != want or any(b != a + (f1 - f0) for a, b, (f0, f1) in zip(offs, offs[1:], want)):
        errs.append("fcpxml clips differ from the plan")
    # xmeml
    r = ET.parse(base + ".xml").getroot()
    for kind in ("video", "audio"):
        items = r.find("sequence/media/" + kind).iter("clipitem")
        got = [(int(c.findtext("in")), int(c.findtext("out")), int(c.findtext("start")), int(c.findtext("end")))
               for c in items]
        t, ok = 0, len(got) == len(want)
        for (i, o, s, e), (f0, f1) in zip(got, want):
            ok &= (i, o, s, e) == (f0, f1, t, t + f1 - f0)
            t += f1 - f0
        if not ok:
            errs.append("xml %s track differs from the plan" % kind)
    if int(r.findtext("sequence/duration")) != total:
        errs.append("xml sequence duration differs")
    # EDL
    drop = is_drop(info["tc"], fps)
    ev = [l.split() for l in open(base + ".edl", encoding="utf-8") if re.match(r"^\d{3} ", l)]
    rec = tc_to_frames("01:00:00" + (";" if drop else ":") + "00", fps)
    got = [(tc_to_frames(e[4], fps) - src0, tc_to_frames(e[5], fps) - src0) for e in ev]
    if got != want or (ev and tc_to_frames(ev[-1][7], fps) - rec != total):
        errs.append("edl events differ from the plan")
    # CapCut project: clip times in microseconds, within half a frame of the plan
    cc = os.path.join(base + "-capcut", "draft_content.json")
    try:
        d = json.load(open(cc, encoding="utf-8"))
        vids = {m["id"]: m for m in d["materials"]["videos"]}
        segs = [s for t in d["tracks"] if t["type"] == "video" for s in t["segments"]]
        half, t, rec = 500000 / float(fps), 0, 0
        ok = len(d["tracks"]) == 1 and len(segs) == len(want) and abs(d["fps"] - float(fps)) < 1e-6
        for s, (f0, f1) in zip(segs, want):
            src, tgt = s["source_timerange"], s["target_timerange"]
            m = vids.get(s["material_id"], {})
            ok &= src["duration"] == tgt["duration"] and tgt["start"] == t and s["speed"] == 1.0
            ok &= abs(src["start"] - float(f0 / fps) * 1e6) < half
            ok &= abs(src["start"] + src["duration"] - float(f1 / fps) * 1e6) < half
            ok &= abs(tgt["start"] - float(rec / fps) * 1e6) < half
            ok &= os.path.normcase(os.path.abspath(m.get("path", ""))) == os.path.normcase(info["path"])
            t += tgt["duration"]
            rec += f1 - f0
        ok &= d["duration"] == t and abs(t - float(total / fps) * 1e6) < half
        if not ok:
            errs.append("capcut project differs from the plan")
        if not all(os.path.exists(m["path"]) for m in vids.values()):
            errs.append("capcut project points to a missing video")
    except (OSError, ValueError, KeyError) as e:
        errs.append("capcut project unreadable: %s" % e)
    print("timeline: %d segments, %d frames = %.3f s" % (len(F), total, float(total / fps)))
    if mp4:
        p = json.loads(run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", mp4]))
        v = next(s for s in p["streams"] if s["codec_type"] == "video")
        a = next(s for s in p["streams"] if s["codec_type"] == "audio")
        nv, da = int(v["nb_read_frames"]), float(a["duration"])
        print("mp4: %d video frames, audio %.3f s" % (nv, da))
        if abs(nv - total) > 1:
            errs.append("mp4 has %d frames, timeline %d" % (nv, total))
        if abs(da - float(total / fps)) > 1.5 / float(fps) + 0.03:
            errs.append("mp4 audio %.3f s vs timeline %.3f s" % (da, float(total / fps)))
    for e in errs:
        print("CHECK FAILED: " + e)
    if errs:
        sys.exit(1)
    print("CHECK OK: fcpxml, xml, edl, capcut%s agree" % (" and mp4" if mp4 else ""))


def cmd_render(a):
    cuts_path = os.path.abspath(a.cuts)
    cuts = json.load(open(cuts_path, encoding="utf-8"))
    work = os.path.dirname(cuts_path)
    src = cuts["source"]
    if not os.path.isabs(src):
        src = os.path.join(work, src)
    info = probe(src)
    if info["vfr"]:
        print("WARNING: variable frame rate source - cuts may be off by a frame in editors.")
    style = a.style or cuts.get("style", "natural")
    if style not in STYLES:
        die("style must be one of: %s" % ", ".join(STYLES))
    st = dict(STYLES[style])
    for k in ("max_pause", "keep_pause"):
        if getattr(a, k) is not None:
            st[k] = getattr(a, k)
    _, db = load_audio(src, work)
    lv = levels(db)
    F = to_frames(plan(cuts, db, lv, st), info["fps"], info["frames"])
    if not F:
        die("nothing to render")
    name = a.name or Path(src).stem + "-cut"
    base = os.path.join(a.out_dir or work, name)
    os.makedirs(os.path.dirname(base), exist_ok=True)
    export_fcpxml(info, F, name, base + ".fcpxml")
    export_xmeml(info, F, name, base + ".xml")
    export_edl(info, F, name, base + ".edl")
    export_capcut(info, F, name, base + "-capcut")
    fps = info["fps"]
    t, rows = 0, []
    for f0, f1 in F:
        rows.append({"src_in": round(float(f0 / fps), 3), "src_out": round(float(f1 / fps), 3),
                     "frames": [f0, f1], "at": round(float(t / fps), 3)})
        t += f1 - f0
    json.dump({"source": info["path"], "fps": str(fps), "style": style, "segments": rows},
              open(base + "-segments.json", "w", encoding="utf-8"), indent=1)
    mp4 = None
    if a.mp4:
        mp4 = base + ".mp4"
        print("rendering %s ..." % mp4)
        render_mp4(info, F, mp4)
    print("source %.2f s -> cut %.2f s (%d segments, style %s)" % (
        info["duration"], float(t / fps), len(F), style))
    for ext in (".fcpxml", ".xml", ".edl", "-capcut" + os.sep) + ((".mp4",) if mp4 else ()):
        print("  " + base + ext)
    check(info, F, base, mp4)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("doctor", help="check FFmpeg, faster-whisper and GPU")
    p = sp.add_parser("analyze", help="transcribe phrases, flag retakes, draft cuts.json")
    p.add_argument("video")
    p.add_argument("--work", required=True, help="folder for takes.md, cuts.json and exports")
    p.add_argument("--script", help="optional text the speaker read (plain text)")
    p.add_argument("--language", help="language code, e.g. en, pl, de (default: detect)")
    p.add_argument("--model", default="large-v3-turbo", help="whisper model (default large-v3-turbo; "
                   "small or base are faster on CPU)")
    p.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    p = sp.add_parser("transcribe", help="transcribe short windows: --at FROM TO [FROM TO ...]")
    p.add_argument("video")
    p.add_argument("--work", required=True)
    p.add_argument("--at", type=float, nargs="+", required=True)
    p.add_argument("--language")
    p.add_argument("--model", default="large-v3-turbo")
    p.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    p = sp.add_parser("frames", help="contact sheet of frames (where the speaker looks)")
    p.add_argument("video")
    p.add_argument("--at", type=float, required=True)
    p.add_argument("--dur", type=float, default=2.0)
    p.add_argument("--png", required=True)
    p = sp.add_parser("render", help="exports (fcpxml, xml, edl, capcut) and optional mp4 from cuts.json")
    p.add_argument("cuts")
    p.add_argument("--style", choices=tuple(STYLES))
    p.add_argument("--max-pause", type=float, help="pauses longer than this are shortened (s)")
    p.add_argument("--keep-pause", type=float, help="...to this length (s)")
    p.add_argument("--mp4", action="store_true", help="also render the cut as MP4")
    p.add_argument("--name", help="output base name (default: <video>-cut)")
    p.add_argument("--out-dir", help="output folder (default: folder of cuts.json)")
    a = ap.parse_args()
    {"doctor": cmd_doctor, "analyze": cmd_analyze, "transcribe": cmd_transcribe,
     "frames": cmd_frames, "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    main()
