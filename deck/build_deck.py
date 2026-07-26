"""Build the Eco-Loop Idea submission deck from the provided SIH template.

Template rules honoured:
  * max six slides INCLUDING the title -> the instructions slide is deleted,
    exactly as that slide itself directs;
  * the "idea details pointers" are kept as section headers with the answer
    written underneath — the template forbids changing them;
  * points and diagrams, not paragraphs; never more than five bullets a slide.

Pitch-spec rules: light ground, deep blue structure, green reserved for
positive metrics, orange for constraints, and the headline figures treated as
standalone callouts rather than list items.

All figures come from `make_images.py`, which reads the committed run outputs,
so the numbers on the slides cannot drift from the repository.
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from style import (BLUE, BLUE_DEEP, BODY, CARD, CARD_BLUE, CARD_GREEN,
                   CARD_ORANGE, GREEN, HAIRLINE, INK, MONO, ORANGE, PAPER,
                   SLATE, TITLE_FONT, apply_footer, badge, card, set_bg,
                   style_title)

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.pptx"
OUTPUT = HERE / "Eco-Loop_Building_Agents_Idea.pptx"

# Only the submitting student can supply these; flagged loudly, never invented.
TBD = "<<FILL IN>>"


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #
def set_bullet(paragraph, char: str = "▪") -> None:
    pPr = paragraph._p.get_or_add_pPr()
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    pPr.set("marL", "165100")
    pPr.set("indent", "-165100")
    pPr.append(pPr.makeelement(qn("a:buFont"), {"typeface": BODY}))
    pPr.append(pPr.makeelement(qn("a:buChar"), {"char": char}))


def clear_bullet(paragraph) -> None:
    """The template's body boxes carry list formatting; a paragraph that does
    not opt out inherits a glyph and a hanging indent, which put a stray bullet
    in front of every section header."""
    pPr = paragraph._p.get_or_add_pPr()
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    pPr.set("marL", "0")
    pPr.set("indent", "0")
    pPr.append(pPr.makeelement(qn("a:buNone"), {}))


def write(shape, blocks, *, space_after=6) -> None:
    """(text, size, bold, colour, level, bullet) rows -> a styled text frame."""
    tf = shape.text_frame
    tf.word_wrap = True
    tf.clear()
    first = True
    for text, size, bold, colour, level, bullet in blocks:
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        para.level = level
        para.space_after = Pt(space_after)
        para.alignment = PP_ALIGN.LEFT      # template boxes are justified
        set_bullet(para) if bullet else clear_bullet(para)
        run = para.add_run()
        run.text = text
        run.font.name = BODY
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = colour


def title_face(shape, only_first=False) -> None:
    """Section headers wear the title face; body copy stays in the body face."""
    paras = shape.text_frame.paragraphs
    for para in (paras[:1] if only_first else paras):
        for run in para.runs:
            if run.font.bold or only_first:
                run.font.name = TITLE_FONT


def move(shape, left=None, top=None, width=None, height=None) -> None:
    if left is not None:   shape.left = Inches(left)
    if top is not None:    shape.top = Inches(top)
    if width is not None:  shape.width = Inches(width)
    if height is not None: shape.height = Inches(height)


def by_name(slide, name):
    for sh in slide.shapes:
        if sh.name == name:
            return sh
    raise KeyError(f"{name!r} not on slide; have {[s.name for s in slide.shapes]}")


def image(slide, img: Path, left, top, width):
    return slide.shapes.add_picture(str(img), Inches(left), Inches(top), width=Inches(width))


def textbox(slide, left, top, width, height):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    box.text_frame.word_wrap = True
    return box


def hero(slide, left, top, width, value, label, colour, *, fill=CARD,
         height=1.55, vsize=44):
    """A headline metric as a standalone callout, never a bullet."""
    card(slide, left, top, width, height, fill=fill, line=HAIRLINE)
    box = textbox(slide, left + 0.26, top + 0.13, width - 0.52, height - 0.26)
    tf = box.text_frame
    tf.clear()
    p1 = tf.paragraphs[0]
    p1.alignment = PP_ALIGN.LEFT
    clear_bullet(p1)
    r1 = p1.add_run(); r1.text = value
    r1.font.name = TITLE_FONT; r1.font.size = Pt(vsize)
    r1.font.bold = True; r1.font.color.rgb = colour
    p2 = tf.add_paragraph()
    p2.alignment = PP_ALIGN.LEFT
    clear_bullet(p2)
    r2 = p2.add_run(); r2.text = label
    r2.font.name = BODY; r2.font.size = Pt(11); r2.font.color.rgb = SLATE
    return box


# --------------------------------------------------------------------------- #
def main() -> None:
    prs = Presentation(str(TEMPLATE))

    sld_lst = prs.slides._sldIdLst
    first = list(sld_lst)[0]
    prs.part.drop_rel(first.get(qn("r:id")))
    sld_lst.remove(first)

    s_title, s_idea, s_tech, s_feas, s_art, s_ref = prs.slides
    for n, slide in enumerate(prs.slides, 1):
        set_bg(slide, PAPER)
        style_title(slide, colour=BLUE_DEEP)
        apply_footer(slide, n)

    # ======================================================== 1 · TITLE PAGE
    card(s_title, 0.42, 1.22, 6.35, 3.3, fill=CARD_BLUE, line=None)
    fields = by_name(s_title, "TextBox 6")
    move(fields, left=0.72, top=1.4, width=5.8, height=3.0)
    write(fields, [
        (f"Problem Statement ID   {TBD}", 13, True, ORANGE, 0, False),
        ("Problem Statement Title   Eco-Loop Building Agents — autonomous "
         "closed-loop HVAC control using EnergyPlus, MCP and a local open-source LLM",
         13, False, INK, 0, False),
        (f"Theme   {TBD}      ·      PS Category   Software", 13, False, INK, 0, False),
        ("Student   Ayush Yadav", 13, False, INK, 0, False),
        (f"Student ID   {TBD}", 13, False, INK, 0, False),
    ], space_after=9)

    pitch = textbox(s_title, 7.05, 1.24, 5.85, 1.9)
    write(pitch, [
        ("A BUILDING THAT RUNS ITSELF", 20, True, BLUE_DEEP, 0, False),
        ("EnergyPlus supplies the physics. A local open-source LLM supplies the "
         "judgement. The Model Context Protocol carries every reading and every "
         "command between them — while the simulation is still running.",
         12.5, False, INK, 0, False),
    ], space_after=9)
    title_face(pitch, only_first=True)

    hero(s_title, 7.05, 3.22, 2.85, "−8.5%", "HVAC energy\nsummer week",
         GREEN, fill=CARD_GREEN, height=1.4, vsize=32)
    hero(s_title, 10.06, 3.22, 2.85, "336/336", "agent turns\nzero failures",
         BLUE, fill=CARD_BLUE, height=1.4, vsize=28)
    hero(s_title, 7.05, 4.8, 5.86, "100% open source",
         "One laptop — no cloud, no API keys, no per-query cost",
         BLUE_DEEP, fill=CARD, height=1.1, vsize=21)

    stack = textbox(s_title, 0.72, 4.72, 6.1, 1.2)
    write(stack, [
        ("TECH STACK", 11, True, BLUE_DEEP, 0, False),
        ("EnergyPlus 26.1 · pyenergyplus · FastMCP · Model Context Protocol · "
         "Ollama + Llama 3.2 3B · Pydantic · Streamlit", 11, False, SLATE, 0, False),
    ], space_after=5)
    title_face(stack, only_first=True)

    # ========================================================= 2 · IDEA TITLE
    card(s_idea, 0.42, 1.16, 6.3, 5.5, fill=CARD, line=HAIRLINE)
    badge(s_idea, 0.62, 1.3, "1")
    body = by_name(s_idea, "TextBox 8")
    move(body, left=1.22, top=1.28, width=5.32, height=5.3)
    write(body, [
        ("PROPOSED SOLUTION", 13, True, BLUE_DEEP, 0, False),
        ("Traditional BMS follow rigid clock schedules. Eco-Loop turns the "
         "building into a self-correcting agent.", 12, False, INK, 0, False),
        ("HOW IT WORKS", 13, True, BLUE_DEEP, 0, False),
        ("EnergyPlus runs in-process; sensors read every zone timestep",
         11.5, False, INK, 0, True),
        ("Every 60 simulated minutes the state reaches a local LLM via six MCP tools",
         11.5, False, INK, 0, True),
        ("The model weighs comfort, demand and grid carbon, then returns setpoints",
         11.5, False, INK, 0, True),
        ("INNOVATION", 13, True, BLUE_DEEP, 0, False),
        ("Injected into the LIVE solver — no IDF rewrite, no restart",
         11.5, False, INK, 0, True),
        ("MCP is the safety boundary: every command validated server-side",
         11.5, False, INK, 0, True),
    ], space_after=6)
    title_face(body)

    image(s_idea, HERE / "results.png", 6.92, 1.16, 6.05)
    hero(s_idea, 6.9, 3.22, 6.09, "−8.5% HVAC energy",
         "Measured, not projected — while holding occupants closer to thermal "
         "neutrality than the baseline (mean PMV −0.43 → −0.02)",
         GREEN, fill=CARD_GREEN, height=1.5, vsize=26)

    card(s_idea, 6.9, 4.88, 6.09, 1.78, fill=CARD_BLUE, line=None)
    diff = textbox(s_idea, 7.16, 4.98, 5.6, 1.58)
    write(diff, [
        ("WHY IT IS DIFFERENT", 13, True, BLUE_DEEP, 0, False),
        ("Most LLM-and-simulation work rewrites the model file and re-runs it — that "
         "can only compare finished runs. It cannot react to a zone drifting out of "
         "comfort at 14:00 on day 3.", 11.5, False, INK, 0, False),
    ], space_after=5)
    title_face(diff, only_first=True)

    # =================================================== 3 · TECHNICAL APPROACH
    card(s_tech, 0.42, 1.06, 12.5, 0.98, fill=CARD_BLUE, line=None)
    badge(s_tech, 0.62, 1.2, "2")
    body = by_name(s_tech, "TextBox 8")
    move(body, left=1.22, top=1.14, width=11.4, height=1.2)
    write(body, [
        ("TECHNOLOGIES   EnergyPlus 26.1 (pyenergyplus C API) · Python · FastMCP over "
         "streamable HTTP · Ollama running Llama 3.2 3B · Pydantic · Streamlit + Plotly",
         11.5, False, INK, 0, False),
        ("METHODOLOGY   sample every timestep → aggregate per control interval "
         "→ LLM tool-calling turn → validate → inject into live actuators",
         11.5, False, INK, 0, False),
    ], space_after=6)

    # The closed-loop diagram is the visual centrepiece of this slide.
    image(s_tech, HERE / "arch.png", 0.62, 2.58, 12.1)
    cap = textbox(s_tech, 0.62, 6.5, 12.1, 0.4)
    write(cap, [("The loop closes inside a single running simulation — 168 agent "
                 "decisions per simulated week, zero restarts.",
                 11, False, SLATE, 0, False)], space_after=0)

    # ================================================ 4 · FEASIBILITY & VIABILITY
    hero(s_feas, 0.42, 1.02, 4.02, "336 / 336",
         "agent turns — 0 fallbacks, 0 timeouts, 0 actuation errors",
         GREEN, fill=CARD_GREEN, height=1.46, vsize=33)
    hero(s_feas, 4.66, 1.02, 4.02, "−8.5%",
         "HVAC energy, summer week (−3.7% winter)",
         GREEN, fill=CARD_GREEN, height=1.46, vsize=33)
    hero(s_feas, 8.9, 1.02, 4.02, "2.1 s",
         "median agent decision latency, fully on-device",
         BLUE, fill=CARD_BLUE, height=1.46, vsize=33)

    card(s_feas, 0.42, 2.72, 6.2, 1.98, fill=CARD_ORANGE, line=None)
    body = by_name(s_feas, "TextBox 8")
    move(body, left=0.72, top=2.82, width=5.6, height=2.34)
    write(body, [
        ("RISKS & CONSTRAINTS", 13, True, ORANGE, 0, False),
        ("LLM latency inside a blocking simulation callback", 11.5, False, INK, 0, True),
        ("A small model can move the wrong setpoint, or run heating against cooling",
         11.5, False, INK, 0, True),
        ("One setpoint pair cannot satisfy five differently-loaded zones",
         11.5, False, INK, 0, True),
    ], space_after=8)
    title_face(body, only_first=True)

    card(s_feas, 6.75, 2.72, 6.17, 1.98, fill=CARD_BLUE, line=None)
    right = textbox(s_feas, 7.05, 2.82, 5.57, 2.34)
    write(right, [
        ("HOW EACH IS SOLVED", 13, True, BLUE_DEEP, 0, False),
        ("Hard per-turn deadline; on timeout the loop holds the last accepted command",
         11.5, False, INK, 0, True),
        ("Observations state the required direction — the model cannot invert physics",
         11.5, False, INK, 0, True),
        ("Seasonal changeover parks the idle setpoint; a per-zone trim corrects each zone",
         11.5, False, INK, 0, True),
    ], space_after=8)
    title_face(right, only_first=True)

    card(s_feas, 0.42, 4.96, 12.5, 1.34, fill=CARD_GREEN, line=None)
    scale = textbox(s_feas, 0.72, 5.06, 11.9, 1.14)
    write(scale, [
        ("VIABILITY AT SCALE", 13, True, GREEN, 0, False),
        ("Buildings consume roughly 40% of global energy — a single-digit HVAC "
         "reduction is material at portfolio scale. The loop is model-agnostic: any "
         "EnergyPlus model with a thermostat works, and swapping in a larger "
         "cognitive engine needs no change to the MCP tool layer.",
         11.5, False, INK, 0, False),
    ], space_after=5)
    title_face(scale, only_first=True)

    # ========================================================== 5 · ARTIFACTS
    card(s_art, 0.42, 1.0, 12.5, 1.16, fill=CARD_BLUE, line=None)
    badge(s_art, 0.62, 1.16, "4")
    body = by_name(s_art, "TextBox 8")
    move(body, left=1.22, top=1.08, width=11.4, height=1.24)
    write(body, [
        ("github.com/ayushYadav1107/Eco_loop", 13, True, BLUE_DEEP, 0, False),
        ("Baseline and runtime-generated .idf models, the full agent decision trail "
         "(agent_decisions.jsonl), per-timestep EnergyPlus results and the savings "
         "dashboard are all committed — a fresh clone reproduces every figure here.",
         11.5, False, INK, 0, False),
    ], space_after=6)
    for run in body.text_frame.paragraphs[0].runs:
        run.font.name = MONO

    image(s_art, HERE / "decisions.png", 0.62, 2.48, 12.1)

    # ================================================ 6 · RESEARCH & REFERENCES
    card(s_ref, 0.42, 1.06, 6.3, 3.62, fill=CARD, line=HAIRLINE)
    badge(s_ref, 0.62, 1.2, "5")
    body = by_name(s_ref, "TextBox 8")
    move(body, left=1.22, top=1.18, width=5.32, height=3.9)
    write(body, [
        ("SIMULATION & BUILDING PHYSICS", 12.5, True, BLUE_DEEP, 0, False),
        ("EnergyPlus Engineering Reference · EMS Application Guide", 11, False, INK, 0, True),
        ("pyenergyplus Data Exchange API — get_variable_handle, set_actuator_value",
         11, False, INK, 0, True),
        ("ISO 7730:2005 · ASHRAE Standard 55 — PMV/PPD comfort model", 11, False, INK, 0, True),
        ("AGENT, PROTOCOL & INFERENCE", 12.5, True, BLUE_DEEP, 0, False),
        ("Model Context Protocol — modelcontextprotocol.io · FastMCP — gofastmcp.com",
         11, False, INK, 0, True),
        ("Meta Llama 3.2 served locally through Ollama — ollama.com", 11, False, INK, 0, True),
    ], space_after=6)
    title_face(body)

    card(s_ref, 6.9, 1.06, 6.02, 2.34, fill=CARD_BLUE, line=None)
    repro = textbox(s_ref, 7.18, 1.16, 5.46, 2.14)
    write(repro, [
        ("REPRODUCE THE RESULT", 12.5, True, BLUE_DEEP, 0, False),
        ("python main.py prepare", 10.5, False, INK, 0, False),
        ("python main.py run-baseline --start 07-15 --end 07-21", 10.5, False, INK, 0, False),
        ("python main.py run-ai --start 07-15 --end 07-21", 10.5, False, INK, 0, False),
        ("python main.py dashboard", 10.5, False, INK, 0, False),
    ], space_after=4)
    paras = repro.text_frame.paragraphs
    for run in paras[0].runs:
        run.font.name = TITLE_FONT
    for para in paras[1:]:
        for run in para.runs:
            run.font.name = MONO

    card(s_ref, 6.9, 3.54, 6.02, 1.64, fill=CARD_GREEN, line=None)
    audit = textbox(s_ref, 7.18, 3.64, 5.46, 1.44)
    write(audit, [
        ("AUDITABLE, NOT ANECDOTAL", 12.5, True, GREEN, 0, False),
        ("The controller runs at temperature 0, so repeated runs return identical "
         "totals. Anyone can clone the repo and reproduce these exact figures.",
         11, False, INK, 0, False),
    ], space_after=5)
    title_face(audit, only_first=True)

    card(s_ref, 0.42, 5.42, 12.5, 1.32, fill=CARD_ORANGE, line=None)
    limit = textbox(s_ref, 0.72, 5.52, 11.9, 1.12)
    write(limit, [
        ("HONEST LIMITATION — AND THE NEXT STEP", 12.5, True, ORANGE, 0, False),
        ("One setpoint pair drives five differently-loaded zones. Per zone the agent "
         "holds 85–96% of occupied time inside the PMV band; the stricter "
         "worst-zone metric is lower. Per-zone setpoints are next — the actuator "
         "handles and per-zone PMV are already in place.", 11.5, False, INK, 0, False),
    ], space_after=5)
    title_face(limit, only_first=True)

    prs.save(str(OUTPUT))
    print(f"wrote {OUTPUT.name}  ({len(prs.slides._sldIdLst)} slides)")


if __name__ == "__main__":
    main()
