from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

app = FastAPI(title="T-Shirt MVP API")

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

CHEST_ZONE = {
    "x": 495,
    "y": 258,
    "w": 125,
    "h": 80,
}

PRINT_AREA_CM = {
    "width": 12.0,
    "height": 12.0,
}

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def validate_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format. Use PNG, JPG, JPEG, or WEBP."
        )
    return ext


def remove_white_background(
    image: Image.Image,
    threshold: int = 252,
    softness: int = 20
) -> Image.Image:
    image = image.convert("RGBA")
    new_data = []

    for pixel in image.getdata():
        r, g, b, a = pixel
        brightness = (r + g + b) / 3

        if brightness >= threshold:
            new_alpha = 0
        elif brightness >= threshold - softness:
            fade = int(255 * (threshold - brightness) / softness)
            new_alpha = min(a, fade)
        else:
            new_alpha = a

        new_data.append((r, g, b, new_alpha))

    image.putdata(new_data)
    return image


def cm_to_scale(size_cm: float, max_cm: float = 12.0) -> float:
    if size_cm <= 0:
        raise HTTPException(status_code=400, detail="size_cm must be greater than 0.")
    if size_cm > max_cm:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum allowed logo size is {max_cm} cm."
        )
    return size_cm / max_cm


def resize_logo_to_zone(
    logo: Image.Image,
    zone_w: int,
    zone_h: int,
    scale: float
) -> Image.Image:
    max_w = max(1, int(zone_w * scale))
    max_h = max(1, int(zone_h * scale))

    logo_copy = logo.copy()
    logo_copy.thumbnail((max_w, max_h), Image.LANCZOS)
    return logo_copy


def paste_centered(base: Image.Image, overlay: Image.Image, zone: dict) -> None:
    paste_x = zone["x"] + (zone["w"] - overlay.width) // 2
    paste_y = zone["y"] + (zone["h"] - overlay.height) // 2
    base.alpha_composite(overlay, (paste_x, paste_y))


def render_preview(
    garment_path: Path,
    logo_path: Path,
    size_cm: float,
    output_prefix: str = "preview"
) -> dict:
    if not garment_path.exists():
        raise HTTPException(status_code=404, detail=f"{garment_path.name} not found.")
    if not logo_path.exists():
        raise HTTPException(status_code=404, detail=f"{logo_path.name} not found.")

    try:
        base = Image.open(garment_path).convert("RGBA")
        logo = Image.open(logo_path).convert("RGBA")

        logo = remove_white_background(logo, threshold=252, softness=20)

        scale = cm_to_scale(size_cm, max_cm=PRINT_AREA_CM["width"])

        rendered_logo = resize_logo_to_zone(
            logo=logo,
            zone_w=CHEST_ZONE["w"],
            zone_h=CHEST_ZONE["h"],
            scale=scale,
        )

        paste_centered(base, rendered_logo, CHEST_ZONE)

        output_name = f"{uuid4()}_{output_prefix}.png"
        output_path = OUTPUT_DIR / output_name
        base.save(output_path)

        return {
            "status": "success",
            "preview_file": output_name,
            "preview_url": f"/preview/{output_name}",
            "zone_used": CHEST_ZONE,
            "print_area_cm": PRINT_AREA_CM,
            "requested_logo_size_cm": size_cm,
            "applied_scale": round(scale, 4),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rendering failed: {str(e)}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/render-test")
def render_test(size_cm: float = Form(10.0)):
    garment_path = ASSETS_DIR / "garment_front.jpg"
    logo_path = ASSETS_DIR / "logo.png"

    return render_preview(
        garment_path=garment_path,
        logo_path=logo_path,
        size_cm=size_cm,
        output_prefix="render_test"
    )


@app.post("/render")
async def render_mockup(
    garment: UploadFile = File(...),
    logo: UploadFile = File(...),
    size_cm: float = Form(10.0),
):
    validate_extension(garment.filename)
    validate_extension(logo.filename)

    garment_ext = Path(garment.filename).suffix.lower()
    logo_ext = Path(logo.filename).suffix.lower()

    garment_temp = OUTPUT_DIR / f"{uuid4()}_garment{garment_ext}"
    logo_temp = OUTPUT_DIR / f"{uuid4()}_logo{logo_ext}"

    garment_bytes = await garment.read()
    logo_bytes = await logo.read()

    garment_temp.write_bytes(garment_bytes)
    logo_temp.write_bytes(logo_bytes)

    try:
        result = render_preview(
            garment_path=garment_temp,
            logo_path=logo_temp,
            size_cm=size_cm,
            output_prefix="render_upload"
        )
        return JSONResponse(result)

    finally:
        if garment_temp.exists():
            garment_temp.unlink()
        if logo_temp.exists():
            logo_temp.unlink()


@app.get("/preview/{filename}")
def get_preview(filename: str):
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Preview not found.")
    return FileResponse(file_path)