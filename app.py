import os
import re
import sys
import io
import zlib
import ctypes
import threading
import queue
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD
import fitz  # PyMuPDF

from updater import check_for_update

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from icons import get_icon, resource_path


# ================= TRIK WINDOWS TASKBAR ICON =================
try:
    myappid = "pdfsepcleaner.app.gui.1.0"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception:
    pass


# ================= Konfigurasi Tampilan Dasar =================
ctk.set_appearance_mode("dark")

BG_PAGE          = ("#E7F3FF", "#0F172A")
BG_CARD          = ("#FFFFFF", "#1E293B")
BG_SUBTLE        = ("#F1F5F9", "#334155")
BORDER           = ("#E2E8F0", "#475569")
TEXT_PRIMARY     = ("#0F172A", "#F8FAFC")
TEXT_SECONDARY   = ("#475569", "#94A3B8")
TEXT_MUTED       = ("#64748B", "#64748B")
ACCENT           = ("#2563EB", "#3B82F6")
ACCENT_HOVER     = ("#1D4ED8", "#2563EB")
ACCENT_BG        = ("#EFF6FF", "#1E293B")
DANGER           = ("#DC2626", "#EF4444")
DANGER_BG        = ("#DC2626", "#DC2626")
DANGER_BG_HOVER  = ("#EF4444", "#EF4444")

SUCCESS_TEXT     = ("#15803D", "#4ADE80")
WARN_TEXT        = ("#B45309", "#FBBF24")
ERROR_TEXT       = ("#DC2626", "#F87171")

FONT_FAMILY      = ("Segoe UI", "SF Pro Display", "Arial")
FONT_MONO        = ("Consolas", "Cascadia Code", "Courier New")

RADIUS_CARD = 12
RADIUS_CTRL = 8
MIN_LOG_BOX_HEIGHT = 140
BUTTON_TEXT_COLOR = "#FFFFFF"

# ================= Pola Regex =================
URL_PATTERN = re.compile(
    r'(https?://[^\s]+)|(http://[^\s]+)|([a-zA-Z0-9.-]+\.(com|net|org|id|go\.id|edu|mil|info|biz|co\.id)[^\s]*)|(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?)',
    re.IGNORECASE
)

JS_PATTERN = re.compile(
    r'(javascript:[^\s]+)|(onclick\s*=\s*["\'][^"\']*["\'])|(onload\s*=\s*["\'][^"\']*["\'])',
    re.IGNORECASE
)

SEP_PATTERN = re.compile(r'(\d{4})[Rr](\d{7})[Vv](\d{6})')

# ================= Info Aplikasi =================
APP_VERSION = "1.5"
GITHUB_REPO = os.environ.get("SEP_CLEANER_GITHUB_REPO")
DEVELOPER_NAME = "SIMRS Sahabat"
DEVELOPER_WEBSITE = "https://sahabatmedia.co.id"

FEATURE_LIST = [
    "Hapus URL, URI, dan JavaScript dari dokumen PDF.",
    "Hapus semua objek /EmbeddedFile di seluruh dokumen.",
    "Hapus nama file berlebih menjadi 19 digit nomor SEP.",
    "Format otomatis huruf R dan V menjadi kapital pada nama file.",
    "Otomatis kompres ukuran file PDF yang melebihi 30 MB.",
    "Bersihkan ratusan PDF sekaligus cukup dengan 1x Klik.",
]

# ================= Konfigurasi Kompresi Otomatis =================
MAX_OUTPUT_SIZE_BYTES = 30 * 1024 * 1024  # 30 MB

COMPRESSION_LEVELS = [
    (85, 1.00), (75, 1.00), (60, 1.00),
    (75, 0.85), (60, 0.85), (45, 0.85),
    (60, 0.70), (45, 0.70), (35, 0.70),
    (40, 0.55), (30, 0.50),
]


def extract_sep_filename(original_filename: str):
    base_name = os.path.splitext(original_filename)[0]
    match = SEP_PATTERN.search(base_name)
    if match:
        g1, g2, g3 = match.groups()
        return f"{g1}R{g2}V{g3}.pdf"
    return None


def get_unique_output_path(output_folder: str, filename: str) -> str:
    candidate = os.path.join(output_folder, filename)
    if not os.path.exists(candidate):
        return candidate
    base, ext = os.path.splitext(filename)
    counter = 1
    while True:
        candidate = os.path.join(output_folder, f"{base}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _compress_pdf_images(pdf_bytes: bytes, quality: int, scale: float) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    seen_xrefs = set()

    for page in doc:
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            try:
                base_image = doc.extract_image(xref)
                img_bytes = base_image["image"]

                pil_img = Image.open(io.BytesIO(img_bytes))
                if pil_img.mode in ("RGBA", "P", "LA", "CMYK"):
                    pil_img = pil_img.convert("RGB")

                if scale < 1.0:
                    w, h = pil_img.size
                    new_w = max(1, int(w * scale))
                    new_h = max(1, int(h * scale))
                    pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)

                buffer = io.BytesIO()
                pil_img.save(buffer, format="JPEG", quality=quality, optimize=True)
                new_bytes = buffer.getvalue()

                if len(new_bytes) < len(img_bytes):
                    page.replace_image(xref, stream=new_bytes)
            except Exception:
                continue

    out_buffer = io.BytesIO()
    doc.save(out_buffer, garbage=4, deflate=True, clean=True)
    doc.close()
    return out_buffer.getvalue()


def compress_pdf_to_target(pdf_bytes: bytes, target_bytes: int, log_queue: queue.Queue = None,
                            original_filename: str = "") -> bytes:
    if len(pdf_bytes) <= target_bytes:
        return pdf_bytes

    if not PIL_AVAILABLE:
        if log_queue:
            log_queue.put(("warn", f"{original_filename}: modul Pillow tidak terpasang, kompresi ukuran dilewati."))
        return pdf_bytes

    best_bytes = pdf_bytes
    for quality, scale in COMPRESSION_LEVELS:
        try:
            candidate = _compress_pdf_images(pdf_bytes, quality=quality, scale=scale)
        except Exception as e:
            if log_queue:
                log_queue.put(("warn", f"{original_filename}: level kompresi q={quality} skala={scale} gagal ({e})"))
            continue

        if len(candidate) < len(best_bytes):
            best_bytes = candidate

        if len(candidate) <= target_bytes:
            return candidate

    if log_queue:
        size_mb = len(best_bytes) / (1024 * 1024)
        log_queue.put((
            "warn",
            f"{original_filename}: ukuran akhir {size_mb:.1f} MB, target di bawah 30MB belum tercapai "
            f"(gambar dalam PDF sudah dikompres maksimal)."
        ))
    return best_bytes


def remove_embeddings_from_pdf(pdf_bytes: bytes) -> bytes:
    """Menghapus semua file attachment/embedding dari PDF."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        names = doc.embfile_names()
        for name in names:
            try:
                doc.embfile_delete(name)
            except Exception:
                continue
        
        out_buffer = io.BytesIO()
        doc.save(out_buffer, garbage=1, deflate=True)
        return out_buffer.getvalue()
    finally:
        doc.close()


def remove_embeddedfile_aggressive(pdf_bytes: bytes, log_queue: queue.Queue = None, 
                                    original_filename: str = "") -> bytes:
    """
    Menghapus semua jejak /EmbeddedFile dari PDF secara agresif.
    Pendekatan: parse ulang seluruh file sebagai teks, hapus semua pola /EmbeddedFile,
    lalu rekonstruksi ulang.
    """
    # Metode 1: Coba dengan pendekatan object-by-object
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        modified = False
        
        # Sisir semua objek
        for xref in range(1, doc.xref_length()):
            try:
                obj_str = doc.xref_object(xref, compressed=False)
                # Cek apakah ada /EmbeddedFile
                if '/EmbeddedFile' in obj_str:
                    # Hapus entri /EmbeddedFile menggunakan regex
                    # Pola ini lebih agresif: hapus /EmbeddedFile diikuti dengan value apapun
                    new_obj_str = re.sub(
                        r'/EmbeddedFile\s*(?:\[[^\]]*\]|<<[^>]*>>|[^\s/>]+)',
                        '',
                        obj_str
                    )
                    # Bersihkan juga kemungkinan sisa spasi berlebih
                    new_obj_str = re.sub(r'\s+', ' ', new_obj_str)
                    if new_obj_str != obj_str:
                        doc.update_object(xref, new_obj_str)
                        modified = True
            except Exception:
                continue
        
        if modified:
            out_buffer = io.BytesIO()
            doc.save(out_buffer, garbage=4, deflate=True, clean=True)
            pdf_bytes = out_buffer.getvalue()
    finally:
        doc.close()
    
    # Metode 2: Jika masih ada /EmbeddedFile, lakukan pendekatan raw binary
    if b'/EmbeddedFile' in pdf_bytes:
        if log_queue:
            log_queue.put(("warn", f"{original_filename}: masih ada /EmbeddedFile, mencoba pendekatan raw..."))
        
        # Baca sebagai teks, hapus semua pola /EmbeddedFile
        try:
            # Decode dengan fallback
            try:
                text = pdf_bytes.decode('latin-1')
            except:
                text = pdf_bytes.decode('utf-8', errors='ignore')
            
            # Hapus semua pola /EmbeddedFile dengan berbagai variasi
            text = re.sub(r'/EmbeddedFile\s*(?:\[[^\]]*\]|<<[^>]*>>|[^\s/>]+)', '', text)
            # Hapus juga /EF (singkatan dari EmbeddedFile)
            text = re.sub(r'/EF\s*(?:\[[^\]]*\]|<<[^>]*>>|[^\s/>]+)', '', text)
            
            # Kembalikan ke bytes
            pdf_bytes = text.encode('latin-1')
        except Exception as e:
            if log_queue:
                log_queue.put(("error", f"{original_filename}: gagal raw processing: {e}"))
    
    # Metode 3: Terakhir, coba parse ulang dan bersihkan metadata
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        # Bersihkan metadata
        metadata = doc.metadata
        if metadata:
            for key in list(metadata.keys()):
                if isinstance(metadata[key], str):
                    if '/EmbeddedFile' in metadata[key] or 'EmbeddedFile' in metadata[key]:
                        metadata[key] = ''
            doc.set_metadata(metadata)
        
        out_buffer = io.BytesIO()
        doc.save(out_buffer, garbage=4, deflate=True, clean=True)
        pdf_bytes = out_buffer.getvalue()
    finally:
        doc.close()
    
    return pdf_bytes


def remove_urls_and_links_from_pdf(pdf_bytes: bytes) -> bytes:
    """Menghapus URL, IP Address, JavaScript, dan link interaktif dari PDF."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    try:
        for page in doc:
            # 1. Hapus semua link/tautan interaktif
            for link in page.get_links():
                try:
                    page.delete_link(link)
                except Exception:
                    pass
            
            try:
                for annot in page.annots():
                    if annot.type[0] == 2:  # Link annotation
                        try:
                            page.delete_annot(annot)
                        except Exception:
                            pass
            except Exception:
                pass

            # 2. Hapus teks URL, IP, dan JavaScript
            blocks = page.get_text("dict")
            for block in blocks.get("blocks", []):
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            text = span["text"]
                            bbox = span["bbox"]
                            
                            if URL_PATTERN.search(text) or JS_PATTERN.search(text):
                                rect = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])
                                page.add_redact_annot(rect, fill=(1, 1, 1), text=" ")
            
            page.apply_redactions()

        # 3. Hapus metadata yang mencurigakan
        try:
            metadata = doc.metadata
            if metadata:
                for key in list(metadata.keys()):
                    if isinstance(metadata[key], str):
                        if URL_PATTERN.search(metadata[key]) or JS_PATTERN.search(metadata[key]):
                            metadata[key] = ""
                doc.set_metadata(metadata)
        except Exception:
            pass
        
        out_buffer = io.BytesIO()
        doc.save(out_buffer, garbage=4, deflate=True, clean=True)
        return out_buffer.getvalue()
    finally:
        doc.close()


_PDF_ENTRY_VALUE = r'(?:\[[^\]]*\]|<<(?:[^<>]|<<[^<>]*>>)*>>|(?:\d+\s+\d+\s+R)|[^/>\s]+)'


def _strip_pdf_key(obj_str: str, key: str) -> str:
    pattern = re.compile(r'/' + re.escape(key) + r'\s*' + _PDF_ENTRY_VALUE, re.DOTALL)
    return pattern.sub('', obj_str)


def remove_javascript_and_openaction_from_pdf(pdf_bytes: bytes) -> bytes:
    """Menghapus KEY /JS dan /OpenAction dari PDF."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        catalog_xref = doc.pdf_catalog()

        # 1. Hapus /OpenAction & /AA di root Catalog
        try:
            cat_str = doc.xref_object(catalog_xref, compressed=False)
            new_cat_str = _strip_pdf_key(cat_str, "OpenAction")
            new_cat_str = _strip_pdf_key(new_cat_str, "AA")
            if new_cat_str != cat_str:
                doc.update_object(catalog_xref, new_cat_str)
        except Exception:
            pass

        # 2. Hapus /Names -> /JavaScript
        try:
            if "Names" in doc.xref_get_keys(catalog_xref):
                names_ref = doc.xref_get_key(catalog_xref, "Names")
                if names_ref[0] == "xref":
                    names_xref = int(names_ref[1].split()[0])
                    names_str = doc.xref_object(names_xref, compressed=False)
                    new_names_str = _strip_pdf_key(names_str, "JavaScript")
                    if new_names_str != names_str:
                        doc.update_object(names_xref, new_names_str)
        except Exception:
            pass

        # 3. Hapus /AA di setiap halaman & annotation
        for page in doc:
            try:
                page_str = doc.xref_object(page.xref, compressed=False)
                new_page_str = _strip_pdf_key(page_str, "AA")
                if new_page_str != page_str:
                    doc.update_object(page.xref, new_page_str)
            except Exception:
                pass
            try:
                for annot in page.annots():
                    try:
                        a_str = doc.xref_object(annot.xref, compressed=False)
                        new_a_str = _strip_pdf_key(a_str, "AA")
                        new_a_str = _strip_pdf_key(new_a_str, "A")
                        if new_a_str != a_str:
                            doc.update_object(annot.xref, new_a_str)
                    except Exception:
                        pass
            except Exception:
                pass

        light_buffer = io.BytesIO()
        doc.save(light_buffer, garbage=1, deflate=True)
        light_bytes = light_buffer.getvalue()
    finally:
        doc.close()

    if b"/JS" not in light_bytes:
        return light_bytes

    doc2 = fitz.open(stream=light_bytes, filetype="pdf")
    try:
        for xref in range(1, doc2.xref_length()):
            try:
                keys = doc2.xref_get_keys(xref)
            except Exception:
                continue
            if not keys or ("JS" not in keys and "S" not in keys):
                continue
            try:
                obj_str = doc2.xref_object(xref, compressed=False)
            except Exception:
                continue
            new_obj_str = obj_str
            if "JS" in keys:
                new_obj_str = _strip_pdf_key(new_obj_str, "JS")
            if "S" in keys and "/JavaScript" in obj_str:
                new_obj_str = _strip_pdf_key(new_obj_str, "S")
            if new_obj_str != obj_str:
                try:
                    doc2.update_object(xref, new_obj_str)
                except Exception:
                    pass

        out_buffer = io.BytesIO()
        doc2.save(out_buffer, garbage=4, deflate=True, clean=True)
        return out_buffer.getvalue()
    finally:
        doc2.close()


def neutralize_coincidental_forbidden_bytes(pdf_bytes: bytes, log_queue: queue.Queue = None,
                                              original_filename: str = "") -> bytes:
    """Netralkan kemunculan byte '/JS' atau '/OpenAction' yang kebetulan."""
    tokens = tuple(t for t in (b"/JS", b"/OpenAction") if t in pdf_bytes)
    if not tokens:
        return pdf_bytes

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for xref in range(1, doc.xref_length()):
            try:
                if not doc.xref_is_stream(xref):
                    continue
                raw_stream = doc.xref_stream_raw(xref)
            except Exception:
                continue
            if not raw_stream:
                continue

            matched_tokens = [t for t in tokens if t in raw_stream]
            if not matched_tokens:
                continue

            try:
                decompressed = doc.xref_stream(xref)
            except Exception:
                decompressed = None
            if decompressed is None:
                continue
            matched_tokens = [t for t in matched_tokens if t not in decompressed]
            if not matched_tokens:
                continue

            fixed = False
            for level in (6, 1, 9, 4, 2, 7, 3, 8, 5, 0):
                try:
                    recompressed = zlib.compress(decompressed, level)
                except Exception:
                    continue
                if all(t not in recompressed for t in matched_tokens):
                    try:
                        doc.update_stream(xref, decompressed, new=True)
                        fixed = True
                    except Exception:
                        fixed = False
                    break

            if not fixed and log_queue:
                names = "/".join(t.decode() for t in matched_tokens)
                log_queue.put((
                    "warn",
                    f"{original_filename}: tidak bisa menetralkan noise biner "
                    f"'{names}' pada salah satu stream, periksa manual."
                    ))

        out_buffer = io.BytesIO()
        doc.save(out_buffer, garbage=1, deflate=True)
        return out_buffer.getvalue()
    finally:
        doc.close()


def process_pdf(input_path: str, output_folder: str, log_queue: queue.Queue):
    original_filename = os.path.basename(input_path)
    new_filename = extract_sep_filename(original_filename)

    if new_filename is None:
        log_queue.put(("warn", f"Dilewati (kode SEP 19 digit tidak ditemukan): {original_filename}"))
        return

    try:
        # Baca file PDF
        with open(input_path, "rb") as f:
            pdf_bytes = f.read()

        # 1. Hapus URL, IP, JavaScript (teks tampak), dan link interaktif
        clean_urls_bytes = remove_urls_and_links_from_pdf(pdf_bytes)

        # 2. Hapus objek /JS dan /OpenAction pada level struktur PDF
        clean_js_bytes = remove_javascript_and_openaction_from_pdf(clean_urls_bytes)

        # 3. Hapus semua embedding/file attachment (level atas)
        clean_embed_bytes = remove_embeddings_from_pdf(clean_js_bytes)

        # 4. Hapus semua objek /EmbeddedFile secara agresif
        clean_embed_aggressive = remove_embeddedfile_aggressive(
            clean_embed_bytes, log_queue, original_filename
        )

        # 5. Netralkan kemunculan byte '/JS' atau '/OpenAction' yang kebetulan
        clean_bytes = neutralize_coincidental_forbidden_bytes(
            clean_embed_aggressive, log_queue, original_filename
        )

        # 6. Simpan ke file output
        output_path = get_unique_output_path(output_folder, new_filename)
        with open(output_path, "wb") as f:
            f.write(clean_bytes)

        # 7. Jika ukuran file hasil >= 30MB, kompres otomatis
        file_size = os.path.getsize(output_path)
        if file_size >= MAX_OUTPUT_SIZE_BYTES:
            with open(output_path, "rb") as f:
                original_bytes = f.read()

            compressed_bytes = compress_pdf_to_target(
                original_bytes, MAX_OUTPUT_SIZE_BYTES, log_queue, original_filename
            )

            if len(compressed_bytes) < len(original_bytes):
                with open(output_path, "wb") as f:
                    f.write(compressed_bytes)

            final_size = os.path.getsize(output_path)
            status = "success" if final_size < MAX_OUTPUT_SIZE_BYTES else "warn"
            log_queue.put((
                status,
                f"{original_filename}  ->  {os.path.basename(output_path)}  "
                f"(dikompres {file_size / 1024 / 1024:.1f}MB -> {final_size / 1024 / 1024:.1f}MB)"
            ))
        else:
            log_queue.put(("success", f"{original_filename}  ->  {os.path.basename(output_path)}"))
            
    except Exception as e:
        log_queue.put(("error", f"Gagal memproses {original_filename}: {e}"))


class Card(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            fg_color=BG_CARD,
            corner_radius=RADIUS_CARD,
            border_width=1,
            border_color=BORDER,
            **kwargs
        )


class CTkWithDnD(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


class AboutDialog(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Tentang Aplikasi")
        self.configure(fg_color=BG_PAGE)
        self.transient(master)
        self.grab_set()

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        win_w = min(480, screen_w - 40)
        win_h = min(560, screen_h - 80)
        self.geometry(f"{win_w}x{win_h}")
        self.minsize(320, 320)
        self.resizable(True, True)
        self.after(10, self._center_on_parent(master))

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=20, pady=20)

        logo_box = ctk.CTkFrame(
            container, width=56, height=56, corner_radius=14, fg_color=ACCENT_BG
        )
        logo_box.pack(pady=(0, 10))
        logo_box.pack_propagate(False)
        logo_icon = get_icon("file-pdf", size=28)
        ctk.CTkLabel(logo_box, text="", image=logo_icon).place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            container, text="SEP Cleaner", text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=18, weight="bold")
        ).pack()
        ctk.CTkLabel(
            container, text=f"Versi {APP_VERSION}", text_color=TEXT_MUTED,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=12)
        ).pack(pady=(0, 14))

        dev_card = Card(container)
        dev_card.pack(fill="x", pady=(0, 12))
        dev_inner = ctk.CTkFrame(dev_card, fg_color="transparent")
        dev_inner.pack(fill="x", padx=14, pady=12)

        ctk.CTkLabel(
            dev_inner, text="Dikembangkan oleh", text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=11)
        ).pack(anchor="center")
        ctk.CTkLabel(
            dev_inner, text=DEVELOPER_NAME, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=14, weight="bold")
        ).pack(anchor="center", pady=(2, 6))

        link_label = ctk.CTkLabel(
            dev_inner, text=DEVELOPER_WEBSITE, text_color=ACCENT,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=12, underline=True),
            cursor="hand2", anchor="center"
        )
        link_label.pack(anchor="center")
        link_label.bind("<Button-1>", lambda e: webbrowser.open(DEVELOPER_WEBSITE))

        feat_card = Card(container)
        feat_card.pack(fill="x", pady=(0, 14))
        feat_inner = ctk.CTkFrame(feat_card, fg_color="transparent")
        feat_inner.pack(fill="x", padx=14, pady=12)

        ctk.CTkLabel(
            feat_inner, text="Fungsi Aplikasi", text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=13, weight="bold"), anchor="w"
        ).pack(anchor="w", pady=(0, 8))

        for feature in FEATURE_LIST:
            row = ctk.CTkFrame(feat_inner, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(
                row, text="\u2022", text_color=ACCENT,
                font=ctk.CTkFont(family=FONT_FAMILY[0], size=12, weight="bold")
            ).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(
                row, text=feature, text_color=TEXT_SECONDARY,
                font=ctk.CTkFont(family=FONT_FAMILY[0], size=11), anchor="w",
                justify="left"
            ).pack(side="left")

    def _center_on_parent(self, master):
        def _center():
            self.update_idletasks()
            mx, my = master.winfo_x(), master.winfo_y()
            mw, mh = master.winfo_width(), master.winfo_height()
            w, h = self.winfo_width(), self.winfo_height()
            x = mx + (mw - w) // 2
            y = my + (mh - h) // 2

            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            x = max(0, min(x, screen_w - w))
            y = max(0, min(y, screen_h - h))

            self.geometry(f"+{x}+{y}")
        return _center


class App(CTkWithDnD):
    def __init__(self):
        super().__init__()

        self.title("SEP Cleaner v1.5 by SIMRS Sahabat")
        self.configure(fg_color=BG_PAGE)
        
        icon_ico = resource_path("logo.ico")
        icon_png = resource_path("logo.png")

        try:
            if os.path.exists(icon_ico):
                self.iconbitmap(icon_ico)
        except Exception:
            pass

        try:
            if os.path.exists(icon_png):
                img = tk.PhotoImage(file=icon_png)
                self.tk.call('wm', 'iconphoto', self._w, img)
        except Exception:
            pass

        self.selected_paths: list[str] = []
        self.output_folder: str | None = None
        self.log_queue: queue.Queue = queue.Queue()
        self.is_processing = False
        self.github_repo = GITHUB_REPO
        self.update_check_done = False

        self._build_ui()
        self._setup_drag_and_drop()
        self._update_log_tags()

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        win_w = min(800, screen_w - 40)
        win_h = max(640, min(850, screen_h - 80))
        self.geometry(f"{win_w}x{win_h}")
        self.minsize(680, 680)

        self.after(100, self._poll_log_queue)
        self.after(500, self._check_update_async)

    def _setup_drag_and_drop(self):
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self._on_drop)
        self.file_list_frame.drop_target_register(DND_FILES)
        self.file_list_frame.dnd_bind('<<Drop>>', self._on_drop)

    def _on_drop(self, event):
        raw_data = event.data
        paths = self.tk.splitlist(raw_data)
        
        added_count = 0
        for path in paths:
            path = path.strip('{}')
            if os.path.isfile(path) and path.lower().endswith('.pdf'):
                if path not in self.selected_paths:
                    self.selected_paths.append(path)
                    added_count += 1
            elif os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for file in files:
                        if file.lower().endswith('.pdf'):
                            full_p = os.path.join(root, file)
                            if full_p not in self.selected_paths:
                                self.selected_paths.append(full_p)
                                added_count += 1

        if added_count > 0:
            self._refresh_file_list()
            self.status_label.configure(text=f"Berhasil menambahkan {added_count} file dari Drag & Drop.")

    def _build_ui(self):
        pad = 20
        gap = 12

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=pad, pady=pad)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x", pady=(0, gap))

        logo_box = ctk.CTkFrame(
            header, width=44, height=44, corner_radius=10,
            fg_color=ACCENT_BG
        )
        logo_box.pack(side="left")
        logo_box.pack_propagate(False)
        
        logo_icon = get_icon("file-pdf", size=24)
        ctk.CTkLabel(logo_box, text="", image=logo_icon).place(relx=0.5, rely=0.5, anchor="center")

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=(12, 0), fill="x", expand=True)
        
        ctk.CTkLabel(
            title_box, text="SEP Cleaner v1.5 by SIMRS Sahabat", text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=18, weight="bold"), anchor="w"
        ).pack(anchor="w")

        self.update_btn = ctk.CTkButton(
            header,
            text="Cek Update",
            width=110,
            height=30,
            fg_color="transparent",
            hover_color=BG_SUBTLE,
            text_color=TEXT_SECONDARY,
            border_width=1,
            border_color=BORDER,
            corner_radius=RADIUS_CTRL,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=11),
            command=self._check_update_async
        )
        self.update_btn.pack(side="right", padx=(10, 0))

        self.theme_icon = ctk.CTkLabel(
            header,
            text="",
            image=get_icon("moon", size=22),
            fg_color="transparent",
            cursor="hand2"
        )
        self.theme_icon.pack(side="right", padx=(10, 0))
        self.theme_icon.bind("<Button-1>", lambda e: self._on_theme_toggle())
        
        def on_enter(e):
            current_mode = ctk.get_appearance_mode().lower()
            if current_mode == "dark":
                self.theme_icon.configure(image=get_icon("moon", size=26))
            else:
                self.theme_icon.configure(image=get_icon("sun", size=26))
        
        def on_leave(e):
            current_mode = ctk.get_appearance_mode().lower()
            if current_mode == "dark":
                self.theme_icon.configure(image=get_icon("moon", size=22))
            else:
                self.theme_icon.configure(image=get_icon("sun", size=22))
        
        self.theme_icon.bind("<Enter>", on_enter)
        self.theme_icon.bind("<Leave>", on_leave)

        mode_card = Card(outer)
        mode_card.pack(fill="x", pady=(0, gap))
        mode_row = ctk.CTkFrame(mode_card, fg_color="transparent")
        mode_row.pack(fill="x", padx=14, pady=12)

        self.mode_var = tk.StringVar(value="File")
        self.mode_switch = ctk.CTkSegmentedButton(
            mode_row, values=["File", "Folder"],
            variable=self.mode_var, command=self._on_mode_change,
            fg_color=BG_SUBTLE, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=BG_SUBTLE, unselected_hover_color=BORDER,
            text_color=TEXT_PRIMARY, corner_radius=RADIUS_CTRL, height=34,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=12, weight="bold")
        )
        self.mode_switch.pack(side="left")
        self._update_mode_switch_text_colors()

        self.select_btn = ctk.CTkButton(
            mode_row, text=" Pilih File PDF", command=self._on_select_input,
            image=get_icon("file-pdf", size=16, light_color="#FFFFFF", dark_color="#FFFFFF"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=BUTTON_TEXT_COLOR,
            corner_radius=RADIUS_CTRL, height=34,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=12, weight="bold")
        )
        self.select_btn.pack(side="right")

        list_card = Card(outer)
        list_card.pack(fill="x", pady=(0, gap))

        list_header = ctk.CTkFrame(list_card, fg_color="transparent")
        list_header.pack(fill="x", padx=14, pady=(12, 8))

        self.file_count_label = ctk.CTkLabel(
            list_header, text="File dipilih (0)", text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=13, weight="bold")
        )
        self.file_count_label.pack(side="left")

        self.clear_btn = ctk.CTkButton(
            list_header, text=" Clear All", width=80, height=28,
            image=get_icon("trash", size=14, light_color="#FFFFFF", dark_color="#FFFFFF"),
            fg_color=DANGER_BG, hover_color=DANGER_BG_HOVER, text_color=BUTTON_TEXT_COLOR,
            corner_radius=RADIUS_CTRL,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=11, weight="bold"),
            command=self._on_clear_list
        )
        self.clear_btn.pack(side="right")

        file_list_wrapper = ctk.CTkFrame(list_card, fg_color="transparent", height=150)
        file_list_wrapper.pack(fill="x", padx=14, pady=(0, 14))
        file_list_wrapper.pack_propagate(False)

        self.file_list_frame = ctk.CTkScrollableFrame(
            file_list_wrapper, fg_color="transparent"
        )
        self.file_list_frame.pack(fill="both", expand=True)
        self.file_list_frame.grid_columnconfigure(0, weight=1)

        out_card = Card(outer)
        out_card.pack(fill="x", pady=(0, gap))
        out_row = ctk.CTkFrame(out_card, fg_color="transparent")
        out_row.pack(fill="x", padx=14, pady=12)

        out_left = ctk.CTkFrame(out_row, fg_color="transparent")
        out_left.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        folder_icon = get_icon("folder", size=16)
        ctk.CTkLabel(out_left, text="", image=folder_icon).pack(side="left", padx=(0, 8))
        
        self.output_label = ctk.CTkLabel(
            out_left, text="Folder output belum dipilih", text_color=TEXT_MUTED,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=12), anchor="w"
        )
        self.output_label.pack(side="left", fill="x", expand=True)

        self.output_btn = ctk.CTkButton(
            out_row, text=" Pilih Output", command=self._on_select_output,
            image=get_icon("folder-open", size=16, light_color="#FFFFFF", dark_color="#FFFFFF"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=BUTTON_TEXT_COLOR,
            corner_radius=RADIUS_CTRL, height=34,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=12, weight="bold")
        )
        self.output_btn.pack(side="right")

        action_card = Card(outer)
        action_card.pack(fill="x", pady=(0, gap))
        action_inner = ctk.CTkFrame(action_card, fg_color="transparent")
        action_inner.pack(fill="x", padx=14, pady=14)

        self.progress_bar = ctk.CTkProgressBar(
            action_inner, height=6, corner_radius=3,
            fg_color=BG_SUBTLE, progress_color=ACCENT
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(0, 8))

        self.status_label = ctk.CTkLabel(
            action_inner, text="Siap memproses.",
            text_color=TEXT_SECONDARY, font=ctk.CTkFont(family=FONT_FAMILY[0], size=12), anchor="w"
        )
        self.status_label.pack(anchor="w", pady=(0, 10))

        self.start_btn = ctk.CTkButton(
            action_inner, text=" Mulai Proses", height=42,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=13, weight="bold"),
            image=get_icon("play", size=18, light_color="#FFFFFF", dark_color="#FFFFFF"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=BUTTON_TEXT_COLOR,
            corner_radius=RADIUS_CTRL,
            command=self._on_start
        )
        self.start_btn.pack(fill="x")

        self.about_btn = ctk.CTkButton(
            outer, text="Tentang Aplikasi", command=self._on_about_click,
            fg_color="transparent", hover_color=BG_SUBTLE, text_color=TEXT_SECONDARY,
            border_width=1, border_color=BORDER,
            corner_radius=RADIUS_CTRL, height=32,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=12)
        )
        self.about_btn.pack(side="bottom", fill="x", pady=(gap, 0))

        log_card = Card(outer)
        log_card.pack(fill="both", expand=True)

        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.pack(fill="x", padx=14, pady=(12, 6))
        
        ctk.CTkLabel(
            log_header, text="Log Proses", text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY[0], size=13, weight="bold")
        ).pack(side="left")

        self.log_box = ctk.CTkTextbox(
            log_card, height=MIN_LOG_BOX_HEIGHT, fg_color=BG_SUBTLE, text_color=TEXT_PRIMARY,
            corner_radius=RADIUS_CTRL,
            font=ctk.CTkFont(family=FONT_MONO[0], size=11),
            wrap="word"
        )
        self.log_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.log_box.configure(state="disabled")

        self._refresh_file_list()

    def _update_log_tags(self):
        is_dark = ctk.get_appearance_mode().lower() == "dark"
        self.log_box.tag_config("tag_success", foreground=SUCCESS_TEXT[1] if is_dark else SUCCESS_TEXT[0])
        self.log_box.tag_config("tag_warn", foreground=WARN_TEXT[1] if is_dark else WARN_TEXT[0])
        self.log_box.tag_config("tag_error", foreground=ERROR_TEXT[1] if is_dark else ERROR_TEXT[0])

    def _check_update_async(self):
        if not self.github_repo:
            self.status_label.configure(text="Update belum dikonfigurasi. Atur SEP_CLEANER_GITHUB_REPO untuk aktifkan cek update.")
            return
        if self.update_check_done and self.is_processing:
            return

        self.update_check_done = True
        self.status_label.configure(text="Memeriksa pembaruan terbaru...")
        thread = threading.Thread(target=self._check_update_worker, daemon=True)
        thread.start()

    def _check_update_worker(self):
        result = check_for_update(APP_VERSION, self.github_repo)
        self.log_queue.put(("update_check", result))

    def _handle_update_result(self, result):
        if result.get("error") and not result.get("available"):
            self.status_label.configure(text=result["error"])
            return

        if result.get("available"):
            message = (
                f"Versi baru tersedia: {result['latest_version']} "
                f"(versi Anda {result['current_version']})"
            )
            self.status_label.configure(text=message)
            if messagebox.askyesno(
                "Update Tersedia",
                f"{message}\n\nBuka halaman release untuk mengunduh update?"
            ):
                webbrowser.open(result.get("html_url") or result.get("download_url") or "")
            return

        self.status_label.configure(text="Aplikasi Anda sudah menggunakan versi terbaru.")

    def _on_about_click(self):
        AboutDialog(self)

    def _on_theme_toggle(self):
        if ctk.get_appearance_mode().lower() == "dark":
            ctk.set_appearance_mode("light")
            self.theme_icon.configure(image=get_icon("sun", size=22))
        else:
            ctk.set_appearance_mode("dark")
            self.theme_icon.configure(image=get_icon("moon", size=22))
        
        self._update_log_tags()

    def _update_mode_switch_text_colors(self):
        current = self.mode_var.get()
        for value, btn in self.mode_switch._buttons_dict.items():
            if value == current:
                btn.configure(text_color="#FFFFFF")
            else:
                btn.configure(text_color=TEXT_PRIMARY)

    def _on_mode_change(self, value):
        self._update_mode_switch_text_colors()
        self.selected_paths = []
        self._refresh_file_list()
        if value == "File":
            self.select_btn.configure(
                text=" Pilih File PDF", 
                image=get_icon("file-pdf", size=16, light_color="#FFFFFF", dark_color="#FFFFFF")
            )
        else:
            self.select_btn.configure(
                text=" Pilih Folder Sumber", 
                image=get_icon("folder-open", size=16, light_color="#FFFFFF", dark_color="#FFFFFF")
            )

    def _on_select_input(self):
        if self.mode_var.get() == "File":
            paths = filedialog.askopenfilenames(
                title="Pilih file PDF",
                filetypes=[("PDF files", "*.pdf")]
            )
            if paths:
                self.selected_paths = list(paths)
        else:
            folder = filedialog.askdirectory(title="Pilih folder berisi PDF")
            if folder:
                self.selected_paths = [
                    os.path.join(folder, f) for f in os.listdir(folder)
                    if f.lower().endswith(".pdf")
                ]
        self._refresh_file_list()

    def _refresh_file_list(self):
        self.file_count_label.configure(text=f"File dipilih ({len(self.selected_paths)})")

        for widget in self.file_list_frame.winfo_children():
            widget.destroy()

        if not self.selected_paths:
            empty = ctk.CTkFrame(self.file_list_frame, fg_color="transparent")
            empty.grid(row=0, column=0, sticky="ew", pady=20)
            
            folder_icon = get_icon("folder-open", size=28)
            ctk.CTkLabel(empty, text="", image=folder_icon).pack()
            ctk.CTkLabel(
                empty, text="Belum ada file dipilih.\nSeret file/folder PDF ke sini.", text_color=TEXT_MUTED,
                font=ctk.CTkFont(family=FONT_FAMILY[0], size=12), justify="center"
            ).pack(pady=(6, 0))
            return

        for i, path in enumerate(self.selected_paths):
            row = ctk.CTkFrame(self.file_list_frame, fg_color=BG_SUBTLE, corner_radius=RADIUS_CTRL)
            row.grid(row=i, column=0, sticky="ew", pady=3)
            row.grid_columnconfigure(0, weight=1)

            left = ctk.CTkFrame(row, fg_color="transparent")
            left.grid(row=0, column=0, sticky="ew", padx=(10, 4), pady=6)
            
            pdf_icon = get_icon("file-pdf", size=14)
            ctk.CTkLabel(left, text="", image=pdf_icon).pack(side="left", padx=(0, 8))
            
            ctk.CTkLabel(
                left, text=os.path.basename(path), text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family=FONT_FAMILY[0], size=12), anchor="w"
            ).pack(side="left", fill="x", expand=True)

            remove_btn = ctk.CTkLabel(
                row, 
                text="", 
                image=get_icon("xmark", size=14, force_color="#EF4444"), 
                cursor="hand2"
            )
            remove_btn.grid(row=0, column=1, padx=(0, 8))
            remove_btn.bind("<Button-1>", lambda e, p=path: self._remove_file(p))

    def _remove_file(self, path):
        if path in self.selected_paths:
            self.selected_paths.remove(path)
        self._refresh_file_list()

    def _on_clear_list(self):
        self.selected_paths = []
        self._refresh_file_list()
        self.progress_bar.set(0)
        self.status_label.configure(text="Daftar file dikosongkan. Siap memilih PDF baru.")
        self._clear_log()

    def _on_select_output(self):
        folder = filedialog.askdirectory(title="Pilih folder tujuan output")
        if folder:
            self.output_folder = folder
            self.output_label.configure(text=folder, text_color=TEXT_PRIMARY)

    def _on_start(self):
        if self.is_processing:
            return
        if not self.selected_paths:
            messagebox.showwarning("Peringatan", "Silakan pilih file atau folder PDF terlebih dahulu.")
            return
        if not self.output_folder:
            messagebox.showwarning("Peringatan", "Silakan pilih folder output terlebih dahulu.")
            return

        self.is_processing = True
        self.start_btn.configure(
            state="disabled", 
            text=" Sedang memproses...",
            image=get_icon("play", size=18, light_color="#FFFFFF", dark_color="#FFFFFF")
        )
        self.progress_bar.set(0)
        self._clear_log()

        thread = threading.Thread(target=self._run_processing, daemon=True)
        thread.start()

    def _run_processing(self):
        total = len(self.selected_paths)
        for i, path in enumerate(self.selected_paths, start=1):
            process_pdf(path, self.output_folder, self.log_queue)
            self.log_queue.put(("progress", i / total))
        self.log_queue.put(("done", None))

    def _poll_log_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "progress":
                    self.progress_bar.set(payload)
                    self.update_idletasks()
                elif kind == "done":
                    self.is_processing = False
                    self.start_btn.configure(
                        state="normal", 
                        text=" Mulai Proses",
                        image=get_icon("play", size=18, light_color="#FFFFFF", dark_color="#FFFFFF")
                    )
                    self.status_label.configure(text="Selesai! Semua file telah diproses.")
                    messagebox.showinfo("Selesai", "Semua file berhasil diproses.")
                elif kind == "update_check":
                    self._handle_update_result(payload)
                else:
                    self._append_log(kind, payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _append_log(self, kind, message):
        prefix = {"success": "✓ ", "warn": "⚠ ", "error": "✗ "}.get(kind, "- ")

        self.log_box.configure(state="normal")
        start_index = self.log_box.index("end-1c")
        self.log_box.insert("end", f"{prefix}{message}\n")
        
        if kind in ("success", "warn", "error"):
            tag_name = f"tag_{kind}"
            self.log_box.tag_add(tag_name, start_index, "end-1c")
            
        self.log_box.configure(state="disabled")
        self.log_box.see("end")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()