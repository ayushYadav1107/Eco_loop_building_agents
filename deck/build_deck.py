"""Build the Eco-Loop Idea submission deck from the provided SIH template.

Template rules honoured:
  * max six slides INCLUDING the title  -> the instructions slide is deleted,
    leaving exactly the six content slides;
  * the "idea details pointers" are kept verbatim as section headers and the
    answer is written underneath each one;
  * points and diagrams, not paragraphs.

All figures come from `make_images.py`, which reads the committed run outputs,
so the deck cannot drift from the repository.
"""
from __future__ import annotations

import copy
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.pptx"
OUTPUT = HERE / "Eco-Loop_Building_Agents_Idea.pptx"

INK = RGBColor(0x12, 0x1B, 0x2B)
SLATE = RGBColor(0x4A, 0x57, 0x68)
EMBER = RGBColor(0xC2, 0x4A, 0x18)
GREEN = RGBColor(0x0D, 0x7A, 0x4F)
BODY = "Arial"

# What only the submitting student can supply. Deliberately loud so it cannot
# be missed, and reported back rather than invented.
TBD = "<<FILL IN>>"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def delete_slide(prs, index: int) -> None:
    sld_id_lst = prs.slides._sldIdLst
    slides = list(sld_id_lst)
    rId = slides[index].get(qn("r:id"))
    prs.part.drop_rel(rId)
    sld_id_lst.remove(slides[index])


def set_bullet(paragraph, char: str = "•") -> None:
    """Give a paragraph a real bullet glyph via pPr (never typed into the text).

    marL/indent create the hanging indent: without them the glyph sits flush
    against the first word and wrapped lines run back under the bullet.
    """
    pPr = paragraph._p.get_or_add_pPr()
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    pPr.set("marL", "182880")     # 0.20" text inset
    pPr.set("indent", "-182880")  # pull the glyph back to the margin
    buFont = pPr.makeelement(qn("a:buFont"), {"typeface": "Arial"})
    buChar = pPr.makeelement(qn("a:buChar"), {"char": char})
    pPr.append(buFont)
    pPr.append(buChar)


def clear_bullet(paragraph) -> None:
    """Force 'no bullet'. The template's body text boxes carry list formatting,
    so a paragraph that does not opt in still inherits a glyph and a hanging
    indent - which put a stray bullet in front of every section header."""
    pPr = paragraph._p.get_or_add_pPr()
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    pPr.set("marL", "0")
    pPr.set("indent", "0")
    pPr.append(pPr.makeelement(qn("a:buNone"), {}))


def write(shape, blocks, *, space_after=5) -> None:
    """Replace a text frame with (text, size, bold, colour, indent, bullet) rows."""
    tf = shape.text_frame
    tf.word_wrap = True
    tf.clear()
    first = True
    for text, size, bold, colour, indent, bullet in blocks:
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        para.level = indent
        para.space_after = Pt(space_after)
        # Explicit left alignment: the template's boxes are justified, which
        # stretches word spacing on any line that wraps.
        para.alignment = PP_ALIGN.LEFT
        set_bullet(para) if bullet else clear_bullet(para)
        run = para.add_run()
        run.text = text
        run.font.name = BODY
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = colour


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


def add_image(slide, img: Path, left, top, width):
    return slide.shapes.add_picture(str(img), Inches(left), Inches(top), width=Inches(width))


def textbox(slide, left, top, width, height):
    return slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))


def stat(slide, left, top, width, value, label, colour=EMBER):
    """A large figure with a small caption - the deck's repeated motif."""
    box = textbox(slide, left, top, width, 1.15)
    write(box, [
        (value, 34, True, colour, 0, False),
        (label, 11, False, SLATE, 0, False),
    ], space_after=1)
    box.text_frame.word_wrap = True
    return box


# --------------------------------------------------------------------------- #
def main() -> None:
    prs = Presentation(str(TEMPLATE))
    delete_slide(prs, 0)                      # the IMPORTANT INSTRUCTIONS slide
    s_title, s_idea, s_tech, s_feas, s_art, s_ref = prs.slides

    # ---------------------------------------------------------------- 1 title
    write(by_name(s_title, "TextBox 6"), [
        (f"Problem Statement ID  –  {TBD}", 15, True, INK, 0, False),
        ("Problem Statement Title  –  Eco-Loop Building Agents: autonomous "
         "closed-loop HVAC control using EnergyPlus, MCP and a local open-source LLM",
         15, False, INK, 0, False),
        (f"Theme  –  {TBD}   (Smart Automation / Sustainability)", 15, False, INK, 0, False),
        ("PS Category  –  Software", 15, False, INK, 0, False),
        ("Student Name  –  Ayush Yadav", 15, False, INK, 0, False),
        (f"Student ID  –  {TBD}", 15, False, INK, 0, False),
    ], space_after=13)

    # The title slide carries the headline numbers rather than a diagram: the
    # architecture belongs on TECHNICAL APPROACH, and repeating it here made the
    # same figure appear twice in a six-slide deck.
    pitch = textbox(s_title, 7.15, 1.5, 5.6, 1.9)
    write(pitch, [
        ("A building that runs itself.", 21, True, EMBER, 0, False),
        ("EnergyPlus supplies the physics, a local open-source LLM supplies the "
         "judgement, and the Model Context Protocol carries every reading and every "
         "command between them — while the simulation is still running.",
         13, False, INK, 0, False),
    ], space_after=9)

    for i, (v, lab) in enumerate([
        ("-8.5%", "HVAC energy, summer week"),
        ("336/336", "agent turns, zero failures"),
        ("100%", "open source, runs on a laptop"),
    ]):
        stat(s_title, 7.15, 3.5 + i * 1.05, 5.6, v, lab,
             GREEN if i else EMBER)

    chips = textbox(s_title, 0.55, 5.15, 6.0, 1.7)
    write(chips, [
        ("Built with", 12, True, EMBER, 0, False),
        ("EnergyPlus 26.1  ·  pyenergyplus  ·  FastMCP  ·  Model Context Protocol  ·  "
         "Ollama + Llama 3.2 3B  ·  Pydantic  ·  Streamlit", 12, False, SLATE, 0, False),
        ("Everything runs locally. No cloud inference, no API keys, no per-query cost.",
         12, False, SLATE, 0, False),
    ], space_after=6)

    # ----------------------------------------------------------------- 2 idea
    body = by_name(s_idea, "TextBox 8")
    move(body, left=0.55, top=1.35, width=6.15, height=5.4)
    write(body, [
        ("Proposed Solution", 16, True, EMBER, 0, False),
        ("Traditional BMS follow rigid clock schedules. Eco-Loop turns the building "
         "into a self-correcting agent.", 13, False, INK, 0, False),
        ("Detailed explanation", 16, True, EMBER, 0, False),
        ("EnergyPlus runs inside the Python process; sensors are read every zone "
         "timestep", 12.5, False, INK, 0, True),
        ("Every 60 simulated minutes the aggregated state goes to a local LLM "
         "through six MCP tools", 12.5, False, INK, 0, True),
        ("The model reasons over comfort, demand and grid carbon, then returns "
         "setpoints", 12.5, False, INK, 0, True),
        ("How it addresses the problem", 16, True, EMBER, 0, False),
        ("Control adapts continuously to weather, occupancy and carbon intensity "
         "instead of a fixed schedule", 12.5, False, INK, 0, True),
        ("Innovation and uniqueness", 16, True, EMBER, 0, False),
        ("Setpoints are injected into the LIVE solver via the actuator API - no IDF "
         "rewrite, no restart, so the loop closes within one simulation",
         12.5, False, INK, 0, True),
        ("MCP is the safety boundary: every command is validated server-side before "
         "it can reach an actuator", 12.5, False, INK, 0, True),
    ], space_after=6)

    add_image(s_idea, HERE / "results.png", 6.9, 1.45, 6.1)
    cap = textbox(s_idea, 6.9, 3.55, 6.1, 1.5)
    write(cap, [
        ("Measured, not projected", 15, True, GREEN, 0, False),
        ("8.5% less HVAC energy across a summer week, while holding occupants "
         "closer to thermal neutrality than the baseline schedule "
         "(mean PMV -0.43 → -0.02).", 12.5, False, INK, 0, False),
    ], space_after=5)
    why = textbox(s_idea, 6.9, 5.0, 6.1, 1.8)
    write(why, [
        ("Why it is different", 15, True, EMBER, 0, False),
        ("Most LLM-and-simulation work rewrites the model file and re-runs it. That "
         "can only compare finished runs — it cannot react to a zone drifting out of "
         "comfort at 14:00 on day 3. Eco-Loop closes the loop inside the simulation.",
         12.5, False, INK, 0, False),
    ], space_after=5)

    # ----------------------------------------------------------------- 3 tech
    body = by_name(s_tech, "TextBox 8")
    move(body, left=0.67, top=1.15, width=12.0, height=1.5)
    write(body, [
        ("Technologies   EnergyPlus 26.1 (pyenergyplus C API)  ·  Python  ·  FastMCP "
         "over streamable HTTP  ·  Ollama running Llama 3.2 3B  ·  Pydantic validation  ·  "
         "Streamlit + Plotly dashboard", 13, False, INK, 0, False),
        ("Methodology   sample every timestep  →  aggregate per control interval  →  "
         "LLM tool-calling turn  →  validate  →  inject into live actuators",
         13, False, INK, 0, False),
    ], space_after=7)
    add_image(s_tech, HERE / "arch.png", 0.9, 2.75, 11.55)

    # ------------------------------------------------------------ 4 viability
    body = by_name(s_feas, "TextBox 8")
    move(body, left=0.67, top=2.65, width=6.0, height=4.4)
    write(body, [
        ("Feasibility", 16, True, EMBER, 0, False),
        ("Runs end to end on one consumer laptop - 4 GB VRAM, no cloud, no API cost",
         12.5, False, INK, 0, True),
        ("Fully open source: EnergyPlus, Llama 3.2, FastMCP", 12.5, False, INK, 0, True),
        ("Potential challenges and risks", 16, True, EMBER, 0, False),
        ("LLM latency inside a blocking simulation callback", 12.5, False, INK, 0, True),
        ("A small model can move the wrong setpoint, or run heating and cooling "
         "against each other", 12.5, False, INK, 0, True),
        ("One setpoint pair cannot satisfy five differently-loaded zones",
         12.5, False, INK, 0, True),
    ], space_after=5)

    right = textbox(s_feas, 7.0, 2.65, 5.7, 4.4)
    write(right, [
        ("Strategies for overcoming them", 16, True, EMBER, 0, False),
        ("Interval batching + a hard per-turn deadline; on timeout the loop holds the "
         "last accepted command rather than stalling", 12.5, False, INK, 0, True),
        ("Observations state the required direction, so the model cannot invert the "
         "physics", 12.5, False, INK, 0, True),
        ("Seasonal changeover parks the idle mode's setpoint - heating and cooling "
         "can never fight", 12.5, False, INK, 0, True),
        ("A per-zone proportional trim corrects each zone from its own PMV",
         12.5, False, INK, 0, True),
    ], space_after=9)

    stat(s_feas, 0.67, 1.25, 3.9, "336 / 336", "agent turns completed - 0 fallbacks, "
         "0 timeouts, 0 actuation errors", GREEN)
    stat(s_feas, 4.85, 1.25, 3.9, "-8.5%", "HVAC energy, summer week (-3.7% winter)")
    stat(s_feas, 9.0, 1.25, 3.9, "2.1 s", "median agent decision latency, on-device")

    # ------------------------------------------------------------- 5 artifacts
    body = by_name(s_art, "TextBox 8")
    move(body, left=0.67, top=1.2, width=12.0, height=1.2)
    write(body, [
        ("Code  ·  github.com/ayushYadav1107/Eco_loop_building_agents", 13, True, EMBER, 0, False),
        ("Baseline and runtime-generated .idf models, the full agent decision trail "
         "(agent_decisions.jsonl), per-timestep EnergyPlus results and the savings "
         "dashboard are committed - a fresh clone reproduces these figures.",
         12.5, False, INK, 0, False),
    ], space_after=6)
    add_image(s_art, HERE / "decisions.png", 0.9, 2.5, 11.55)

    # ------------------------------------------------------------ 6 references
    body = by_name(s_ref, "TextBox 8")
    move(body, left=0.67, top=1.35, width=6.6, height=5.3)
    write(body, [
        ("Simulation and building physics", 15, True, EMBER, 0, False),
        ("EnergyPlus Engineering Reference and EMS Application Guide - energyplus.net",
         12.5, False, INK, 0, True),
        ("pyenergyplus Data Exchange API (get_variable_handle, set_actuator_value)",
         12.5, False, INK, 0, True),
        ("ISO 7730:2005 and ASHRAE Standard 55 - PMV/PPD thermal comfort model",
         12.5, False, INK, 0, True),
        ("Agent, protocol and inference", 15, True, EMBER, 0, False),
        ("Model Context Protocol specification - modelcontextprotocol.io", 12.5, False, INK, 0, True),
        ("FastMCP - gofastmcp.com", 12.5, False, INK, 0, True),
        ("Meta Llama 3.2 served locally through Ollama - ollama.com", 12.5, False, INK, 0, True),
        ("Project", 15, True, EMBER, 0, False),
        ("Repository, architecture document and reproducible results - "
         "github.com/ayushYadav1107/Eco_loop_building_agents", 12.5, False, INK, 0, True),
    ], space_after=6)

    repro = textbox(s_ref, 7.5, 1.35, 5.2, 5.3)
    write(repro, [
        ("Reproduce the result", 16, True, EMBER, 0, False),
        ("python main.py prepare", 11.5, True, INK, 0, False),
        ("python main.py run-baseline --start 07-15 --end 07-21", 11.5, True, INK, 0, False),
        ("python main.py run-ai --start 07-15 --end 07-21", 11.5, True, INK, 0, False),
        ("python main.py dashboard", 11.5, True, INK, 0, False),
        ("The controller runs at temperature 0, so repeated runs return identical "
         "totals — the comparison is auditable rather than anecdotal.",
         12, False, SLATE, 0, False),
        ("Honest limitation", 16, True, EMBER, 0, False),
        ("One setpoint pair drives five differently-loaded zones. Per zone the agent "
         "holds 85–96% of occupied time in the PMV band; the stricter worst-zone "
         "metric is lower. Per-zone setpoints are the next step.",
         12, False, SLATE, 0, False),
    ], space_after=8)

    prs.save(str(OUTPUT))
    print(f"wrote {OUTPUT.name}  ({len(prs.slides.__iter__.__self__._sldIdLst)} slides)")


if __name__ == "__main__":
    main()
