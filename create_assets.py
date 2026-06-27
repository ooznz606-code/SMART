# -*- coding: utf-8 -*-
"""
create_assets.py — يولّد الأيقونة وشاشة التحميل لـ SmartTrader
تشغيل: python create_assets.py
المخرجات: assets/icon.ico  assets/icon.png  assets/splash.png  assets/app_logo.png
"""
import os, math
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── الألوان ──────────────────────────────────────────────────────────────────
NAVY       = (11,  31,  58,  255)
NAVY_LIGHT = (18,  48,  88,  255)
ELECTRIC   = (0,  174, 239, 255)
SILVER     = (192, 192, 192, 255)
GREEN      = (0,  200,  83, 255)
WHITE      = (255, 255, 255, 255)
GOLD       = (255, 200,  60, 255)
DARK_BG    = (7,   17,  35,  255)
TRANSPARENT= (0,    0,   0,   0)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(OUT, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
def _draw_icon(size: int) -> Image.Image:
    """يرسم أيقونة SmartTrader بحجم مُعطى (مع 4× oversample للحدة)."""
    S = size * 4          # رسم بحجم 4× ثم تصغير للحدة
    img  = Image.new("RGBA", (S, S), TRANSPARENT)
    draw = ImageDraw.Draw(img)

    cx, cy = S // 2, S // 2
    R = int(S * 0.46)

    # ── 1. درع خلفي (ظل) ─────────────────────────────────────────────────
    def shield_poly(ox, oy, r, extra=0):
        """مضلع درع Pentagon"""
        pts = []
        # رأس الدرع (نصف دائرة علوية)
        for a in range(200, 340, 4):
            rad = math.radians(a)
            pts.append((ox + (r + extra) * math.cos(rad),
                        oy + (r + extra) * math.sin(rad) * 0.75))
        # الجانبان
        pts.append((ox + r + extra,  oy + r * 0.28))
        pts.append((ox + r + extra,  oy + r * 0.65))
        # القاع المدبب
        pts.append((ox,              oy + r + extra))
        pts.append((ox - r - extra,  oy + r * 0.65))
        pts.append((ox - r - extra,  oy + r * 0.28))
        return [(int(x), int(y)) for x, y in pts]

    # ظل خارجي (glow أزرق)
    glow = Image.new("RGBA", (S, S), TRANSPARENT)
    gd   = ImageDraw.Draw(glow)
    for i in range(12, 0, -1):
        alpha = int(40 * (1 - i / 12))
        color = (0, 174, 239, alpha)
        gd.polygon(shield_poly(cx, cy - S*0.02, R + i*3), fill=color)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=S * 0.015))
    img.alpha_composite(glow)

    # ── 2. جسم الدرع ─────────────────────────────────────────────────────
    # طبقة داكنة داخلية
    draw.polygon(shield_poly(cx, cy - S*0.02, R),     fill=NAVY)
    draw.polygon(shield_poly(cx, cy - S*0.02, R - 8), fill=NAVY_LIGHT)

    # حافة كهربائية
    draw.line([(cx - R, cy + R*0.28), (cx - R, cy + R*0.65),
               (cx, cy + R + 10),
               (cx + R, cy + R*0.65), (cx + R, cy + R*0.28)],
              fill=ELECTRIC, width=max(2, S//90))

    # قوس علوي للحافة
    draw.arc([cx - R, cy - R - S*0.02,
              cx + R, cy + R*0.35 - S*0.02],
             start=200, end=340, fill=ELECTRIC, width=max(2, S//90))

    # ── 3. شمعة تداول في المنتصف ──────────────────────────────────────────
    cw = int(R * 0.28)    # عرض جسم الشمعة
    ch = int(R * 0.55)    # ارتفاع جسم الشمعة
    wk = int(R * 0.18)    # طول الفتيل

    # شمعة صاعدة (خضراء)
    bx1, bx2 = cx - cw//2, cx + cw//2
    by1 = int(cy - ch*0.3)
    by2 = int(cy + ch*0.3)

    # فتيل علوي
    draw.line([(cx, by1 - wk), (cx, by1)], fill=GREEN, width=max(2, S//100))
    # فتيل سفلي
    draw.line([(cx, by2), (cx, by2 + wk)], fill=GREEN, width=max(2, S//100))
    # جسم الشمعة مع حافة
    draw.rectangle([bx1, by1, bx2, by2], fill=GREEN)
    draw.rectangle([bx1, by1, bx2, by1 + (by2-by1)//4], fill=(0, 220, 100, 255))

    # شمعة ثانوية (فضية، أصغر، خلف)
    sw = int(cw * 0.7)
    off = int(R * 0.38)
    # شمعة يمين (أصغر، هابطة/فضية)
    rx1, rx2 = cx + off - sw//2, cx + off + sw//2
    ry1, ry2 = int(cy - ch*0.18), int(cy + ch*0.22)
    draw.line([(cx+off, ry1-int(wk*0.6)), (cx+off, ry1)], fill=SILVER, width=max(1, S//130))
    draw.line([(cx+off, ry2), (cx+off, ry2+int(wk*0.6))], fill=SILVER, width=max(1, S//130))
    draw.rectangle([rx1, ry1, rx2, ry2], fill=(120, 120, 140, 220))

    # ── 4. خط ترند صاعد ──────────────────────────────────────────────────
    tx0 = int(cx - R * 0.72)
    ty0 = int(cy + R * 0.45)
    tx1 = int(cx - R * 0.20)
    ty1 = int(cy + R * 0.15)
    tx2 = int(cx + R * 0.10)
    ty2 = int(cy + R * 0.22)
    tx3 = int(cx + R * 0.72)
    ty3 = int(cy - R * 0.08)

    # ظل خط الترند
    for off_ in range(3, 0, -1):
        draw.line([(tx0, ty0+off_), (tx1, ty1+off_), (tx2, ty2+off_), (tx3, ty3+off_)],
                  fill=(0, 200, 83, 60), width=max(1, S//80) + off_)
    draw.line([(tx0, ty0), (tx1, ty1), (tx2, ty2), (tx3, ty3)],
              fill=GREEN, width=max(2, S//80))

    # نقاط على خط الترند
    for px, py in [(tx1, ty1), (tx2, ty2), (tx3, ty3)]:
        r2 = max(4, S // 60)
        draw.ellipse([px-r2, py-r2, px+r2, py+r2], fill=GREEN)
        draw.ellipse([px-r2+2, py-r2+2, px+r2-2, py+r2-2], fill=(0, 230, 100, 255))

    # ── 5. بريق ركن علوي ─────────────────────────────────────────────────
    spark_r = max(3, S // 55)
    sx, sy  = cx + int(R * 0.52), cy - int(R * 0.60)
    draw.ellipse([sx-spark_r, sy-spark_r, sx+spark_r, sy+spark_r],
                 fill=WHITE)

    # ── 6. تصغير للحجم الحقيقي مع anti-alias ──────────────────────────
    return img.resize((size, size), Image.LANCZOS)


# ─────────────────────────────────────────────────────────────────────────────
def create_icon():
    """يولّد icon.ico بكل الأحجام المطلوبة"""
    import io as _io, struct as _struct

    sizes = [16, 32, 48, 64, 128, 256]
    frames_rgba = []
    for s in sizes:
        frame = _draw_icon(s)
        bg = Image.new("RGBA", (s, s), (11, 31, 58, 255))
        bg.alpha_composite(frame)
        frames_rgba.append(bg)
        if s == 256:
            bg.save(os.path.join(OUT, "icon.png"), "PNG")
            print(f"  OK icon.png ({s}x{s})")

    # بناء ICO يدوياً بـ BMP (أكثر توافقاً مع Windows و PyInstaller)
    def _to_bmp_blob(img):
        """تحويل RGBA Image إلى BMP DIB blob مع AND mask لـ ICO"""
        w, h = img.size
        px = list(img.getdata())
        # BITMAPINFOHEADER (40 bytes) — الارتفاع ضعف الحقيقي (يشمل AND mask)
        dib  = _struct.pack("<IiiHHIIiiII", 40, w, h*2, 1, 32, 0, 0, 0, 0, 0, 0)
        # بيانات البكسل BGRA من الأسفل للأعلى
        rows = []
        for row in range(h-1, -1, -1):
            for col in range(w):
                r2,g2,b2,a = px[row*w + col]
                rows.append(_struct.pack("BBBB", b2, g2, r2, a))
        # AND mask (كلها صفر = شفاف كلياً)
        mask_row_bytes = ((w + 31) // 32) * 4
        mask = b'\x00' * (mask_row_bytes * h)
        return dib + b''.join(rows) + mask

    blobs = [_to_bmp_blob(img) for img in frames_rgba]
    n = len(sizes)
    header = 6 + 16 * n
    offsets, off = [], header
    for b in blobs:
        offsets.append(off); off += len(b)

    ico = _io.BytesIO()
    ico.write(_struct.pack("<HHH", 0, 1, n))
    for i, s in enumerate(sizes):
        w = s if s < 256 else 0
        ico.write(_struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(blobs[i]), offsets[i]))
    for b in blobs:
        ico.write(b)

    with open(os.path.join(OUT, "icon.ico"), "wb") as f:
        f.write(ico.getvalue())
    print(f"  OK icon.ico ({n} احجام BMP, {len(ico.getvalue())//1024}KB)")


# ─────────────────────────────────────────────────────────────────────────────
def create_splash():
    """يولّد splash.png (600×350) وapp_logo.png"""
    W, H = 600, 350
    img  = Image.new("RGBA", (W, H), (7, 17, 35, 255))
    draw = ImageDraw.Draw(img)

    # ── خلفية متدرجة ─────────────────────────────────────────────────────
    for y in range(H):
        r = int(7  + (18  - 7)  * y / H)
        g = int(17 + (40  - 17) * y / H)
        b = int(35 + (80  - 35) * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # ── خطوط grid خفيفة ──────────────────────────────────────────────────
    for x in range(0, W, 40):
        draw.line([(x, 0), (x, H)], fill=(255, 255, 255, 8))
    for y in range(0, H, 30):
        draw.line([(0, y), (W, y)], fill=(255, 255, 255, 8))

    # ── شمعات تزيينية في الخلفية ─────────────────────────────────────────
    candles = [(50, 200, 30, 80, True), (110, 180, 30, 100, False),
               (490, 190, 30, 90, True), (545, 175, 30, 110, True)]
    for cx2, cy2, cw2, ch2, up in candles:
        color = (0, 180, 70, 35) if up else (180, 50, 50, 35)
        draw.rectangle([cx2-cw2//2, cy2-ch2//2, cx2+cw2//2, cy2+ch2//2], fill=color)
        draw.line([(cx2, cy2-ch2//2-20), (cx2, cy2-ch2//2)], fill=(*color[:3], 25), width=2)
        draw.line([(cx2, cy2+ch2//2), (cx2, cy2+ch2//2+20)], fill=(*color[:3], 25), width=2)

    # ── خط ترند تزييني ────────────────────────────────────────────────────
    trend_pts = [(30, 290), (120, 240), (200, 260), (300, 190),
                 (380, 210), (470, 160), (580, 130)]
    draw.line(trend_pts, fill=(0, 200, 83, 40), width=2)

    # ── أيقونة مصغرة في اليسار ───────────────────────────────────────────
    logo = _draw_icon(96)
    img.alpha_composite(logo, dest=(W//2 - 48, 35))

    # ── نصوص ─────────────────────────────────────────────────────────────
    # نحاول تحميل خط النظام أو نستخدم الافتراضي
    def _font(size):
        for path in [
            "C:/Windows/Fonts/Segoeui.ttf",
            "C:/Windows/Fonts/Arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
        ]:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    # SmartTrader
    fnt_title = _font(52)
    title_txt = "SmartTrader"
    bbox = draw.textbbox((0, 0), title_txt, font=fnt_title)
    tw = bbox[2] - bbox[0]
    tx = (W - tw) // 2
    ty = 148
    # ظل نص
    draw.text((tx+2, ty+2), title_txt, fill=(0, 50, 120, 180), font=fnt_title)
    # نص أساسي أبيض
    draw.text((tx, ty), title_txt, fill=WHITE, font=fnt_title)
    # تلوين حرف "S" بالأزرق
    draw.text((tx, ty), "S", fill=(0, 174, 239, 255), font=fnt_title)

    # Subtitle
    fnt_sub = _font(18)
    sub_txt = "Professional Trading Platform"
    bbox2 = draw.textbbox((0, 0), sub_txt, font=fnt_sub)
    sx2 = (W - (bbox2[2] - bbox2[0])) // 2
    draw.text((sx2, ty + 68), sub_txt,
              fill=(0, 174, 239, 200), font=fnt_sub)

    # فاصل
    draw.line([(W//2 - 120, 280), (W//2 + 120, 280)],
              fill=(0, 174, 239, 80), width=1)

    # Loading text
    fnt_load = _font(13)
    load_txt = "Initializing..."
    bbox3 = draw.textbbox((0, 0), load_txt, font=fnt_load)
    lx = (W - (bbox3[2] - bbox3[0])) // 2
    draw.text((lx, 292), load_txt, fill=(120, 160, 200, 200), font=fnt_load)

    # Version
    fnt_ver = _font(11)
    draw.text((W - 90, H - 20), "v1.0.0",
              fill=(80, 110, 150, 180), font=fnt_ver)

    # ── حافة خارجية ──────────────────────────────────────────────────────
    draw.rectangle([0, 0, W-1, H-1], outline=(0, 174, 239, 60), width=1)
    draw.rectangle([1, 1, W-2, H-2], outline=(0, 100, 160, 30), width=1)

    img.save(os.path.join(OUT, "splash.png"), "PNG")
    print("  ✅ splash.png (600×350)")

    # ── app_logo.png (512×512) ────────────────────────────────────────────
    logo_big = _draw_icon(512)
    bg = Image.new("RGBA", (512, 512), (11, 31, 58, 255))
    bg.alpha_composite(logo_big)
    bg.save(os.path.join(OUT, "app_logo.png"), "PNG")
    print("  ✅ app_logo.png (512×512)")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🎨 توليد أصول SmartTrader...\n")
    create_icon()
    create_splash()
    print(f"\n✅ جميع الملفات محفوظة في: {OUT}\n")
