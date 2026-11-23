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

### Web

Strony internetowe.

**Model-invoked**

- **[iterate-with-review](./skills/web/iterate-with-review/SKILL.md)**: Poprawia stronę w kolejnych turach bez udziału użytkownika. W każdej turze agent wprowadza zmiany i robi zrzuty ekranu, a recenzenci z personami dobranymi do strony oceniają je świeżym okiem. Do następnej tury przechodzą tylko uwagi, które mają sens.

#### iterate-with-review

Wymagania:

- kod strony na dysku: folder projektu albo adres repo git, które skill sklonuje,
- coś do zrzutów ekranu: dołączony skrypt potrzebuje Pythona 3.9 lub nowszego i Playwrighta (`python -m pip install playwright`, potem `python -m playwright install chromium`); zamiast niego może być dowolne narzędzie do przeglądarki, z którego korzysta Claude,
- opcjonalnie git: wtedy po każdej turze jest zwykły commit, bez pusha.

Użycie: w Claude Code napisz np. „Zrób 3 tury iterate-with-review na stronie w folderze moja-strona. Strona jest dla właścicieli małych sklepów internetowych”. Opis odbiorcy jest opcjonalny, ale pomaga. Skill czyta stronę i dobiera do niej persony: pierwsza to odbiorca, do którego strona mówi, a druga to ktoś, kto patrzy z innej strony i ma wpływ na decyzję, np. inwestor przy landingu startupu. Własne persony mają pierwszeństwo. Potem skill sam robi kolejne tury. Pyta tylko wtedy, gdy nie może dostać kodu strony albo jej uruchomić.

Jedna tura:

1. Agent analizuje stronę i spisuje zmiany.
2. Wprowadza je w kodzie.
3. Robi zrzuty ekranu w wersji na komputer i na telefon.
4. Recenzenci, po jednym na personę, oceniają zrzuty. Nie widzą planu, kodu ani poprzednich ocen, więc patrzą świeżym okiem.
5. Agent przyjmuje albo odrzuca każdą uwagę i podaje powód.
6. Przyjęte uwagi są wejściem do następnej tury.

Co dostajesz w folderze `.iterations/` w projekcie:

| Plik | Co zawiera |
|---|---|
| `personas.md` | persony dobrane do strony |
| `round-0/` | zrzuty ekranu przed zmianami |
| `round-N/analysis.md` | co zmienić w tej turze i dlaczego |
| `round-N/*.png` | zrzuty po zmianach, na komputer i na telefon |
| `round-N/review-<persona>.md` | uwagi recenzenta |
| `round-N/decisions.md` | uwagi przyjęte i odrzucone, z powodem |
| `summary.md` | przed i po, zmiany w każdej turze, odrzucone uwagi i pytania |

Skill nie wymyśla treści (opinii klientów, logotypów, liczb) i niczego nie wypycha. W repo git folder `.iterations/` trafia do `.git/info/exclude`, więc nie wchodzi do commitów.

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

**[iterate-with-review](./skills/web/iterate-with-review/SKILL.md)** (web) improves a website in autonomous rounds. Each round the agent analyzes the page, makes changes and takes desktop and mobile screenshots; then reviewer subagents playing personas fitted to the site (the audience it speaks to and someone who influences the decision, e.g. an investor) judge the screenshots with fresh eyes, without seeing the plan, the code or earlier reviews. The agent accepts or rejects every note with a reason, and the accepted notes drive the next round. Everything lands in `.iterations/` with a before/after summary; in a git repo each round is a plain commit, never pushed. Needs the site's source code; the bundled screenshot script needs Python 3.9+ and Playwright (`pip install playwright`, `python -m playwright install chromium`).
