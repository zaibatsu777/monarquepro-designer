from pathlib import Path
from uuid import uuid4
from typing import Union

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image


app = FastAPI(title="T-Shirt Multi-View API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

PRINT_AREA_CM = {
    "width": 12.0,
    "height": 12.0,
}

ZONES = {
    "front": {"x": 495, "y": 258, "w": 125, "h": 80},
    "back": {"x": 335, "y": 220, "w": 240, "h": 180},
    "left_sleeve": {"x": 420, "y": 390, "w": 90, "h": 90},
    "right_sleeve": {"x": 370, "y": 400, "w": 90, "h": 90},
}

TEMPLATES = {
    "front": ASSETS_DIR / "garment_front.jpg",
    "back": ASSETS_DIR / "garment_back.jpg",
    "left_sleeve": ASSETS_DIR / "garment_left_sleeve.jpg",
    "right_sleeve": ASSETS_DIR / "garment_right_sleeve.jpg",
}

OptionalUpload = Union[UploadFile, str, None]


def is_real_upload(file) -> bool:
    return (
        file is not None
        and not isinstance(file, str)
        and hasattr(file, "filename")
        and file.filename not in [None, ""]
    )


def validate_extension(filename: str):
    ext = Path(filename or "").suffix.lower()

    if ext == ".pdf":
        raise HTTPException(
            status_code=400,
            detail="PDF files are not supported. Upload PNG, JPG, JPEG, or WEBP.",
        )

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format. Upload PNG, JPG, JPEG, or WEBP.",
        )


def validate_template(view_name: str) -> Path:
    template_path = TEMPLATES[view_name]

    if not template_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"{template_path.name} not found in assets folder.",
        )

    return template_path


def remove_white_background(
    image: Image.Image,
    threshold: int = 252,
    softness: int = 20,
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
        raise HTTPException(
            status_code=400,
            detail="Logo size must be greater than 0 cm.",
        )

    if size_cm > max_cm:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum allowed logo size is {max_cm} cm.",
        )

    return size_cm / max_cm


def resize_logo_to_zone(
    logo: Image.Image,
    zone_w: int,
    zone_h: int,
    scale: float,
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


def render_single_view(
    view_name: str,
    logo_path: Path,
    size_cm: float,
) -> str:
    template_path = validate_template(view_name)
    zone = ZONES[view_name]

    try:
        base = Image.open(template_path).convert("RGBA")
        logo = Image.open(logo_path).convert("RGBA")

        logo = remove_white_background(logo)

        scale = cm_to_scale(
            size_cm=size_cm,
            max_cm=PRINT_AREA_CM["width"],
        )

        rendered_logo = resize_logo_to_zone(
            logo=logo,
            zone_w=zone["w"],
            zone_h=zone["h"],
            scale=scale,
        )

        paste_centered(base, rendered_logo, zone)

        output_name = f"{uuid4()}_{view_name}.png"
        output_path = OUTPUT_DIR / output_name

        base.save(output_path)

        return f"/preview/{output_name}"

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Rendering failed for {view_name}: {str(e)}",
        )


async def save_upload_to_temp(upload, label: str) -> Path:
    validate_extension(upload.filename)

    ext = Path(upload.filename).suffix.lower()
    temp_path = OUTPUT_DIR / f"{uuid4()}_{label}{ext}"

    file_bytes = await upload.read()

    if not file_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"{label} upload is empty.",
        )

    temp_path.write_bytes(file_bytes)

    return temp_path


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/render-multi")
async def render_multi(
    front_logo: OptionalUpload = File(None),
    back_logo: OptionalUpload = File(None),
    left_sleeve_logo: OptionalUpload = File(None),
    right_sleeve_logo: OptionalUpload = File(None),

    front_size_cm: float = Form(10.0),
    back_size_cm: float = Form(12.0),
    left_sleeve_size_cm: float = Form(6.0),
    right_sleeve_size_cm: float = Form(6.0),
):
    uploads = {
        "front": {
            "file": front_logo,
            "size_cm": front_size_cm,
        },
        "back": {
            "file": back_logo,
            "size_cm": back_size_cm,
        },
        "left_sleeve": {
            "file": left_sleeve_logo,
            "size_cm": left_sleeve_size_cm,
        },
        "right_sleeve": {
            "file": right_sleeve_logo,
            "size_cm": right_sleeve_size_cm,
        },
    }

    if not any(is_real_upload(item["file"]) for item in uploads.values()):
        raise HTTPException(
            status_code=400,
            detail="At least one logo must be uploaded.",
        )

    temp_files = []
    renders = {}

    try:
        for view_name, item in uploads.items():
            upload = item["file"]
            size_cm = item["size_cm"]

            if not is_real_upload(upload):
                continue

            temp_logo_path = await save_upload_to_temp(upload, f"{view_name}_logo")
            temp_files.append(temp_logo_path)

            renders[view_name] = render_single_view(
                view_name=view_name,
                logo_path=temp_logo_path,
                size_cm=size_cm,
            )

        return {
            "status": "success",
            "renders": renders,
            "note": "Empty file fields are ignored. Upload PNG, JPG, JPEG, or WEBP.",
        }

    finally:
        for temp_file in temp_files:
            if temp_file.exists():
                temp_file.unlink()


@app.get("/preview/{filename}")
def get_preview(filename: str):
    file_path = OUTPUT_DIR / filename

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Preview not found.",
        )

    return FileResponse(file_path)