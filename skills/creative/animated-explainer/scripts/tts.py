#!/usr/bin/env python3
"""Narrate an explainer: one MP3 per scene from <dir>/narration.json with Gemini TTS.

  GEMINI_API_KEY=... python3 tts.py <explainer dir> [scene numbers...]

narration.json: {"lang": "ru", "voice": "Aoede", "style": "<how to read it>", "lines": ["scene 1 text", ...]}
Writes <dir>/audio/n<i>.mp3 and <dir>/audio/manifest.json ({"durations": [...], "hashes": [...]}).
Unchanged lines (same text+voice+style hash) are skipped, so edit one line and re-run cheaply.
Needs ffmpeg/ffprobe on PATH. Stdlib only.
"""
import base64, hashlib, json, os, subprocess, sys, time, urllib.error, urllib.request

MODEL = os.environ.get("EXPLAINER_TTS_MODEL", "gemini-2.5-flash-preview-tts")
DEFAULT_STYLE = ("Read this aloud warmly and playfully, like a kind storyteller talking to a six-year-old. "
                 "Speak slowly and expressively with small pauses. Read only the text, in its own language:")


def say(key, text, voice, style):
    body = {"contents": [{"parts": [{"text": style + "\n\n" + text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=240))
            return base64.b64decode(r["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError) as e:
            print(f"  retry {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(4 * (attempt + 1))
    raise SystemExit("TTS failed after retries")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    d = os.path.abspath(sys.argv[1])
    only = {int(a) for a in sys.argv[2:]}
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise SystemExit("Set GEMINI_API_KEY")
    spec = json.load(open(os.path.join(d, "narration.json")))
    voice, style = spec.get("voice", "Aoede"), spec.get("style", DEFAULT_STYLE)
    out = os.path.join(d, "audio")
    os.makedirs(out, exist_ok=True)
    mpath = os.path.join(out, "manifest.json")
    man = json.load(open(mpath)) if os.path.exists(mpath) else {}
    n = len(spec["lines"])
    hashes = (man.get("hashes") or []) + [None] * n
    durs = (man.get("durations") or []) + [0] * n
    hashes, durs = hashes[:n], durs[:n]
    for i, text in enumerate(spec["lines"]):
        h = hashlib.sha256(f"{MODEL}|{voice}|{style}|{text}".encode()).hexdigest()[:16]
        mp3 = os.path.join(out, f"n{i}.mp3")
        if (only and i not in only) or (not only and hashes[i] == h and os.path.exists(mp3)):
            continue
        pcm = say(key, text, voice, style)
        raw = mp3 + ".pcm"
        open(raw, "wb").write(pcm)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", raw,
                        "-af", "silenceremove=start_periods=1:start_threshold=-50dB",
                        "-c:a", "libmp3lame", "-b:a", "48k", mp3], check=True)
        os.remove(raw)
        durs[i] = round(float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                                         "-of", "csv=p=0", mp3]).decode().strip()), 2)
        hashes[i] = h
        print(f"scene {i + 1}: {durs[i]} s")
    json.dump({"durations": durs, "hashes": hashes, "voice": voice, "model": MODEL}, open(mpath, "w"), indent=1)


if __name__ == "__main__":
    main()
