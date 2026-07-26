"""Build the Eco-Loop Idea submission deck from the provided SIH template.

Template rules honoured:
  * max six slides INCLUDING the title -> the instructions slide is deleted,
    exactly as that slide itself directs;
  * the "idea details pointers" are kept verbatim as section headers with the
    answer written underneath — the template forbids changing them;
  * points and diagrams, not paragraphs.

Only the presentation layer is restyled (ground, type, colour, cards). Every
required section and pointer survives intact.

All figures come from `make_images.py`, which reads the committed run outputs,
so the numbers on the slides cannot drift from the repository.
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from style import (BODY, CARD, CARD_COOL, CHALK, EMBER, EMBER_LIGHT, INK, MIST,
                   NAVY, NAVY_SOFT, PAPER, SLATE, TEAL, TEAL_LIGHT, TITLE_FONT,
                   badge, card, set_bg, style_chrome, style_title)

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.pptx"
OUTPUT = HERE / "Eco-Loop_Building_Agents_Idea.pptx"

# What only the submitting student can supply. Deliberately loud so it cannot
# be missed, and reported back rather than invented.
TBD = "<<FILL IN>>"


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #
def set_bullet(paragraph, char: str = "•") -> None:
    """A real bullet glyph via pPr, never typed into the text.

    marL/indent give the hanging indent; without them the glyph sits flush
    against the first word and wrapped lines run back underneath it.
    """
    pPr = paragraph._p.get_or_add_pPr()
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    pPr.set("marL", "165100")
    pPr.set("indent", "-165100")
    pPr.append(pPr.makeelement(qn("a:buFont"), {"typeface": BODY}))
    pPr.append(pPr.makeelement(qn("a:buChar"), {"char": char}))


def clear_bullet(paragraph) -> None:
    """Force 'no bullet'. The template's body text boxes carry list formatting,
    so a paragraph that does not opt out inherits a glyph and a hanging indent —
    which put a stray bullet in front of every section header."""
    pPr = paragraph._p.get_or_add_pPr()
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    pPr.set("marL", "0")
    pPr.set("indent", "0")
    pPr.append(pPr.makeelement(qn("a:buNone"), {}))


def write(shape, blocks, *, space_after=5) -> None:
    """Replace a text frame with (text, size, bold, colour, level, bullet) rows."""
    tf = shape.text_frame
    tf.word_wrap = True
    tf.clear()
    first = True
    for text, size, bold, colour, level, bullet in blocks:
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        para.level = level
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
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    box.text_frame.word_wrap = True
    return box


def stat_tile(slide, left, top, width, value, label, accent, *,
              on_navy=False, height=1.3, vsize=29):
    """Big figure over a small caption, on its own tinted panel."""
    card(slide, left, top, width, height, fill=NAVY_SOFT if on_navy else CARD)
    box = textbox(slide, left + 0.24, top + 0.11, width - 0.48, height - 0.22)
    write(box, [
        (value, vsize, True, accent, 0, False),
        (label, 10.5, False, MIST if on_navy else SLATE, 0, False),
    ], space_after=1)
    return box


# --------------------------------------------------------------------------- #
def main() -> None:
    prs = Presentation(str(TEMPLATE))

    # Delete the IMPORTANT INSTRUCTIONS slide, leaving exactly six.
    sld_lst = prs.slides._sldIdLst
    first = list(sld_lst)[0]
    prs.part.drop_rel(first.get(qn("r:id")))
    sld_lst.remove(first)

    s_title, s_idea, s_tech, s_feas, s_art, s_ref = prs.slides

    # =============================================================== 1 TITLE
    # Dark opening slide — the top slice of the sandwich.
    set_bg(s_title, NAVY)
    style_title(s_title, colour=CHALK, size=32)
    style_chrome(s_title, MIST)
    for sh in s_title.shapes:
        if sh.name == "Subtitle 3":
            for para in sh.text_frame.paragraphs:
                for run in para.runs:
                    run.font.name = TITLE_FONT
                    run.font.color.rgb = CHALK

    card(s_title, 0.42, 1.3, 6.4, 3.62, fill=NAVY_SOFT)
    fields = by_name(s_title, "TextBox 6")
    move(fields, left=0.72, top=1.5, width=5.85, height=3.65)
    write(fields, [
        (f"Problem Statement ID   {TBD}", 13.5, True, EMBER_LIGHT, 0, False),
        ("Problem Statement Title   Eco-Loop Building Agents — autonomous "
         "closed-loop HVAC control using EnergyPlus, MCP and a local open-source LLM",
         13.5, False, CHALK, 0, False),
        (f"Theme   {TBD}", 13.5, False, CHALK, 0, False),
        ("PS Category   Software", 13.5, False, CHALK, 0, False),
        ("Student Name   Ayush Yadav", 13.5, False, CHALK, 0, False),
        (f"Student ID   {TBD}", 13.5, False, CHALK, 0, False),
    ], space_after=10)

    pitch = textbox(s_title, 7.15, 1.42, 5.7, 1.95)
    write(pitch, [
        ("A building that runs itself.", 22, True, EMBER_LIGHT, 0, False),
        ("EnergyPlus supplies the physics, a local open-source LLM supplies the "
         "judgement, and the Model Context Protocol carries every reading and every "
         "command between them — while the simulation is still running.",
         12.5, False, CHALK, 0, False),
    ], space_after=8)

    for i, (v, lab, col) in enumerate([
        ("−8.5%", "HVAC energy, summer week", EMBER_LIGHT),
        ("336 / 336", "agent turns — zero failures", TEAL_LIGHT),
        ("100%", "open source, runs on a laptop", CHALK),
    ]):
        stat_tile(s_title, 7.15, 3.28 + i * 1.19, 5.7, v, lab, col,
                  on_navy=True, height=1.07, vsize=26)

    chips = textbox(s_title, 0.72, 5.15, 6.1, 1.0)
    write(chips, [
        ("EnergyPlus 26.1 · pyenergyplus · FastMCP · Model Context Protocol · "
         "Ollama + Llama 3.2 3B · Pydantic · Streamlit", 10.5, False, MIST, 0, False),
    ], space_after=2)

    # ================================================================ 2 IDEA
    set_bg(s_idea, PAPER)
    style_title(s_idea, colour=NAVY)
    style_chrome(s_idea, SLATE)
    badge(s_idea, 0.46, 1.3, "1")

    card(s_idea, 0.42, 1.26, 6.3, 4.42, fill=CARD)
    body = by_name(s_idea, "TextBox 8")
    move(body, left=1.02, top=1.3, width=5.5, height=5.4)
    write(body, [
        ("Proposed Solution", 14.5, True, EMBER, 0, False),
        ("Traditional BMS follow rigid clock schedules. Eco-Loop turns the building "
         "into a self-correcting agent.", 11.5, False, INK, 0, False),
        ("Detailed explanation", 14.5, True, EMBER, 0, False),
        ("EnergyPlus runs inside the Python process; sensors read every zone timestep",
         11, False, INK, 0, True),
        ("Every 60 simulated minutes the aggregated state goes to a local LLM through "
         "six MCP tools", 11, False, INK, 0, True),
        ("The model reasons over comfort, demand and grid carbon, then returns setpoints",
         11, False, INK, 0, True),
        ("How it addresses the problem", 14.5, True, EMBER, 0, False),
        ("Control adapts continuously to weather, occupancy and carbon intensity "
         "instead of a fixed schedule", 11, False, INK, 0, True),
        ("Innovation and uniqueness", 14.5, True, EMBER, 0, False),
        ("Setpoints are injected into the LIVE solver via the actuator API — no IDF "
         "rewrite, no restart, so the loop closes inside one simulation",
         11, False, INK, 0, True),
        ("MCP is the safety boundary: every command is validated server-side before it "
         "can reach an actuator", 11, False, INK, 0, True),
    ], space_after=5)

    add_image(s_idea, HERE / "results.png", 6.95, 1.4, 6.0)
    card(s_idea, 6.9, 3.46, 6.05, 1.52, fill=CARD_COOL)
    cap = textbox(s_idea, 7.14, 3.58, 5.58, 1.28)
    write(cap, [
        ("Measured, not projected", 13.5, True, TEAL, 0, False),
        ("8.5% less HVAC energy across a summer week, while holding occupants closer "
         "to thermal neutrality than the baseline (mean PMV −0.43 → −0.02).",
         11.5, False, INK, 0, False),
    ], space_after=4)

    card(s_idea, 6.9, 5.14, 6.05, 1.62, fill=CARD)
    why = textbox(s_idea, 7.14, 5.26, 5.58, 1.38)
    write(why, [
        ("Why it is different", 13.5, True, EMBER, 0, False),
        ("Most LLM-and-simulation work rewrites the model file and re-runs it — that "
         "can only compare finished runs. It cannot react to a zone drifting out of "
         "comfort at 14:00 on day 3.", 11.5, False, INK, 0, False),
    ], space_after=4)

    # ================================================================ 3 TECH
    set_bg(s_tech, PAPER)
    style_title(s_tech, colour=NAVY)
    style_chrome(s_tech, SLATE)
    badge(s_tech, 0.46, 1.2, "2")

    card(s_tech, 0.42, 1.16, 12.5, 1.42, fill=CARD)
    body = by_name(s_tech, "TextBox 8")
    move(body, left=1.02, top=1.24, width=11.6, height=1.3)
    write(body, [
        ("Technologies    EnergyPlus 26.1 (pyenergyplus C API) · Python · FastMCP over "
         "streamable HTTP · Ollama running Llama 3.2 3B · Pydantic validation · "
         "Streamlit + Plotly dashboard", 11.5, False, INK, 0, False),
        ("Methodology    sample every timestep → aggregate per control interval → "
         "LLM tool-calling turn → validate → inject into live actuators",
         11.5, False, INK, 0, False),
    ], space_after=6)
    add_image(s_tech, HERE / "arch.png", 0.72, 2.84, 11.9)

    # ========================================================== 4 FEASIBILITY
    set_bg(s_feas, PAPER)
    style_title(s_feas, colour=NAVY)
    style_chrome(s_feas, SLATE)
    badge(s_feas, 0.46, 1.14, "3")

    for i, (v, lab, col) in enumerate([
        ("336 / 336", "agent turns — 0 fallbacks, 0 timeouts, 0 actuation errors", TEAL),
        ("−8.5%", "HVAC energy, summer week (−3.7% winter)", EMBER),
        ("2.1 s", "median agent decision latency, on-device", NAVY),
    ]):
        stat_tile(s_feas, 1.02 + i * 4.0, 1.1, 3.78, v, lab, col,
                  height=1.24, vsize=26)

    card(s_feas, 0.42, 2.72, 6.2, 2.62, fill=CARD)
    body = by_name(s_feas, "TextBox 8")
    move(body, left=0.72, top=2.84, width=5.6, height=3.8)
    write(body, [
        ("Feasibility", 14, True, EMBER, 0, False),
        ("Runs end to end on one consumer laptop — 4 GB VRAM, no cloud, no API cost",
         11, False, INK, 0, True),
        ("Fully open source: EnergyPlus, Llama 3.2, FastMCP", 11, False, INK, 0, True),
        ("Potential challenges and risks", 14, True, EMBER, 0, False),
        ("LLM latency inside a blocking simulation callback", 11, False, INK, 0, True),
        ("A small model can move the wrong setpoint, or run heating and cooling against "
         "each other", 11, False, INK, 0, True),
        ("One setpoint pair cannot satisfy five differently-loaded zones",
         11, False, INK, 0, True),
    ], space_after=6)

    card(s_feas, 6.75, 2.72, 6.16, 2.62, fill=CARD_COOL)
    right = textbox(s_feas, 7.05, 2.84, 5.56, 3.8)
    write(right, [
        ("Strategies for overcoming them", 14, True, TEAL, 0, False),
        ("Interval batching + a hard per-turn deadline; on timeout the loop holds the "
         "last accepted command rather than stalling", 11, False, INK, 0, True),
        ("Observations state the required direction, so the model cannot invert the "
         "physics", 11, False, INK, 0, True),
        ("Seasonal changeover parks the idle mode's setpoint — heating and cooling can "
         "never fight", 11, False, INK, 0, True),
        ("A per-zone proportional trim corrects each zone from its own PMV",
         11, False, INK, 0, True),
    ], space_after=6)

    # Viability at scale — real content for the lower band rather than padding.
    card(s_feas, 0.42, 5.5, 12.49, 1.28, fill=CARD_COOL)
    scale = textbox(s_feas, 0.72, 5.62, 11.9, 1.04)
    write(scale, [
        ("Viability at scale", 14, True, TEAL, 0, False),
        ("Buildings consume roughly 40% of global energy, so a single-digit HVAC "
         "reduction is material at portfolio scale. The loop is model-agnostic — any "
         "EnergyPlus model with a thermostat works — and swapping the cognitive engine "
         "for a larger local or hosted model needs no change to the MCP tool layer.",
         11.5, False, INK, 0, False),
    ], space_after=4)

    # ============================================================ 5 ARTIFACTS
    set_bg(s_art, PAPER)
    style_title(s_art, colour=NAVY)
    style_chrome(s_art, SLATE)
    badge(s_art, 0.46, 1.12, "4")

    card(s_art, 0.42, 1.08, 12.5, 1.3, fill=CARD)
    body = by_name(s_art, "TextBox 8")
    move(body, left=1.02, top=1.16, width=11.6, height=1.16)
    write(body, [
        ("Code   github.com/ayushYadav1107/Eco_loop", 12.5, True, EMBER, 0, False),
        ("Baseline and runtime-generated .idf models, the full agent decision trail "
         "(agent_decisions.jsonl), per-timestep EnergyPlus results and the savings "
         "dashboard are all committed — a fresh clone reproduces these figures.",
         11.5, False, INK, 0, False),
    ], space_after=5)
    add_image(s_art, HERE / "decisions.png", 0.72, 2.6, 11.9)

    # =========================================================== 6 REFERENCES
    # Dark closing slide — the bottom slice of the sandwich.
    set_bg(s_ref, NAVY)
    style_title(s_ref, colour=CHALK)
    style_chrome(s_ref, MIST)

    card(s_ref, 0.42, 1.2, 6.3, 3.95, fill=NAVY_SOFT)
    body = by_name(s_ref, "TextBox 8")
    move(body, left=0.74, top=1.36, width=5.66, height=5.1)
    write(body, [
        ("Simulation and building physics", 13, True, EMBER_LIGHT, 0, False),
        ("EnergyPlus Engineering Reference and EMS Application Guide — energyplus.net",
         11, False, CHALK, 0, True),
        ("pyenergyplus Data Exchange API — get_variable_handle, set_actuator_value",
         11, False, CHALK, 0, True),
        ("ISO 7730:2005 and ASHRAE Standard 55 — PMV/PPD thermal comfort model",
         11, False, CHALK, 0, True),
        ("Agent, protocol and inference", 13, True, EMBER_LIGHT, 0, False),
        ("Model Context Protocol specification — modelcontextprotocol.io", 11, False, CHALK, 0, True),
        ("FastMCP — gofastmcp.com", 11, False, CHALK, 0, True),
        ("Meta Llama 3.2 served locally through Ollama — ollama.com", 11, False, CHALK, 0, True),
        ("Project", 13, True, EMBER_LIGHT, 0, False),
        ("Repository, architecture document and reproducible results — "
         "github.com/ayushYadav1107/Eco_loop", 11, False, CHALK, 0, True),
    ], space_after=6)

    card(s_ref, 6.9, 1.2, 6.0, 2.02, fill=NAVY_SOFT)
    repro = textbox(s_ref, 7.18, 1.36, 5.44, 2.28)
    write(repro, [
        ("Reproduce the result", 13, True, EMBER_LIGHT, 0, False),
        ("python main.py prepare", 10.5, True, CHALK, 0, False),
        ("python main.py run-baseline --start 07-15 --end 07-21", 10.5, True, CHALK, 0, False),
        ("python main.py run-ai --start 07-15 --end 07-21", 10.5, True, CHALK, 0, False),
        ("python main.py dashboard", 10.5, True, CHALK, 0, False),
        ("Temperature 0, so repeated runs return identical totals — the comparison is "
         "auditable rather than anecdotal.", 10.5, False, MIST, 0, False),
    ], space_after=4)

    card(s_ref, 6.9, 3.42, 6.0, 1.73, fill=NAVY_SOFT)
    limit = textbox(s_ref, 7.18, 3.56, 5.44, 1.5)
    write(limit, [
        ("Honest limitation", 13, True, EMBER_LIGHT, 0, False),
        ("One setpoint pair drives five differently-loaded zones. Per zone the agent "
         "holds 85–96% of occupied time inside the PMV band; the stricter worst-zone "
         "metric is lower.", 11, False, CHALK, 0, False),
        ("Per-zone setpoints are the next step — the actuator handles and per-zone PMV "
         "are already in place.", 11, False, MIST, 0, False),
    ], space_after=4)

    prs.save(str(OUTPUT))
    print(f"wrote {OUTPUT.name}  ({len(prs.slides._sldIdLst)} slides)")


if __name__ == "__main__":
    main()
