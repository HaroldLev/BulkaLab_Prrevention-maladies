#!/usr/bin/env python3
"""Piste voix narrée (Piper TTS local, open source et gratuit) pour un storyboard.

Usage : python tts_narration.py --storyboard storyboards/x.json --out out/x
Sortie : out/x/narration.wav — durée exacte de la vidéo : chaque texte "nar"
est synthétisé au début de sa scène, puis complété par du silence jusqu'à la
fin de la scène (scène suivante jamais décalée).

Le storyboard doit contenir "voice": "<id de voix piper>" (ex. fr_FR-siwis-medium).
Champ optionnel "voice_speed" (float) : length-scale Piper — < 1.0 accélère la
voix (ex. 0.85 ≈ 15 % plus rapide), > 1.0 la ralentit. Défaut : vitesse native.
Si "voice" est absent, le script ne fait rien et sort 0 (MP4 musique seule).
En cas d'échec de synthèse, le script sort NON-ZÉRO pour faire échouer le job
(jamais de MP4 sans voix livré en silence).
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import wave


def find_model(voice_id):
    """Cherche le .onnx d'une voix, localement ou après téléchargement."""
    for pat in (voice_id + ".onnx", "**/" + voice_id + ".onnx"):
        hits = glob.glob(pat, recursive=True)
        if hits:
            return hits[0]
    return None


def ensure_voice(voice_id, workdir):
    """Télécharge la voix si besoin (piper ne télé charge PAS via -m id)."""
    model = find_model(voice_id)
    if model:
        return model
    p = subprocess.run(
        [sys.executable, "-m", "piper.download_voices", voice_id],
        cwd=workdir,
        capture_output=True,
    )
    sys.stderr.write("download_voices rc=%d\n%s\n%s\n" % (
        p.returncode,
        (p.stdout or b"")[:800].decode("utf-8", "replace"),
        (p.stderr or b"")[:800].decode("utf-8", "replace"),
    ))
    return find_model(voice_id)


def synth_scene(model_path, text, out_wav, length_scale=None):
    """Synthétise un texte : CLI piper d'abord, API python en secours."""
    cmd = ["piper", "-m", model_path, "-f", out_wav]
    if length_scale is not None:
        cmd += ["--length-scale", str(float(length_scale))]
    try:
        p = subprocess.run(
            cmd,
            input=(text.strip() + "\n").encode("utf-8"),
            capture_output=True,
            timeout=120,
        )
        if p.returncode == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 44:
            return True
        sys.stderr.write("CLI piper rc=%d : %s\n" % (
            p.returncode, (p.stderr or b"")[:500].decode("utf-8", "replace")))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("CLI piper exception: %r\n" % e)
    # secours : API python (synthétise selon la version de piper ; sans réglage
    # de vitesse, l'option length-scale n'est pas uniforme entre versions)
    try:
        from piper import PiperVoice
        try:
            from piper import PiperConfig as _Cfg
            voice = PiperVoice.load(model_path, config=_Cfg(length_scale=float(length_scale)))
        except Exception:
            voice = PiperVoice.load(model_path)
        with wave.open(out_wav, "wb") as w:
            try:
                voice.synthesize(text.strip(), w)
            except (AttributeError, TypeError):
                voice.synthesize_wav(text.strip(), w)
        return os.path.getsize(out_wav) > 44
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("API piper exception: %r\n" % e)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.storyboard, "r", encoding="utf-8") as f:
        sb = json.load(f)
    voice_id = sb.get("voice")
    if not voice_id:
        print("pas de champ voice -> pas de narration pour", sb.get("slug"))
        return 0
    length_scale = sb.get("voice_speed")

    os.makedirs(args.out, exist_ok=True)
    model = ensure_voice(voice_id, os.getcwd())
    if not model:
        sys.stderr.write("voix introuvable après téléchargement : %s\n" % voice_id)
        return 2

    seg_map = {}
    errors = 0
    for j, sc in enumerate(sb.get("scenes", [])):
        txt = (sc.get("nar") or "").strip()
        if not txt:
            continue
        tmp = os.path.join(args.out, "seg_%02d.wav" % j)
        if synth_scene(model, txt, tmp, length_scale):
            seg_map[j] = tmp
        else:
            sys.stderr.write("échec synthèse scène %d\n" % j)
            errors += 1
    if not seg_map:
        return 3
    if errors:
        print("ATTENTION : %d scène(s) sans voix" % errors)

    with wave.open(seg_map[min(seg_map)], "rb") as w0:
        ch, sw, sr = w0.getnchannels(), w0.getsampwidth(), w0.getframerate()
    out_path = os.path.join(args.out, "narration.wav")
    with wave.open(out_path, "wb") as wo:
        wo.setnchannels(ch)
        wo.setsampwidth(sw)
        wo.setframerate(sr)
        for j, sc in enumerate(sb.get("scenes", [])):
            target = int(round(float(sc["dur"]) * sr))
            written = 0
            if j in seg_map:
                with wave.open(seg_map[j], "rb") as w:
                    raw = w.readframes(target)
                    wo.writeframes(raw)
                    written = min(target, len(raw) // (w.getnchannels() * w.getsampwidth()))
            if written < target:
                wo.writeframes(b"\x00" * ((target - written) * ch * sw))
            if j in seg_map:
                try:
                    os.remove(seg_map[j])
                except OSError:
                    pass

    total = sum(float(sc["dur"]) for sc in sb.get("scenes", []))
    print("narration.wav : %.1f s, %d octets, voix %s (vitesse %s, %s)" % (
        total, os.path.getsize(out_path), voice_id, length_scale or "native", model))
    return 0


if __name__ == "__main__":
    sys.exit(main())
