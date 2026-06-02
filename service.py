# service.py — sidecar HTTP que sua API Next.js chama (mesmo contrato multipart).
# Sobe com:  uvicorn service:app --host 0.0.0.0 --port 8000
import os
import numpy as np
import cv2
from fastapi import Depends, FastAPI, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import Response, JSONResponse
from aurora_vectorizer import vectorize_to_svg, VectorizeOptions

app = FastAPI(title="Aurora HQ Vectorizer")

_API_KEY = os.environ.get("API_KEY")

def _check_key(x_api_key: str | None = Header(None)):
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/vectorize", dependencies=[Depends(_check_key)])
async def vectorize(
    file: UploadFile = File(...),
    saturation: float = Form(1.8),
    stroke: str = Form("#111111"),
    stroke_width: float = Form(2.4),
    capture_folds: bool = Form(True),
    color_separate_text: bool = Form(True),
    fold_erode: int = Form(11),
    turdsize_text: int = Form(8),
    # split_y: -2 = automático (vão) | -1 = sem texto separado | >=0 = manual (px na imagem original)
    split_y: int = Form(-2),
    invert_bg: bool = Form(False),
):
    data = await file.read()
    # Fundo escuro: inverter imagem para que o motor veja fundo branco.
    # Apos inversao, normaliza brilho para que o fundo fique branco puro (>240).
    # Sem isso, fundos cinza escuro (~50) viram cinza claro (~205) apos 255-arr,
    # e o ink detector (gray < 230) classifica o fundo como tinta → SVG vazio.
    if invert_bg:
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is not None:
            arr = 255 - arr
            gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
            bg_val = float(np.percentile(gray, 85))
            if 100 < bg_val < 245:
                scale = 255.0 / bg_val
                arr = np.clip(arr.astype(np.float32) * scale, 0, 255).astype(np.uint8)
            _, buf = cv2.imencode('.png', arr)
            data = buf.tobytes()
    opts = VectorizeOptions(
        saturation=saturation, stroke=stroke, stroke_width=stroke_width,
        capture_folds=capture_folds, color_separate_text=color_separate_text,
        fold_erode=fold_erode, turdsize_text=turdsize_text,
        split_y=(None if split_y == -2 else split_y),
        fill_emblem_holes=not invert_bg,
    )
    try:
        svg = vectorize_to_svg(data, opts)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    fname = (file.filename or "image").rsplit(".", 1)[0] + ".svg"
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
