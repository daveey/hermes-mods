#!/usr/bin/env python3
"""Build one self-contained HTML explainer from a project dir.

  python3 build.py <explainer dir> [--out FILE] [--no-audio]

Reads <dir>/explainer.js (defines `const EXPLAINER = {...}`), optional <dir>/explainer.css,
<dir>/narration.json and <dir>/audio/ (from tts.py). Inlines everything (audio as base64 data URIs) into
templates/shell.html, so the output works offline, from file://, in a chat preview or on any static host.
Default output: <dir>/dist/<dir name>.html. Prints the path and size.
"""
import argparse, base64, html, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..", "templates", "shell.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--out")
    ap.add_argument("--no-audio", action="store_true")
    a = ap.parse_args()
    d = os.path.abspath(a.dir)
    js = open(os.path.join(d, "explainer.js")).read()
    css_path = os.path.join(d, "explainer.css")
    css = open(css_path).read() if os.path.exists(css_path) else ""
    narr_path = os.path.join(d, "narration.json")
    spec = json.load(open(narr_path)) if os.path.exists(narr_path) else {"lines": []}
    lines = spec.get("lines", [])
    audio, durs = [], []
    man_path = os.path.join(d, "audio", "manifest.json")
    if not a.no_audio and lines and os.path.exists(man_path):
        man = json.load(open(man_path))
        files = [os.path.join(d, "audio", f"n{i}.mp3") for i in range(len(lines))]
        if all(os.path.exists(f) for f in files):
            audio = ["data:audio/mpeg;base64," + base64.b64encode(open(f, "rb").read()).decode() for f in files]
            durs = man.get("durations", [])
        else:
            print("warning: audio incomplete, building silent (run tts.py)")
    m = re.search(r"title:\s*'([^']+)'", js)
    title = m.group(1) if m else os.path.basename(d)
    out = open(SHELL).read()
    for k, v in {"__TITLE__": html.escape(title), "__UI_LANG__": spec.get("ui_lang", "en"), "__CSS__": css,
                 "__DEFS__": "", "__EXPLAINER__": js.replace("</script", "<\\/script"),
                 "__NARR__": json.dumps(lines, ensure_ascii=False).replace("</", "<\\/"),
                 "__AUDIO__": json.dumps(audio), "__DUR__": json.dumps(durs)}.items():
        out = out.replace(k, v)
    dest = a.out or os.path.join(d, "dist", os.path.basename(d.rstrip("/")) + ".html")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "w").write(out)
    print(dest, f"{len(out) // 1024} KB", "with audio" if audio else "silent")


if __name__ == "__main__":
    main()
