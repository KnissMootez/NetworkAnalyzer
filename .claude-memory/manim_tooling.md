---
name: manim-tooling
description: How to run Manim for the NetworkAnalyzer explainer scenes (env + the broken-launcher gotcha)
metadata: 
  node_type: memory
  type: reference
  originSessionId: b7a3ae0b-3111-423b-884d-b509095e7ac6
---

**MAIN ASSET — `archive/networkanalyzer_presentation.py`**: a polished, complete Manim presentation the user made that "worked wonders" and is still valid/current ("still the same"). 1249 lines, 6 scenes: `S1_Title`, `S2_Architecture`, `S3_Dashboard`, `S4_AgentLoop`, `S5_Charts`, `S6_SelfCorrection`. Full light palette (bg #f0f4ff, indigo #6366f1 / blue accents) + a dark palette. This is the GO-TO for Manim — reuse/extend it, don't rebuild from scratch. It lives in archive/ (gitignored) but is intact. Render a scene: `python -m manim -qh archive/networkanalyzer_presentation.py S2_Architecture`.

`manim_scenes.py` (project root) is a NEWER minimal starter I wrote (one `Architecture` scene, Huawei-red palette) — redundant next to the archived presentation; prefer the archived one unless the user wants the red theme.

**Manim WORKS on this machine — the old "needs Python 3.12" belief is OBSOLETE.** Manim Community v0.20.1 is installed in `networkanalyzer-env` and renders fine on **Python 3.14.3**.

**GOTCHA:** the bare `manim` command is broken — its `manim.exe` launcher has a hardcoded shebang to the deleted old env `smartcare-env\Scripts\python.exe` (leftover from the project rename). Always invoke as a MODULE instead:
- `python -m manim -pql manim_scenes.py Architecture`  (preview, low quality)
- `python -m manim -qh manim_scenes.py Architecture`   (high quality for the deck)

Renders land in `media/videos/manim_scenes/<quality>/<Scene>.mp4` (media/ is gitignored). Do renders headless with `-ql --disable_caching` (drop `-p` so no preview window). Planned scenes: 1 Architecture (done), 2 Agentic loop, 3 Closed loop. See [[NetworkAnalyzer Project Overview]].
