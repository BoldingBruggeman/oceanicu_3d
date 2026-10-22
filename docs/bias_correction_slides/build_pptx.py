"""Build the 3-slide CMIP6 bias-correction deck as a .pptx, mirroring the
published Artifact (https://claude.ai/artifact/1jVr7u6So4w8EnwWZMESbt)."""
import os
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
import copy

DARK = RGBColor(0x1B, 0x2A, 0x33)
LIGHT = RGBColor(0xF6, 0xF3, 0xEC)
AMBER = RGBColor(0xC9, 0x7A, 0x3E)
TEXT_LIGHT = RGBColor(0xF3, 0xEF, 0xE6)
SOFT_LIGHT = RGBColor(0xB7, 0xC4, 0xC9)
MUTED = RGBColor(0x8C, 0x9B, 0xA1)
TEXT_DARK = RGBColor(0x1B, 0x2A, 0x33)
SOFT_DARK = RGBColor(0x4B, 0x5A, 0x62)
CARD_DARK = RGBColor(0x24, 0x37, 0x42)
CARD_DARK2 = RGBColor(0x2E, 0x46, 0x50)
ROW_WHITE = RGBColor(0xFD, 0xFC, 0xF8)
ROW_TAN = RGBColor(0xEF, 0xE9, 0xDA)

SERIF = "Georgia"
SANS = "Arial"

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]

M = Inches(0.85)          # outer margin
CW = prs.slide_width - 2 * M   # usable content width


def add_slide(bg):
    slide = prs.slides.add_slide(BLANK)
    rect = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
    rect.fill.solid()
    rect.fill.fore_color.rgb = bg
    rect.line.fill.background()
    rect.shadow.inherit = False
    return slide


def no_outline(shape):
    shape.line.fill.background()
    shape.shadow.inherit = False


def box(slide, l, t, w, h, fill=None, round_=False):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if round_ else MSO_SHAPE.RECTANGLE, l, t, w, h)
    if round_:
        shp.adjustments[0] = 0.08
    if fill is None:
        shp.fill.background()
        shp.line.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
        no_outline(shp)
    return shp


def text(slide, l, t, w, h, runs, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, line_spacing=1.0):
    """runs: list of (text, size_pt, color, bold, italic, font, spacing_pt)"""
    tb = slide.shapes.add_textbox(l, t, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    first = True
    for para_runs in runs:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.line_spacing = line_spacing
        for (txt, size, color, bold, italic, font, spacing) in para_runs:
            r = p.add_run()
            r.text = txt
            r.font.size = Pt(size)
            r.font.color.rgb = color
            r.font.bold = bold
            r.font.italic = italic
            r.font.name = font
            if spacing:
                rPr = r._r.get_or_add_rPr()
                rPr.set('spc', str(int(spacing * 100)))
    return tb


def R(t, size, color, bold=False, italic=False, font=SANS, spacing=0):
    return (t, size, color, bold, italic, font, spacing)


def footer(slide, left_text, right_text, muted=MUTED):
    text(slide, M, prs.slide_height - Inches(0.62), Inches(9.0), Inches(0.4),
         [[R(left_text, 13, muted)]], anchor=MSO_ANCHOR.MIDDLE)
    text(slide, prs.slide_width - M - Inches(2.0), prs.slide_height - Inches(0.62), Inches(2.0), Inches(0.4),
         [[R(right_text, 13, muted)]], align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)


# ---------------------------------------------------------------- Slide 1
s1 = add_slide(DARK)
y = Inches(2.3)
text(s1, M, y, CW, Inches(0.4),
     [[R("OCEANICU  ·  ATMOSPHERIC FORCING", 13, AMBER, bold=True, spacing=1.5)]])
y += Inches(0.55)
text(s1, M, y, CW, Inches(1.6),
     [[R("CMIP6 Meteo Bias Correction", 44, TEXT_LIGHT, bold=True, font=SERIF)]], line_spacing=1.05)
y += Inches(1.5)
text(s1, M, y, Inches(9.8), Inches(1.1),
     [[R("Quantile Delta Mapping of climate-model forcing against ERA5 — so the "
         "ocean model runs on a real climate signal, not model drift.", 18, SOFT_LIGHT)]],
     line_spacing=1.3)
footer(s1, "ocean-prep · bias_correction_guide.md", "1 / 5")

# ---------------------------------------------------------------- Slide 2
s2 = add_slide(LIGHT)
y = M
text(s2, M, y, CW, Inches(0.35), [[R("THE PROBLEM, THE METHOD", 13, AMBER, bold=True, spacing=1.5)]])
y += Inches(0.42)
text(s2, M, y, CW, Inches(0.7), [[R("Correcting a GCM's systematic bias", 30, TEXT_DARK, bold=True, font=SERIF)]])
y += Inches(0.72)
text(s2, M, y, CW, Inches(0.75),
     [[R("CMIP6 captures large-scale climate well but is consistently biased over a given "
         "region. Left alone, that bias becomes a spurious trend once it forces the ocean model.",
         16, SOFT_DARK)]], line_spacing=1.25)
y += Inches(0.95)

# table
rows = [
    ("Method", "Corrects", "Future trend", "Cost"),
    ("Delta", "Mean only", "Preserved", "Low"),
    ("Quantile Mapping (QM)", "Full distribution", "Distorted", "Medium"),
    ("Quantile Delta Mapping (QDM)", "Full distribution", "Preserved", "Medium–high"),
]
row_fills = [DARK, ROW_WHITE, ROW_TAN, AMBER]
row_text_colors = [TEXT_LIGHT, TEXT_DARK, TEXT_DARK, TEXT_DARK]
col_w = [Emu(int(CW * f)) for f in (0.36, 0.26, 0.22, 0.16)]
row_h = Inches(0.5)
tbl_top = y
for ri, row in enumerate(rows):
    x = M
    for ci, cell in enumerate(row):
        c = box(s2, x, tbl_top + ri * row_h, col_w[ci], row_h, fill=row_fills[ri])
        bold = ri == 3
        text(s2, x + Inches(0.12), tbl_top + ri * row_h, col_w[ci] - Inches(0.24), row_h,
             [[R(cell, 15, row_text_colors[ri], bold=bold)]], anchor=MSO_ANCHOR.MIDDLE)
        x += col_w[ci]
y = tbl_top + len(rows) * row_h + Inches(0.3)

# callout
callout_h = Inches(1.75)
box(s2, M, y, CW, callout_h, fill=DARK, round_=True)
cy = y + Inches(0.18)
text(s2, M + Inches(0.3), cy, CW - Inches(0.6), Inches(0.32),
     [[R("PRODUCTION METHOD — CANNON ET AL. (2015)", 13, AMBER, bold=True, spacing=1.0)]])
cy += Inches(0.36)
text(s2, M + Inches(0.3), cy, CW - Inches(0.6), Inches(0.55),
     [[R("At each quantile q, apply the reference-minus-historical delta to the future value: "
         "today's distribution is corrected while the GCM's own future climate-change signal "
         "survives untouched.", 14, TEXT_LIGHT)]], line_spacing=1.2)
cy += Inches(0.58)
text(s2, M + Inches(0.3), cy, CW - Inches(0.6), Inches(0.3),
     [[R("future_bc = future + [ v_ref(q) − v_hist(q) ]   — additive (temperature)", 13, SOFT_LIGHT)]])
cy += Inches(0.3)
text(s2, M + Inches(0.3), cy, CW - Inches(0.6), Inches(0.3),
     [[R("future_bc = future × [ v_ref(q) / v_hist(q) ]   — multiplicative (precipitation)", 13, SOFT_LIGHT)]])

footer(s2, "Cannon, Sobie & Murdock (2015), J. Climate 28(17)", "2 / 5", muted=SOFT_DARK)

# ---------------------------------------------------------------- Slide 3 (real example)
s2b = add_slide(LIGHT)
y = M
text(s2b, M, y, CW, Inches(0.35), [[R("REAL EXAMPLE", 13, AMBER, bold=True, spacing=1.5)]])
y += Inches(0.42)
text(s2b, M, y, CW, Inches(0.7),
     [[R("Does it actually work? One point, one variable", 30, TEXT_DARK, bold=True, font=SERIF)]])
y += Inches(0.85)

img_dir = os.path.dirname(os.path.abspath(__file__))
img_path = os.path.join(img_dir, "tas_point_example.png")
img_w = Inches(8.6)
img_h = Emu(int(img_w * 892 / 1610))
img_left = (prs.slide_width - img_w) / 2
s2b.shapes.add_picture(img_path, img_left, y, width=img_w, height=img_h)

footer(s2b, "GFDL-ESM4, historical, 2009–2014 — real production output, not a mock-up", "3 / 5",
       muted=SOFT_DARK)

# ---------------------------------------------------------------- Slide 4 (future example)
s2c = add_slide(LIGHT)
y = M
text(s2c, M, y, CW, Inches(0.35), [[R("REAL EXAMPLE, FUTURE PERIOD", 13, AMBER, bold=True, spacing=1.5)]])
y += Inches(0.42)
text(s2c, M, y, CW, Inches(0.7),
     [[R("No ERA5 in the future — so what does correction do there?", 26, TEXT_DARK, bold=True, font=SERIF)]])
y += Inches(0.8)

img2_path = os.path.join(img_dir, "tas_future_example.png")
img2_w = Inches(7.3)
img2_h = Emu(int(img2_w * 1054 / 1715))
img2_left = (prs.slide_width - img2_w) / 2
s2c.shapes.add_picture(img2_path, img2_left, y, width=img2_w, height=img2_h)

footer(s2c, "GFDL-ESM4, SSP1-2.6, 2015–2099 — real production output", "4 / 5",
       muted=SOFT_DARK)

# ---------------------------------------------------------------- Slide 5
s3 = add_slide(DARK)
y = M
text(s3, M, y, CW, Inches(0.35), [[R("WORKFLOW", 13, AMBER, bold=True, spacing=1.5)]])
y += Inches(0.42)
text(s3, M, y, CW, Inches(0.7),
     [[R("From ERA5 and CMIP6 to model-ready forcing", 30, TEXT_LIGHT, bold=True, font=SERIF)]])
y += Inches(0.85)

card_gap = Inches(0.3)
card_w = Emu(int((CW - 2 * card_gap) / 3))
card_h = Inches(1.05)
inputs = [
    ("ERA5 (ref)", "Hourly reanalysis, 1990–2014 calibration window"),
    ("CMIP6 historical (hist)", "Same model, same window, Amon"),
    ("CMIP6 future (SSP)", "2020–2099 projection to correct"),
]
x = M
for title, sub in inputs:
    box(s3, x, y, card_w, card_h, fill=CARD_DARK, round_=True)
    text(s3, x + Inches(0.25), y + Inches(0.15), card_w - Inches(0.5), Inches(0.35),
         [[R(title, 16, TEXT_LIGHT, bold=True)]])
    text(s3, x + Inches(0.25), y + Inches(0.52), card_w - Inches(0.5), Inches(0.45),
         [[R(sub, 13, SOFT_LIGHT)]], line_spacing=1.15)
    x += card_w + card_gap
y += card_h + Inches(0.18)

arrow = s3.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, prs.slide_width / 2 - Inches(0.22), y, Inches(0.44), Inches(0.4))
arrow.fill.solid()
arrow.fill.fore_color.rgb = AMBER
no_outline(arrow)
y += Inches(0.55)

big_h = Inches(1.5)
box(s3, M, y, CW, big_h, fill=CARD_DARK2, round_=True)
text(s3, M + Inches(0.3), y + Inches(0.18), CW - Inches(0.6), Inches(0.35),
     [[R("bias_correct_forcings.py — per calendar month, QDM", 16, TEXT_LIGHT, bold=True)]])
chip_gap = Inches(0.25)
chip_w = Emu(int((CW - Inches(0.6) - chip_gap) / 2))
chip_h = Inches(0.78)
cx = M + Inches(0.3)
cy2 = y + Inches(0.62)
for label, detail in [
    ("[correct]", "daily, bias-corrected — {var}_bc_{year}.nc"),
    ("[disaggregate]", "+ ERA5 diurnal cycle — hourly {var}_bc_disagg_{year}.nc"),
]:
    box(s3, cx, cy2, chip_w, chip_h, fill=DARK, round_=True)
    text(s3, cx + Inches(0.2), cy2 + Inches(0.1), chip_w - Inches(0.4), Inches(0.3),
         [[R(label, 14, AMBER, bold=True)]])
    text(s3, cx + Inches(0.2), cy2 + Inches(0.4), chip_w - Inches(0.4), Inches(0.35),
         [[R(detail, 12, SOFT_LIGHT)]], line_spacing=1.1)
    cx += chip_w + chip_gap
y += big_h + Inches(0.25)

stat_gap = Inches(0.3)
stat_w = Emu(int((CW - 2 * stat_gap) / 3))
stat_h = Inches(1.05)
stats = [
    ("1990–2014", "Calibration window — fixed by CMIP6's historical protocol"),
    ("≈100 quantiles", "Daily data, ≈900 samples per calendar month"),
    ("Exact", "Daily mean preserved — diurnal anomalies sum to zero"),
]
x = M
for big, sub in stats:
    box(s3, x, y, stat_w, stat_h, fill=CARD_DARK, round_=True)
    text(s3, x + Inches(0.25), y + Inches(0.12), stat_w - Inches(0.5), Inches(0.4),
         [[R(big, 20, TEXT_LIGHT, bold=True)]])
    text(s3, x + Inches(0.25), y + Inches(0.54), stat_w - Inches(0.5), Inches(0.48),
         [[R(sub, 12, SOFT_LIGHT)]], line_spacing=1.15)
    x += stat_w + stat_gap

footer(s3, "QA: bc diagnostics — calibration maps · seasonal cycle · trend · monthly breakdown", "5 / 5")

out_path = os.path.join(img_dir, "CMIP6_bias_correction_slides.pptx")
prs.save(out_path)
print("saved:", out_path)
