"""Export the deck to PDF (required for submission) and PNGs (for visual QA).

Uses the installed PowerPoint via COM rather than LibreOffice: it renders the
real fonts, so a text-overflow check here reflects what the grader will see.
"""
from __future__ import annotations

import sys
from pathlib import Path

import win32com.client

HERE = Path(__file__).resolve().parent
DECK = HERE / "Echo-Loop_Idea_Deck.pptx"
PDF = HERE / "Echo-Loop_Idea_Deck.pdf"
SHOTS = HERE / "shots"

PP_SAVE_AS_PDF = 32


def main() -> None:
    SHOTS.mkdir(exist_ok=True)
    for old in SHOTS.glob("*.png"):
        old.unlink()

    app = win32com.client.Dispatch("PowerPoint.Application")
    pres = None
    try:
        pres = app.Presentations.Open(str(DECK), WithWindow=False)
        pres.SaveAs(str(PDF), PP_SAVE_AS_PDF)
        print(f"wrote {PDF.name}")
        for i, slide in enumerate(pres.Slides, 1):
            out = SHOTS / f"slide-{i}.png"
            slide.Export(str(out), "PNG", 1600, 900)
            print(f"  {out.name}")
    finally:
        if pres is not None:
            pres.Close()
        app.Quit()


if __name__ == "__main__":
    sys.exit(main())
