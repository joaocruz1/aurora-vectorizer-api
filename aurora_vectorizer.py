#!/usr/bin/env python3
"""
Motor de vetorização HQ para logos (gradiente + multi-cor) -> linha p/ laser.

Função pública:  vectorize_to_svg(image_bytes, opts) -> str (SVG)

Técnicas:
  1. SÓLIDOS via potrace (contornos suaves, cantos nítidos).
  2. TEXTO multi-cor separado por MATIZ (auto-detecta as cores) -> letras de
     cores diferentes que se encostam não fundem.
  3. DOBRAS internas do emblema (gradiente): bordas multi-canal saturadas ->
     esqueleto -> spline (scipy) -> extensão das pontas até a silhueta.

Nada é hardcoded pro Aurora: o corte emblema/texto é por MAIOR VÃO horizontal,
e as cores do texto são detectadas por picos no histograma de matiz.
"""
from __future__ import annotations
import subprocess, tempfile, os, re
from dataclasses import dataclass
import numpy as np
import cv2
from skimage.morphology import skeletonize
from skan import Skeleton, summarize
from scipy.interpolate import splprep, splev


@dataclass
class VectorizeOptions:
    upscale: int = 2
    saturation: float = 1.8
    stroke: str = "#111111"
    stroke_width: float = 2.4
    # sólidos
    turdsize_emblem: int = 3
    turdsize_text: int = 8
    text_min_area: int = 120          # área mínima (px, escalada por upscale) p/ tirar respingos
    # separação de cor no texto
    color_separate_text: bool = True
    hue_merge: int = 6                # picos de matiz a < isso são fundidos (OpenCV 0-179)
    max_text_colors: int = 5
    # dobras internas
    capture_folds: bool = True
    fold_erode: int = 20              # afasta as dobras da borda externa
    fold_min_branch: int = 70
    fold_spline_smooth: float = 5.0
    fold_extend: int = 90             # alcance máx. da extensão das pontas
    fill_emblem_holes: bool = True
    # corte emblema/texto (None = automático por vão; -1 = sem texto separado)
    split_y: int | None = None


# ───────────────────────── helpers ─────────────────────────

def _potrace(mask255: np.ndarray, turd: int) -> list[str]:
    """Traça uma máscara (branco=tinta) e devolve os <path .../> (espaço potrace)."""
    # Limpar bordas: impede shapes de tocar a borda da imagem,
    # evitando o retângulo de fundo que potrace gera nesses casos.
    mask255[0, :] = 0; mask255[-1, :] = 0
    mask255[:, 0] = 0; mask255[:, -1] = 0
    with tempfile.TemporaryDirectory() as d:
        pbm, svg = os.path.join(d, "m.pbm"), os.path.join(d, "m.svg")
        cv2.imwrite(pbm, 255 - mask255)            # potrace traça preto
        subprocess.run(["potrace", pbm, "-s", "-o", svg, "--turdsize", str(turd),
                        "--alphamax", "1.0", "--opttolerance", "0.2", "--unit", "1"],
                       check=True)
        return re.findall(r'<path\b[^>]*\bd="[^"]+"\s*/>', open(svg).read())


def _clean(m: np.ndarray, min_area: int = 0) -> np.ndarray:
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    if min_area > 0:
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
        out = np.zeros_like(m)
        for i in range(1, n):
            if st[i, cv2.CC_STAT_AREA] >= min_area:
                out[lab == i] = 255
        m = out
    m = cv2.GaussianBlur(m, (0, 0), 0.8)
    return (m > 100).astype(np.uint8) * 255


def _auto_split(ink: np.ndarray) -> int | None:
    """Maior vão horizontal COM tinta dos dois lados = corte emblema/texto.
    Exigir conteúdo acima E abaixo evita pegar a margem vazia do rodapé."""
    rows = (ink > 0).sum(axis=1)
    h = len(rows)
    thr = max(2, rows.max() * 0.02)
    empty = rows < thr
    best_len, best_mid = 0, None
    y = 0
    while y < h:
        if empty[y]:
            s = y
            while y < h and empty[y]:
                y += 1
            e = y  # vão [s, e)
            ink_above = int((rows[:s] > thr).sum())
            ink_below = int((rows[e:] > thr).sum())
            if ink_above > h * 0.01 and ink_below > h * 0.01 and (e - s) > best_len:
                best_len, best_mid = (e - s), (s + e) // 2
        else:
            y += 1
    return best_mid if best_len >= h * 0.01 else None


def _hue_peaks(hue: np.ndarray, ink: np.ndarray, merge: int, cap: int) -> list[int]:
    """Picos do histograma de matiz dos pixels de tinta."""
    h = hue[ink]
    if h.size < 50:
        return []
    hist = cv2.GaussianBlur(np.bincount(h, minlength=180).astype(np.float32).reshape(-1, 1),
                            (0, 0), 1.5).ravel()
    mx = hist.max()
    peaks = [i for i in range(180)
             if hist[i] > mx * 0.12 and hist[i] >= hist[(i - 1) % 180] and hist[i] >= hist[(i + 1) % 180]]
    merged: list[int] = []
    for p in sorted(peaks, key=lambda i: -hist[i]):
        if all(min(abs(p - q), 180 - abs(p - q)) > merge for q in merged):
            merged.append(p)
        if len(merged) >= cap:
            break
    return sorted(merged)


def _extend_to_mask(P, mask, max_ext=90, step=2.0):
    """Estende as duas pontas pela tangente até encostar na silhueta."""
    P = [np.array(p, float) for p in P]
    if len(P) < 6:
        return [tuple(p) for p in P]
    h, w = mask.shape
    def grow(anchor, ref):
        d = anchor - ref; n = np.linalg.norm(d)
        if n < 1e-6:
            return []
        d /= n; pt = anchor.copy(); out = []
        for _ in range(int(max_ext / step)):
            pt = pt + d * step
            xi, yi = int(round(pt[0])), int(round(pt[1]))
            if xi < 0 or yi < 0 or xi >= w or yi >= h:
                break
            out.append((pt[0], pt[1]))
            if mask[yi, xi] == 0:
                break
        return out
    front = grow(P[0], P[min(8, len(P) - 1)])[::-1]
    back = grow(P[-1], P[max(len(P) - 9, 0)])
    return front + [tuple(p) for p in P] + back


def _catmull(P) -> str:
    n = len(P)
    d = "M %.2f %.2f " % P[0]
    for k in range(n - 1):
        p0, p1, p2, p3 = P[max(k-1,0)], P[k], P[k+1], P[min(k+2,n-1)]
        c1 = (p1[0]+(p2[0]-p0[0])/6, p1[1]+(p2[1]-p0[1])/6)
        c2 = (p2[0]-(p3[0]-p1[0])/6, p2[1]-(p3[1]-p1[1])/6)
        d += "C %.2f %.2f %.2f %.2f %.2f %.2f " % (c1[0],c1[1],c2[0],c2[1],p2[0],p2[1])
    return d


# ───────────────────────── pipeline ─────────────────────────

def vectorize_to_svg(image_bytes: bytes, opts: VectorizeOptions | None = None) -> str:
    o = opts or VectorizeOptions()
    arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("imagem inválida")
    UP = max(1, o.upscale)
    img = cv2.resize(arr, None, fx=UP, fy=UP, interpolation=cv2.INTER_LANCZOS4)
    H_, W_ = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    S, Hue = hsv[:, :, 1], hsv[:, :, 0]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = (S > 50) | (gray < 230)
    # Limpar margem de borda: artefatos de compressão (JPEG/AVIF/WebP) fazem
    # pixels de fundo "quase branco" (gray 220-250) disparar o threshold.
    # Sem essa limpeza o MORPH_CLOSE conecta esses pixels ao conteúdo,
    # transformando a máscara inteira num bloco sólido.
    margin = max(4, int(min(H_, W_) * 0.01))
    ink[:margin, :] = False; ink[-margin:, :] = False
    ink[:, :margin] = False; ink[:, -margin:] = False

    # corte emblema/texto
    if o.split_y is None:
        split = _auto_split(ink.astype(np.uint8) * 255)
    elif o.split_y < 0:
        split = None
    else:
        split = o.split_y * UP
    if split is None:
        split = H_  # tudo é "emblema"

    solid_paths: list[str] = []

    # ── EMBLEMA (acima do corte): silhueta sólida ──
    emb = (ink.astype(np.uint8) * 255).copy()
    emb[split:] = 0
    if emb.any():
        emb = cv2.morphologyEx(emb, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        # Usar máscara fechada (com holes preenchidos) apenas para fold detection,
        # preservando a separação de penas na silhueta (potrace).
        if o.fill_emblem_holes:
            ff = emb.copy(); mk = np.zeros((H_ + 2, W_ + 2), np.uint8)
            cv2.floodFill(ff, mk, (0, 0), 255)
            emb_mask = emb | cv2.bitwise_not(ff)
        else:
            emb_mask = emb.copy()
        solid_paths += _potrace(_clean(emb), o.turdsize_emblem)
    else:
        emb_mask = np.zeros((H_, W_), np.uint8)

    # ── TEXTO (abaixo do corte): separa por cor ──
    if split < H_:
        tband = np.zeros((H_, W_), bool); tband[split:] = True
        tink = ink & tband
        if o.color_separate_text:
            peaks = _hue_peaks(Hue, tink, o.hue_merge, o.max_text_colors)
        else:
            peaks = []
        if len(peaks) >= 2:
            for pk in peaks:
                dist = np.minimum(np.abs(Hue.astype(int) - pk), 180 - np.abs(Hue.astype(int) - pk))
                nearest = np.ones((H_, W_), bool)
                for other in peaks:
                    if other == pk:
                        continue
                    do = np.minimum(np.abs(Hue.astype(int) - other), 180 - np.abs(Hue.astype(int) - other))
                    nearest &= dist <= do
                layer = (tink & nearest).astype(np.uint8) * 255
                solid_paths += _potrace(_clean(layer, o.text_min_area * UP), o.turdsize_text)
        else:
            solid_paths += _potrace(_clean((tink.astype(np.uint8) * 255), o.text_min_area * UP),
                                    o.turdsize_text)

    # ── DOBRAS internas do emblema ──
    folds: list[str] = []
    if o.capture_folds and emb_mask.any():
        h = hsv.astype(np.float32); h[:, :, 1] = np.clip(h[:, :, 1] * o.saturation, 0, 255)
        boost = cv2.cvtColor(h.astype(np.uint8), cv2.COLOR_HSV2BGR)
        lab = cv2.cvtColor(boost, cv2.COLOR_BGR2LAB)
        fused = np.zeros((H_, W_), np.float32)
        for ch in [cv2.cvtColor(boost, cv2.COLOR_BGR2GRAY), boost[:, :, 0], boost[:, :, 1],
                   boost[:, :, 2], lab[:, :, 1], lab[:, :, 2]]:
            ch = cv2.normalize(ch, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            ch = cv2.bilateralFilter(ch, 9, 60, 60)
            fused = np.maximum(fused, cv2.Canny(ch, 40, 100).astype(np.float32))
        fmap = cv2.bitwise_and(fused.astype(np.uint8),
                               cv2.erode(emb_mask, np.ones((o.fold_erode * UP, o.fold_erode * UP), np.uint8)))
        fmap = cv2.dilate(fmap, np.ones((3, 3), np.uint8), 2)
        fmap = cv2.morphologyEx(fmap, cv2.MORPH_CLOSE, np.ones((9 * UP, 9 * UP), np.uint8))
        sk_img = skeletonize(fmap > 0)
        if sk_img.sum() > 10:
            sk = Skeleton(sk_img.astype(np.uint8)); summ = summarize(sk, separator="_")
            for i in range(sk.n_paths):
                if summ.iloc[i]["branch_distance"] < o.fold_min_branch * UP:
                    continue
                co = sk.path_coordinates(i)
                if len(co) < 10:
                    continue
                pts = np.array([(c, r) for r, c in co], float)
                keep = [0]
                for j in range(1, len(pts)):
                    if np.hypot(*(pts[j] - pts[keep[-1]])) > 4.0:
                        keep.append(j)
                pts = pts[keep]
                if len(pts) < 6:
                    continue
                try:
                    tck, _ = splprep([pts[:, 0], pts[:, 1]], s=len(pts) * o.fold_spline_smooth, k=3)
                    xs, ys = splev(np.linspace(0, 1, max(16, len(pts) // 5)), tck)
                except Exception:
                    xs, ys = pts[:, 0], pts[:, 1]
                P = _extend_to_mask(list(zip(xs, ys)), emb_mask, max_ext=o.fold_extend)
                # Re-sample: garantir espaçamento mínimo entre pontos para
                # eliminar micro-segmentos que criam aparência pontilhada.
                if len(P) > 2:
                    resampled = [P[0]]
                    for pt in P[1:]:
                        dx, dy = pt[0]-resampled[-1][0], pt[1]-resampled[-1][1]
                        if dx*dx + dy*dy >= 144:  # >= 12px
                            resampled.append(pt)
                    if len(resampled) > 1 and resampled[-1] != P[-1]:
                        resampled.append(P[-1])  # manter ponto final
                    P = resampled
                if len(P) >= 4:
                    folds.append(_catmull(P))

    # ── monta SVG (potrace compartilha o mesmo transform → um grupo só) ──
    g = (f'<g transform="translate(0.000000,{H_}.000000) scale(1.000000,-1.000000)" '
         f'fill="none" stroke="{o.stroke}" stroke-width="{o.stroke_width}" '
         f'stroke-linecap="round" stroke-linejoin="round">' + "".join(solid_paths) + "</g>")
    fg = (f'<g fill="none" stroke="{o.stroke}" stroke-width="{o.stroke_width}" '
          f'stroke-linecap="round" stroke-linejoin="round">'
          + "".join(f'<path d="{d}"/>' for d in folds) + "</g>")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W_} {H_}" '
            f'width="{W_}" height="{H_}">' + g + fg + "</svg>")


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "aurora.jpeg"
    dst = sys.argv[2] if len(sys.argv) > 2 else "out.svg"
    svg = vectorize_to_svg(open(src, "rb").read())
    open(dst, "w").write(svg)
    print(f"ok -> {dst}  ({len(svg)} bytes)")
