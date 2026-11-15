---
name: cut-takes
description: Use when the user has a raw talking-head recording (one person speaking to camera, with repeated takes, false starts, off-camera comments or long pauses) and wants a rough cut - the best take of every line kept once, pauses tightened - delivered as an editable timeline for DaVinci Resolve, Premiere Pro, Final Cut Pro or CapCut desktop and optionally as an MP4. Works in any language, with or without the script that was read. Does not add captions, music or effects.
license: MIT
compatibility: Needs Python 3.9+, FFmpeg on PATH and the faster-whisper package. Runs on CPU; uses an NVIDIA GPU when available. The first run downloads a Whisper model (about 1.6 GB for large-v3-turbo).
---

# Cut takes

Turn a raw talking-head recording into a rough cut: every line said once, in its last complete take, with long pauses shortened. The script finds speech, transcribes it phrase by phrase and measures levels. You decide which takes stay.

Script: `python "${CLAUDE_SKILL_DIR}/scripts/cut_takes.py"` (use `python3` where `python` is Python 2 or missing). Below it is written as `cut_takes.py`.

## 0. Setup (once)

Run `cut_takes.py doctor`. If something is missing, tell the user and offer the install command:

- FFmpeg: Windows `winget install Gyan.FFmpeg`, macOS `brew install ffmpeg`, Linux `sudo apt install ffmpeg`.
- Python package: `python -m pip install faster-whisper`.
- Optional NVIDIA GPU: `python -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"` (about 2 GB). Without it everything runs on CPU, just slower.

## 1. Analyze

Ask for the video path and, if the speaker read a script, the script as plain text (optional). Pick a work folder next to the video, e.g. `<video folder>/<video name>-cut/`.

```
cut_takes.py analyze VIDEO --work WORK [--script SCRIPT.txt] [--language en]
```

- Language is detected automatically; pass `--language` if the detection line in the output is wrong.
- On CPU a long recording takes a while. For a quick pass add `--model small`.
- Very large files (4K, over 20 minutes) work, but rendering is faster from a 1080p copy.

Output in WORK: `takes.md` (read this), `takes.json`, and a draft `cuts.json`.

## 2. Choose takes

Read `takes.md`. Every phrase has an id, source time, text and status:

- `KEEP` / `DROP retaken in #N` - the script already assumes the **last complete take wins**. Check each guess.
- `DROP unclear (noise?)` - a cough, click or breath that Whisper turned into words ("Thank you."). `DROP not in script` - only with `--script`.
- `(cut before "word" ...)` - only the ending of the phrase was said again later; the draft clip ends before that word.
- `!! repeated ...` / `!! false start ...` / `!! long word ...` - a stumble inside a phrase (a long word is often a repeat Whisper merged). Check it by transcribing short windows, then remove it with `skip` or use another take:

  ```
  cut_takes.py transcribe VIDEO --work WORK --at 86.1 87.0 87.4 88.6
  ```
- `Script words not found` - lines that no kept phrase covers. Look for them in DROP phrases; if missing, tell the user.

Then edit `cuts.json`:

- Drop off-camera talk ("okay, once more", "is it recording?"), mic checks and unfinished takes. An unfinished line is better removed than kept.
- A comment often refers to the take before it: "let's not use that" right after a line means that line goes too.
- If two takes are equally complete, prefer the one closer to the script.
- `from` goes in the silence before the take, `to` in the silence after it. Keep clips in the order they should play.
- A stumble inside a good take: `"skip": [[a, b]]` with `a` right after the last good word and `b` just before the clean repeat. Use word times from `takes.json` to place it.

Optional: check where the speaker looks at the end of a take (glancing at notes ruins the cut):

```
cut_takes.py frames VIDEO --at 41.2 --dur 2 --png WORK/end.png
```

Look at the PNG and end `to` before the gaze leaves the camera.

## 3. Render

```
cut_takes.py render WORK/cuts.json [--style natural|tight] [--mp4]
```

- `natural` (default, YouTube and long-form): pauses over 0.5 s shrink to 0.3 s.
- `tight` (shorts, reels): every pause is cut, and breaths and lip clicks between phrases go too (listed as `dropped breath/noise`).
- Fine-tune with `--max-pause` and `--keep-pause` (seconds).
- Add `--mp4` only when the user wants a ready video file.

It writes `<video>-cut.fcpxml`, `.xml`, `.edl`, a CapCut project folder `<video>-cut-capcut/`, `-segments.json` (and `.mp4`) to WORK and ends with `CHECK OK` when all exports agree with each other and with the MP4. On `CHECK FAILED`, report the message and do not hand over the files.

With `--mp4`, transcribe the result once (`cut_takes.py transcribe WORK/<video>-cut.mp4 --work WORK --at 0 <length>`) and read it: every line once, no repeats, no word cut in half.

## 4. Hand over

Tell the user the length before and after, the number of segments, which takes you dropped and why, and anything to listen to. Then tell them how to import:

| Editor | File | How |
|---|---|---|
| DaVinci Resolve | `.fcpxml` (or `.xml`) | File > Import > Timeline, then pick the file. Relink media if asked |
| Premiere Pro | `.xml` | File > Import, pick the `.xml`; a new sequence appears in the project |
| Final Cut Pro | `.fcpxml` | File > Import > XML |
| Other editors | `.edl` | CMX 3600 EDL, import and relink to the source video |
| CapCut desktop | `-capcut/` folder | Quit CapCut, copy the whole folder into the CapCut projects folder (below), start CapCut and open the project from the home screen |

CapCut projects folder: Windows `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft`, macOS `~/Movies/CapCut/User Data/Projects/com.lveditor.draft`, or the path set in CapCut's settings. Do not write into it yourself; tell the user to copy the folder. If the project does not appear or does not open, fall back to the `.mp4`.

The timeline points to the original video file, so do not move or rename it before importing. Every cut is a hard cut on a single track; add crossfades in the editor if a cut clicks.

To change something, edit `cuts.json` and render again.
