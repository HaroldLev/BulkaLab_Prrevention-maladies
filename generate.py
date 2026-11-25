#!/usr/bin/env python3
"""Générateur vidéo « Maladies en Short » — frames + audio → pour ffmpeg.
Usage : python generate.py --storyboard storyboards/rhume-short.json --out out
Sortie : out/frames/frame_00000.jpg ... + out/audio.wav (durée exacte de la vidéo)
Dépendances : Pillow uniquement (pip install pillow).
"""
import argparse, base64, json, math, os, wave
from io import BytesIO
from PIL import Image, ImageDraw

SR = 44100

# ---------------- utilitaires ----------------
def hex_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def load_b64_split(prefix):
    """Reconstitue un binaire découpé en `.partNNN.b64` (< 64 Ko par morceau,
    limite des push de fichiers via l'API GitHub)."""
    import glob as _glob
    parts = sorted(_glob.glob(prefix + '.part*.b64'))
    if not parts:
        raise FileNotFoundError('aucune partie pour ' + prefix)
    chunks = []
    for p in parts:
        with open(p) as f:
            chunks.append(f.read().strip())
    return base64.b64decode(''.join(chunks))

def load_image_b64(prefix):
    return Image.open(BytesIO(load_b64_split(prefix))).convert('RGB')

def load_font_b64_cache(prefix):
    return load_b64_split(prefix)

def ease_out(p):
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3

# ---------------- rendu des frames ----------------
class FrameRenderer:
    def __init__(self, sb, outdir, fps=30, W=1080, H=1920):
        self.sb = sb
        self.outdir = outdir
        self.fps = fps
        self.W, self.H = W, H
        self.scenes = sb['scenes']
        self.starts = []
        acc = 0
        for s in self.scenes:
            self.starts.append(acc)
            acc += s['dur']
        self.total = acc
        # assets
        self.imgs = {k: load_image_b64(os.path.join('assets', 'images', k + '.jpg'))
                     for k in {sc['img'] for sc in self.scenes}}
        fontdir = os.path.join('assets', 'fonts')
        bold_bytes = load_font_b64_cache(os.path.join(fontdir, 'Archivo-Bold.ttf'))
        reg_bytes = load_font_b64_cache(os.path.join(fontdir, 'Archivo-Regular.ttf'))
        from PIL import ImageFont
        self.f_title = ImageFont.truetype(BytesIO(bold_bytes), 74)
        self.f_cap = ImageFont.truetype(BytesIO(reg_bytes), 47)
        self.f_capbold = ImageFont.truetype(BytesIO(bold_bytes), 52)
        self.f_disc = ImageFont.truetype(BytesIO(reg_bytes), 23)
        self.font_cache_bytes = (bold_bytes, reg_bytes)
        # dégradé permanent (comme le canvas : 0.62 -> 0.05 -> 0.12 -> 0.88)
        self.overlay = self._build_overlay()
        # pré-calcul des textes par scène
        self._pre = [self._precompute_scene(sc) for sc in self.scenes]
        os.makedirs(os.path.join(outdir, 'frames'), exist_ok=True)

    def _build_overlay(self):
        W, H = self.W, self.H
        ov = Image.new('L', (1, H))
        px = ov.load()
        for y in range(H):
            u = y / (H - 1)
            if u < 0.34:
                a = 158 * (1 - u / 0.34) + 13 * (u / 0.34)
            elif u < 0.58:
                a = 13 + (31 - 13) * ((u - 0.34) / 0.24)
            else:
                a = 31 + (224 - 31) * ((u - 0.58) / 0.42)
            px[0, y] = int(a)
        ov = ov.resize((W, H))
        black = Image.new('RGB', (W, H), (5, 5, 20))
        black.putalpha(ov)
        return black

    def _wrap(self, draw, text, font, maxw):
        words = text.split()
        lines, cur = [], ''
        for w_ in words:
            cand = (cur + ' ' + w_).strip()
            if cur and draw.textlength(cand, font=font) > maxw:
                lines.append(cur)
                cur = w_
            else:
                cur = cand
        if cur:
            lines.append(cur)
        return lines

    def _precompute_scene(self, sc):
        probe = ImageDraw.Draw(Image.new('RGB', (self.W, self.H)))
        acc = sc['accent']
        title_lines = self._wrap(probe, sc['title'].upper(), self.f_title, self.W - 100)
        caps = []
        inner_w = self.W - 2 * 54 - 2 * 26
        for ln in sc['lines']:
            font = self.f_capbold if ln.get('w') else self.f_cap
            tl = self._wrap(probe, ln['text'], font, inner_w)
            lh = max(font.getbbox('Ap')[3], font.getbbox('Hp')[3]) + 8
            box_h = 26 + len(tl) * lh
            caps.append({'t': ln['t'], 'w': ln.get('w', False), 'lines': tl,
                         'lh': lh, 'box_h': box_h, 'font': font})
        return {'title_lines': title_lines, 'caps': caps, 'accent': acc,
                'title_h': 26 + len(title_lines) * 78}

    def _scene_bg(self, idx, kb):
        sc = self.scenes[idx]
        img = self.imgs[sc['img']]
        s = 1.09 - 0.09 * min(max(kb, 0.0), 1.0)
        cover = max(self.W / img.width, self.H / img.height) * s
        nw, nh = int(img.width * cover + 0.5), int(img.height * cover + 0.5)
        im = img.resize((nw, nh), Image.BILINEAR)
        left, top = (nw - self.W) // 2, (nh - self.H) // 2
        return im.crop((left, top, left + self.W, top + self.H))

    def _slide_text(self, base, lines, font, x, y, fill, line_h):
        d = ImageDraw.Draw(base)
        for i, tline in enumerate(lines):
            d.text((x, y + i * line_h), tline, font=font, fill=fill)

    def render_frame(self, i):
        t = i / self.fps
        W, H = self.W, self.H
        idx = len(self.scenes) - 1
        for k in range(len(self.scenes)):
            if t < self.starts[k] + self.scenes[k]['dur']:
                idx = k
                break
        sc = self.scenes[idx]
        local = max(0.0, min(t - self.starts[idx], sc['dur']))
        pre = self._pre[idx]
        base = self._scene_bg(idx, local / sc['dur']).convert('RGBA')
        # crossfade depuis la scène précédente (0.55 s)
        if idx > 0 and local < 0.55:
            prev = self._scene_bg(idx - 1, 1.0).convert('RGBA')
            alpha = int(255 * (1 - local / 0.55))
            mask = Image.new('L', (W, H), alpha)
            base = Image.composite(base, prev, mask)
        base = Image.alpha_composite(base, self.overlay)

        d = ImageDraw.Draw(base)
        acc = hex_rgb(pre['accent'])
        # titre (slide up + fade)
        f_t = ease_out(local / 0.35)
        y_t = H - 210 - pre['title_h'] - 16 - sum(c['box_h'] + 20 for c in pre['caps']) + (1 - f_t) * 36
        d = ImageDraw.Draw(base)
        for li, tline in enumerate(pre['title_lines']):
            yy = y_t + li * 78 + (1 - f_t) * 36
            alpha = int(255 * f_t)
            col = acc + (alpha,)
            sh = (0, 0, 0, int(alpha * 0.85))
            d.text((54, yy + 5), tline, font=self.f_title, fill=sh)
            d.text((54, yy), tline, font=self.f_title, fill=col)
        # boîtes de sous-titres empilées vers le haut depuis le bas
        visible = [c for c in pre['caps'] if local >= c['t']]
        total_h = sum(c['box_h'] + 20 for c in visible)
        bottom = H - 210
        top_stack = bottom - total_h
        y = top_stack
        for c in visible:
            age = local - c['t']
            fp = ease_out(age / 0.35)
            dy = (1 - fp) * 44
            alpha = int(255 * fp)
            x0 = 54
            y0 = y + dy
            x1 = W - 54
            y1 = y0 + c['box_h']
            d.rounded_rectangle((x0, y0, x1, y1), radius=26,
                                 fill=(8, 8, 26, int(158 * fp)),
                                 outline=None)
            d.rectangle((x0, y0, x0 + 7, y1), fill=acc + (alpha,))
            inner_x = x0 + 26
            for li, tline in enumerate(c['lines']):
                d.text((inner_x, y0 + 13 + li * c['lh'] + 2), tline,
                       font=c['font'], fill=(255, 255, 255, alpha))
            y = y + c['box_h'] + 20
        # mention permanente
        dm = "Vidéo informative · ne remplace pas un avis médical"
        wd = d.textlength(dm, font=self.f_disc)
        d.text(((W - wd) / 2, H - 32), dm, font=self.f_disc, fill=(255, 255, 255, 110))
        return base.convert('RGB')

    def run(self, only_times=None):
        n = int(round(self.total * self.fps))
        if only_times is None:
            rng = range(n)
        else:
            rng = only_times
        for i in rng:
            im = self.render_frame(i)
            im.save(os.path.join(self.outdir, 'frames', 'frame_%05d.jpg' % i),
                    quality=88, optimize=True)

# ---------------- audio (miroir du moteur Web Audio lo-fi) ----------------
def synth_audio(total, out_path, music=None):
    # Profil musical lu depuis le storyboard : {'name': ..., 'bpm': ...}
    # (règle « Musique & trends » : tempo accrocheur, collé aux trends du moment).
    N = int(round(total * SR))
    out = [0.0] * N
    NOTE_N = 4096

    def sine_table(f, kind='sine'):
        n = max(2, int(round(SR / f)))
        if kind == 'sine':
            return [math.sin(2 * math.pi * (j / n)) for j in range(n)]
        # triangle
        return [8 / math.pi * math.pi / 2 * 0 + (2 / math.pi) * math.asin(math.sin(2 * math.pi * j / n)) for j in range(n)]

    music = music or {}
    BPM = float(music.get('bpm', 100))
    BEAT = 60.0 / BPM
    BAR = 4 * BEAT
    bars = int(total / BAR) + 2   # couvre toute la durée, marge de 2 mesures
    t0 = 0.08

    def env_exp(n, a, peak, end):
        e = []
        for j in range(n):
            u = j / n
            if u < a:
                e.append(peak * (j / max(1, int(n * a))))
            else:
                e.append(peak * math.exp(-6.9 * (u - a) / max(1e-9, 1 - a)))
        # normaliser la fin proche de 0
        e[-1] = e[-1] * 0.001 / max(1e-9, e[-1])
        return e

    bar_n = int(BAR * SR)
    prog = [
        {'pad': [(220.0, 1.0), (261.63, 1.0), (329.63, 1.0), (493.88, 1.0), (523.25, 1.0)], 'bass': 110.0,
         'mel': [440.0, 523.25, 587.33, 659.26]},
        {'pad': [(174.61, 1.0), (261.63, 1.0), (329.63, 1.0), (440.0, 1.0), (587.33, 1.0)], 'bass': 87.31,
         'mel': [349.23, 440.0, 523.25, 659.26]},
        {'pad': [(196.0, 1.0), (261.63, 1.0), (329.63, 1.0), (392.0, 1.0), (587.33, 1.0)], 'bass': 130.81,
         'mel': [392.0, 523.25, 587.33, 659.26]},
        {'pad': [(196.0, 1.0), (246.94, 1.0), (293.66, 1.0), (440.0, 1.0), (493.88, 1.0)], 'bass': 98.0,
         'mel': [392.0, 440.0, 493.88, 587.33]},
    ]
    arp = [0, 2, 1, 3, 2, -1, 1, 2]

    # --- précalcul : forme d'onde d'une mesure pour b % 16 = q ---
    barwavs = {}
    for q in range(16):
        bw = [0.0] * bar_n
        p = prog[q % 4]
        # pad : 5 notes, sine + 0.14 triangle à 2f, enveloppe exp
        for (f, gmul) in p['pad']:
            ts = sine_table(f, 'sine')
            tt = sine_table(f * 2.001, 'tri')
            nt, ntt = len(ts), len(tt)
            for j in range(bar_n):
                u = j / bar_n
                if u < 0.30:
                    g = 0.038
                else:
                    g = 0.038 * math.exp(-6.9 * (u - 0.30) / 0.72)
                s = ts[j % nt]
                s2 = tt[j % ntt]
                bw[j] += gmul * g * (s + 0.14 * s2)
        # bass : 2 coups
        for off in (0, BEAT * 2.5):
            i0 = int(off * SR)
            n_b = int(BEAT * 1.4 * SR)
            tb = sine_table(p['bass'], 'sine')
            for j in range(min(n_b, bar_n - i0)):
                u = j / n_b
                g = 0.13 * (u / 0.05 if u < 0.05 else math.exp(-6.9 * (u - 0.05) / 0.95))
                bw[i0 + j] += g * tb[j % len(tb)]
        # kick : 2 coups
        for off in (0, BEAT * 2):
            i0 = int(off * SR)
            n_k = int(0.30 * SR)
            fstart, fend = 130.0, 44.0
            ph = 0.0
            for j in range(min(n_k, bar_n - i0)):
                u = j / n_k
                f = fstart * math.exp(math.log(fend / fstart) * min(1.0, j / (0.11 * SR)))
                ph += 2 * math.pi * f / SR
                g = 0.34 * (u / 0.012 if u < 0.012 else math.exp(-6.9 * (u - 0.012) / 0.988))
                bw[i0 + j] += g * math.sin(ph)
        # hat : 4 contretemps (sujet calme) ou 8 croches par mesure
        # (tempo trend > 84 BPM, miroir exact du template canvas)
        hat_n = 8 if BPM > 84 else 4
        hat_div = 2.0 if BPM > 84 else 1.0
        for h in range(hat_n):
            i0 = int(BEAT * (h + 0.5) / hat_div * SR)
            n_h = int(0.06 * SR)
            rnd = 12345 + q * 77 + h
            for j in range(min(n_h, bar_n - i0)):
                r = ((rnd * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff
                g = 0.05 * math.exp(-6.9 * j / n_h)
                bw[i0 + j] += g * (r * 2 - 1)
        # marimba : 8 double-croches
        for e in range(8):
            m = arp[(e + q) % 8]
            if m < 0:
                continue
            f = p['mel'][m] * (0.5 if (e + q) % 16 >= 8 else 1.0)
            i0 = int((e * BEAT * 0.5 + 0.02) * SR)
            n_m = int(0.55 * SR)
            tm = sine_table(f, 'tri')
            tm2 = [0.5 * math.sin(2 * math.pi * f * j / SR) for j in range(min(n_m, 200))]
            for j in range(min(n_m, bar_n - i0)):
                u = j / n_m
                g = 0.075 * (u / 0.015 if u < 0.015 else math.exp(-6.9 * (u - 0.015) / 0.985))
                s = tm[j % len(tm)]
                if j < 200:
                    s += 0.25 * tm2[j]
                bw[i0 + j] += g * s
        barwavs[q] = bw

    master = 0.55
    a_lp = 1 - math.exp(-2 * math.pi * 2600 / SR)
    y = 0.0
    for b in range(bars):
        i0 = int((t0 + b * BAR) * SR)
        bw = barwavs[b % 16]
        lim = min(bar_n, N - i0)
        if lim <= 0:
            break
        for j in range(lim):
            y += a_lp * (bw[j] - y)
            out[i0 + j] += master * y
    # fade-out final 0.5 s
    fade_n = int(0.5 * SR)
    for j in range(fade_n):
        idx = N - 1 - j
        if idx < 0:
            break
        out[idx] *= j / fade_n
    # douceur sur les extrêmes
    out = [max(-0.95, min(0.95, v)) for v in out]
    # écriture WAV
    with wave.open(out_path, 'w') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = bytearray()
        for v in out:
            frames += int(max(-1.0, min(1.0, v)) * 32767).to_bytes(2, 'little', signed=True)
        w.writeframes(bytes(frames))

# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--storyboard', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--smoke', action='store_true',
                    help='rend quelques frames de contrôle + audio court, sans tout générer')
    args = ap.parse_args()
    with open(args.storyboard) as f:
        sb = json.load(f)
    r = FrameRenderer(sb, args.out, fps=args.fps)
    if args.smoke:
        times = [0, 15, 45, 75, 105, 135, 165, 195]
        # index de frames proches de chaque frontière de scène
        idx = []
        for k, s in enumerate(r.scenes):
            idx.append(int(r.starts[k] * r.fps) + 1)
            idx.append(int((r.starts[k] + min(2, s['dur'])) * r.fps))
        idx.append(int(r.total * r.fps) - 1)
        r.run(only_times=sorted(set(idx)))
        synth_audio(min(6.0, r.total), os.path.join(args.out, 'audio_smoke.wav'), sb.get('music'))
    else:
        r.run()
        synth_audio(r.total, os.path.join(args.out, 'audio.wav'), sb.get('music'))
        # images allégées pour le canvas React (limite 1 024 000 octets, base64 inclus)
        cdir = os.path.join(args.out, 'canvas-images')
        os.makedirs(cdir, exist_ok=True)
        for k, img in r.imgs.items():
            w = 560
            h = int(img.height * w / img.width)
            img.resize((w, h), Image.LANCZOS).save(
                os.path.join(cdir, k + '_small.jpg'),
                quality=42, optimize=True)
    with open(os.path.join(args.out, 'meta.json'), 'w') as f:
        json.dump({'slug': sb['slug'], 'total': r.total, 'fps': args.fps}, f)

if __name__ == '__main__':
    main()
