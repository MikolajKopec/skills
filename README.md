# Skills

Praktyczne skille do Claude Code dla twórców: wideo, produktywność, biznes. Wszystkie są w jednym pluginie, który instalujesz raz i który sam się aktualizuje.

## Instalacja

W Claude Code:

```
/plugin marketplace add MikolajKopec/skills
/plugin install mikolajkopec-skills@mikolajkopec
```

Albo z terminala:

```
claude plugin marketplace add MikolajKopec/skills
claude plugin install mikolajkopec-skills@mikolajkopec
```

Aktualizacja: `/plugin marketplace update mikolajkopec`.

## Skille

Skille leżą w folderach kategorii w `skills/`. **Model-invoked** to skille, po które Claude sięga sam, gdy zadanie pasuje (możesz też wywołać je ręcznie, np. `/cut-takes`).

### Video

Montaż nagrań.

**Model-invoked**

- **[cut-takes](./skills/video/cut-takes/SKILL.md)**: Z surowego nagrania gadającej głowy wycina powtórzone take'i, falstarty i długie pauzy. Oddaje oś czasu do DaVinci Resolve, Premiere Pro, Final Cut Pro i CapCuta na komputerze, a na życzenie gotowe MP4.

#### cut-takes

Wymagania:

- Python 3.9 lub nowszy,
- FFmpeg w PATH: Windows `winget install Gyan.FFmpeg`, macOS `brew install ffmpeg`, Linux `sudo apt install ffmpeg`,
- `python -m pip install faster-whisper`,
- opcjonalnie karta NVIDIA: `python -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"` (ok. 2 GB). Bez tego wszystko działa na procesorze, tylko wolniej.

Przy pierwszym uruchomieniu skrypt pobiera model Whisper (large-v3-turbo, ok. 1,6 GB).

Użycie: w Claude Code napisz np. „Potnij take'i w nagraniu nagrania/film.mp4, tekst jest w scenariusz.txt”. Skill transkrybuje nagranie fraza po frazie, zaznacza powtórzone podejścia i falstarty, wybiera ostatnie pełne podejście każdej kwestii i składa oś czasu. Tekst scenariusza jest opcjonalny, język wykrywa się sam.

Co dostajesz:

| Plik | Do czego |
|---|---|
| `.fcpxml` | Final Cut Pro (File > Import > XML), DaVinci Resolve (File > Import > Timeline) |
| `.xml` | Premiere Pro (File > Import), DaVinci Resolve |
| `.edl` | CMX 3600, inne programy do montażu |
| folder `-capcut` | projekt CapCuta na komputerze (Windows, macOS) z pociętymi klipami do dalszej edycji |
| `.mp4` | gotowy film |

CapCut: zamknij CapCuta, skopiuj cały folder `-capcut` do folderu projektów CapCuta i uruchom go ponownie. Projekt pojawi się na ekranie startowym. Folder projektów: Windows `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft`, macOS `~/Movies/CapCut/User Data/Projects/com.lveditor.draft` (albo ścieżka ustawiona w CapCucie). Jeśli projekt się nie otworzy, użyj `.mp4`.

Oś czasu wskazuje na oryginalny plik wideo, więc nie przenoś go przed importem. Skill nie dodaje napisów, muzyki ani efektów.

## Licencja

MIT, zobacz [LICENSE](LICENSE).

---

## English

Practical Claude Code skills for creators, shipped as one self-updating plugin. Install it in Claude Code:

```
/plugin marketplace add MikolajKopec/skills
/plugin install mikolajkopec-skills@mikolajkopec
```

**[cut-takes](./skills/video/cut-takes/SKILL.md)** (video) cuts retakes, false starts and long pauses out of a raw talking-head recording and exports an editable timeline: `.fcpxml` (Final Cut Pro, DaVinci Resolve), `.xml` (Premiere Pro, DaVinci Resolve), `.edl` (CMX 3600), a CapCut desktop project folder (quit CapCut, copy the `-capcut` folder into CapCut's projects folder, `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft` on Windows or `~/Movies/CapCut/User Data/Projects/com.lveditor.draft` on macOS, then restart CapCut) and optionally an `.mp4`. Any language, script optional, CPU or NVIDIA GPU. Requires Python 3.9+, FFmpeg on PATH and `pip install faster-whisper`. MIT licensed.
