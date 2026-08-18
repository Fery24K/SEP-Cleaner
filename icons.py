import os
import sys
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

_icon_cache = {}


def resource_path(relative_path: str) -> str:
    """Mendapatkan path absolut ke resource, mendukung PyInstaller (_MEIPASS) dan Dev."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def _recolor_image(img: Image.Image, color_hex: str) -> Image.Image:
    """Fungsi untuk mengubah warna ikon solid yang memiliki background transparan."""
    img = img.convert("RGBA")
    colored_img = Image.new("RGBA", img.size, color_hex)
    mask = img.split()[3]
    colored_img.putalpha(mask)
    return colored_img


def get_icon(symbol, size=20, light_color="#111318", dark_color="#F2F3F5", force_color=None):
    if force_color:
        light_color = force_color
        dark_color = force_color

    cache_key = f"{symbol}_{size}_{light_color}_{dark_color}"

    if cache_key in _icon_cache:
        return _icon_cache[cache_key]

    # Menggunakan resource_path agar kompatibel dengan PyInstaller
    icon_dir = resource_path("icons")
    icon_path = os.path.join(icon_dir, f"{symbol}.png")

    # Jika file sama sekali tidak ada, buat placeholder (huruf)
    if not os.path.exists(icon_path):
        img_placeholder = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img_placeholder)
        draw.ellipse([2, 2, size - 2, size - 2], fill=(37, 99, 235, 200))

        try:
            font = ImageFont.truetype("arial.ttf", int(size * 0.6))
        except Exception:
            font = ImageFont.load_default()

        text = symbol[0].upper()
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (size - (bbox[2] - bbox[0])) // 2
        y = (size - (bbox[3] - bbox[1])) // 2 - 2
        draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)

        ctk_img = ctk.CTkImage(light_image=img_placeholder, dark_image=img_placeholder, size=(size, size))
        _icon_cache[cache_key] = ctk_img
        return ctk_img

    # Load file ikon SATU KALI saja
    base_img = Image.open(icon_path)

    if base_img.size != (size, size):
        min_side = min(base_img.size)
        left = (base_img.width - min_side) // 2
        top = (base_img.height - min_side) // 2
        base_img = base_img.crop((left, top, left + min_side, top + min_side))
        base_img = base_img.resize((size, size), Image.Resampling.LANCZOS)

    img_light = _recolor_image(base_img, light_color)
    img_dark = _recolor_image(base_img, dark_color)

    ctk_img = ctk.CTkImage(light_image=img_light, dark_image=img_dark, size=(size, size))

    _icon_cache[cache_key] = ctk_img
    return ctk_img


def clear_cache():
    """Bersihkan cache icon"""
    _icon_cache.clear()