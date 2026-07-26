"""Visual language for the Idea deck — SIH pitch specification.

Palette: deep tech blue carries structure and headers, sustainability green is
reserved for positive metrics only, muted orange marks constraints, risks and
the heating setpoint. Ground is a light off-white for projector readability;
body text is dark slate rather than pure black.

Typography note. The brief asks for Montserrat/Inter headers, Open Sans/Roboto
body and JetBrains Mono for code. None of those are installed on the build
machine, and PowerPoint silently substitutes missing faces at export — so the
PDF would not contain them either. The closest installed equivalents are used
instead and named here in one place: install the real families and change these
three constants to switch.
"""
from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

# --------------------------------------------------------------------------- #
# palette
# --------------------------------------------------------------------------- #
PAPER = RGBColor(0xF8, 0xF9, 0xFA)      # primary background
CARD = RGBColor(0xFF, 0xFF, 0xFF)       # raised content panel
CARD_BLUE = RGBColor(0xEF, 0xF4, 0xFF)  # tinted panel, technical content
CARD_GREEN = RGBColor(0xEC, 0xFD, 0xF3) # tinted panel, results
CARD_ORANGE = RGBColor(0xFF, 0xF3, 0xEA)  # tinted panel, risks

BLUE = RGBColor(0x25, 0x63, 0xEB)       # primary accent
BLUE_DEEP = RGBColor(0x1E, 0x3A, 0x8A)  # headers, structural
GREEN = RGBColor(0x16, 0xA3, 0x4A)      # positive metrics ONLY
ORANGE = RGBColor(0xEA, 0x58, 0x0C)     # constraints, risks, heating
INK = RGBColor(0x1E, 0x29, 0x3B)        # body text — dark slate, not black
SLATE = RGBColor(0x47, 0x55, 0x69)      # secondary text
HAIRLINE = RGBColor(0xE2, 0xE8, 0xF0)

WHITE = RGBColor(0xFF, 0xFF, 0xFF)

# Requested -> installed substitute. Swap these after installing the originals.
TITLE_FONT = "Segoe UI"    # requested: Montserrat / Inter
BODY = "Calibri"           # requested: Open Sans / Roboto
MONO = "Consolas"          # requested: JetBrains Mono / Fira Code

# STRICT footer rule from the brief, applied verbatim to every slide.
FOOTER_TEMPLATE = "Echo-Loop[source: 1]@SIH Idea submission- Template{n}"


# --------------------------------------------------------------------------- #
def set_bg(slide, colour: RGBColor = PAPER) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = colour


def send_to_back(shape) -> None:
    """python-pptx appends shapes on top; a panel added after the template's
    text boxes would cover them. spTree starts with nvGrpSpPr + grpSpPr."""
    sp = shape._element
    tree = sp.getparent()
    tree.remove(sp)
    tree.insert(2, sp)


def card(slide, left, top, width, height, *, fill=CARD, line=HAIRLINE, radius=0.03):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(left), Inches(top), Inches(width), Inches(height))
    shape.adjustments[0] = radius
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)
    shape.shadow.inherit = False
    shape.text_frame.text = ""
    send_to_back(shape)
    return shape


def style_title(slide, colour=BLUE_DEEP, size=34) -> None:
    """Bold uppercase header in the title face, per the brief."""
    for sh in slide.shapes:
        if sh.has_text_frame and sh.name.startswith(("Title", "Subtitle")):
            for para in sh.text_frame.paragraphs:
                for run in para.runs:
                    run.text = run.text.upper()
                    run.font.name = TITLE_FONT
                    run.font.size = Pt(size)
                    run.font.bold = True
                    run.font.italic = False
                    run.font.color.rgb = colour


def apply_footer(slide, number: int) -> None:
    """Replace the template footer with the brief's exact string.

    The template's own footer and slide-number placeholders are emptied rather
    than restyled, so no fragment of the original wording can survive.
    """
    for sh in list(slide.shapes):
        if sh.has_text_frame and ("Footer" in sh.name or "Slide Number" in sh.name):
            sh.text_frame.clear()
            for para in sh.text_frame.paragraphs:
                for run in para.runs:
                    run.text = ""

    box = slide.shapes.add_textbox(Inches(0.42), Inches(6.98), Inches(12.5), Inches(0.34))
    tf = box.text_frame
    tf.word_wrap = False
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.CENTER
    run = para.add_run()
    run.text = FOOTER_TEMPLATE.format(n=number)
    run.font.name = BODY
    run.font.size = Pt(9)
    run.font.color.rgb = SLATE
    return box


def badge(slide, left, top, text, *, fill=BLUE, fg=WHITE, size=0.44, fsize=14):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.OVAL, Inches(left), Inches(top), Inches(size), Inches(size))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    shape.shadow.inherit = False
    tf = shape.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.CENTER
    run = para.add_run()
    run.text = text
    run.font.name = TITLE_FONT
    run.font.size = Pt(fsize)
    run.font.bold = True
    run.font.color.rgb = fg
    return shape
