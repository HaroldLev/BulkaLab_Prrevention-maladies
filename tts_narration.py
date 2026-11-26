#!/usr/bin/env python3
"""Piste voix narrée (Piper TTS local, open source et gratuit) pour un storyboard.

Usage : python tts_narration.py --storyboard storyboards/x.json --out out/x
Sortie : out/x/narration.wav — durée exacte de la vidéo : chaque texte "nar"
est synthétisé au début de sa scène, puis complété par du silence jusqu'à la
fin de la scène (scène suivante jamais décalée).

Le storyboard doit contenir "voice": "<id de voix piper>" (ex. fr_FR-siwis-medium).
Si "voice" est absent, le script ne fait rien (fallback musique seule).
"""
import argparse
import json
import os
import sys
import wave


def synth_scene(voice_id, text, out_wav):
    """Synthétise un texte avec la CLI piper (lecture depuis stdin)."""
    import subprocess
    p = subprocess.run(
        ["piper", "-m", voice_id, "-f", out_wav],
        input=(text.strip() + "\n").encode("utf-8"),
        capture_output=True,
    )
    ok = p.returncode == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 44
    if not ok:
        sys.stderr.write((p.stderr or b"?").decode("utf-8", "replace")[:500] + "\n")
    return ok


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

    os.makedirs(args.out, exist_ok=True)

    # 1. synthèse de chaque scène
    seg_map = {}
    for j, sc in enumerate(sb.get("scenes", [])):
        txt = (sc.get("nar") or "").strip()
        if not txt:
            continue
        tmp = os.path.join(args.out, "seg_%02d.wav" % j)
        if synth_scene(voice_id, txt, tmp):
            seg_map[j] = tmp
        else:
            print("échec synthèse scène %d (silence à la place)" % j)
    if not seg_map:
        print("aucune narration générée pour", sb.get("slug"))
        return 1

    # 2. assemblage au timing des scènes (format piper : mono, 16 bits)
    with wave.open(seg_map[min(seg_map)], "rb") as w0:
        ch, sw, sr = w0.getnchannels(), w0.getsampwidth(), w0.getframerate()
    out_path = os.path.join(args.out, "narration.wav")
    with wave.open(out_path, "wb") as wo:
        wo.setnchannels(ch)
        wo.setsampwidth(sw)
        wo.setframerate(sr)
        for j, sc in enumerate(sb.get("scenes", [])):
            target = int(round(float(sc["dur"]) * sr))  # frames pour la durée de la scène
            written = 0
            if j in seg_map:
                with wave.open(seg_map[j], "rb") as w:
                    jch, jsw = w.getnchannels(), w.getsampwidth()
                    raw = w.readframes(target)
                    n = len(raw) // (jch * jsw)
                    # piper produit du mono 16 bits : conversion simple si besoin
                    if jch == ch and jsw == sw:
                        wo.writeframes(raw)
                        written = min(target, n)
                    else:
                        # rééchantillonnage naïf par troncature de canaux (rare)
                        step = jch * jsw
                        trimmed = b"".join(
                            raw[i * step: i * step + sw] for i in range(min(n, target))
                        )
                        wo.writeframes(trimmed)
                        written = min(target, len(trimmed) // (ch * sw))
            if written < target:
                wo.writeframes(b"\x00" * ((target - written) * ch * sw))
            # nettoyage du segment
            if j in seg_map:
                try:
                    os.remove(seg_map[j])
                except OSError:
                    pass

    total = sum(float(sc["dur"]) for sc in sb.get("scenes", []))
    print("narration.wav : %.1f s, %d octets, voix %s" % (total, os.path.getsize(out_path), voice_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
