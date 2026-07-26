"""Visual language for the Idea deck.

Palette is drawn from the project's own identity rather than picked off a
shelf: ink navy is the ground an HVAC control panel lives on, ember is the
thermal accent, and the two carry through the dashboard, the figures and the
slides so the whole submission reads as one artifact.

Structure is a dark/light sandwich — navy opening and closing slides with warm
content slides between them. The repeated motif is a tinted content card with
a rounded corner; deliberately NOT an accent stripe or a rule under the title,
which read as generated filler.
"""
from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

# --------------------------------------------------------------------------- #
# palette
# --------------------------------------------------------------------------- #
NAVY = RGBColor(0x0E, 0x1A, 0x2B)      # dominant ground, dark slides
NAVY_SOFT = RGBColor(0x1B, 0x2A, 0x3F)  # raised panel on navy
PAPER = RGBColor(0xFA, 0xF7, 0xF2)      # content-slide ground, warm not grey
CARD = RGBColor(0xF2, 0xEC, 0xE2)       # tinted content card
CARD_COOL = RGBColor(0xE9, 0xEF, 0xF7)  # cool variant, for contrast blocks

INK = RGBColor(0x12, 0x1B, 0x2B)        # body text on light
SLATE = RGBColor(0x4A, 0x57, 0x68)      # secondary text on light
CHALK = RGBColor(0xF4, 0xF7, 0xFA)      # body text on navy
MIST = RGBColor(0xA8, 0xBA, 0xCE)       # secondary text on navy

EMBER = RGBColor(0xD9, 0x54, 0x1F)      # the accent
EMBER_LIGHT = RGBColor(0xF2, 0x8A, 0x4C)  # accent on navy (lifted for contrast)
TEAL = RGBColor(0x0D, 0x7A, 0x4F)       # "good" figures on light
TEAL_LIGHT = RGBColor(0x3F, 0xC1, 0x8B)  # "good" figures on navy

TITLE_FONT = "Cambria"                  # safe-list serif, more character than TNR
BODY = "Calibri"                        # safe-list sans, warmer than Arial


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def set_bg(slide, colour: RGBColor) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = colour


def send_to_back(shape) -> None:
    """Move a shape behind everything else on the slide.

    python-pptx appends new shapes on top, so a card added after the template's
    text boxes would cover them. spTree children start with nvGrpSpPr and
    grpSpPr, hence index 2.
    """
    sp = shape._element
    tree = sp.getparent()
    tree.remove(sp)
    tree.insert(2, sp)


def card(slide, left, top, width, height, *, fill=CARD, line=None, radius=0.035):
    """The deck's repeated motif: a soft tinted panel behind a content block."""
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


def style_title(slide, colour=INK, size=34) -> None:
    """Restyle the template's title placeholder without moving it."""
    for sh in slide.shapes:
        if sh.has_text_frame and sh.name.startswith("Title"):
            for para in sh.text_frame.paragraphs:
                for run in para.runs:
                    run.font.name = TITLE_FONT
                    run.font.size = Pt(size)
                    run.font.bold = True
                    run.font.color.rgb = colour
                    run.font.italic = False


def style_chrome(slide, colour) -> None:
    """Footer and slide number, so they stay legible on a navy ground."""
    for sh in slide.shapes:
        if not sh.has_text_frame:
            continue
        if "Footer" in sh.name or "Slide Number" in sh.name:
            for para in sh.text_frame.paragraphs:
                for run in para.runs:
                    run.font.color.rgb = colour
                    run.font.size = Pt(10)
                    run.font.name = BODY


def badge(slide, left, top, text, *, fill=EMBER, fg=RGBColor(0xFF, 0xFF, 0xFF),
          size=0.42, fsize=13):
    """Small filled marker used to index sections — the secondary motif."""
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
    run.font.name = BODY
    run.font.size = Pt(fsize)
    run.font.bold = True
    run.font.color.rgb = fg
    return shape
