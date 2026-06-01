# service.py — sidecar HTTP que sua API Next.js chama (mesmo contrato multipart).
# Sobe com:  uvicorn service:app --host 0.0.0.0 --port 8000
import os
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
):
    data = await file.read()
    opts = VectorizeOptions(
        saturation=saturation, stroke=stroke, stroke_width=stroke_width,
        capture_folds=capture_folds, color_separate_text=color_separate_text,
        fold_erode=fold_erode, turdsize_text=turdsize_text,
        split_y=(None if split_y == -2 else split_y),
    )
    try:
        svg = vectorize_to_svg(data, opts)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    fname = (file.filename or "image").rsplit(".", 1)[0] + ".svg"
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
