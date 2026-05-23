#!/usr/bin/env python3
"""Generate PDF from docs/FORTRESS_AI_WHITEPAPER.md."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "docs" / "FORTRESS_AI_WHITEPAPER.md"
OUT_PATH = ROOT / "docs" / "FORTRESS_AI_WHITEPAPER.pdf"


def ascii_safe(text: str) -> str:
    replacements = {
        "\u2014": "-",
        "\u2013": "-",
        "\u00b7": "-",
        "\u2265": ">=",
        "\u2192": "->",
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


def md_to_html(text: str) -> str:
    import markdown

    return markdown.markdown(text, extensions=["tables", "fenced_code", "nl2br"])


def build_pdf(html: str, out: Path) -> None:
    from fpdf import FPDF

    class PDF(FPDF):
        def header(self) -> None:
            if self.page_no() == 1:
                return
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(100, 100, 100)
            self.cell(0, 8, "Fortress AI - System & Dashboard Whitepaper", new_x="LMARGIN", new_y="NEXT", align="L")

        def footer(self) -> None:
            self.set_y(-15)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 10, f"Page {self.page_no()}", align="C")

    pdf = PDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.multi_cell(0, 10, "Fortress AI", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 14)
    pdf.multi_cell(0, 8, "System & Dashboard Whitepaper", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 10)
    pdf.multi_cell(
        0,
        6,
        "External technical review - Neural Ops UI v2 - Not investment advice",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(6)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 10)
    pdf.write_html(html)
    pdf.output(str(out))


def main() -> int:
    md_path = Path(sys.argv[1]) if len(sys.argv) > 1 else MD_PATH
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT_PATH
    if not md_path.exists():
        print(f"Missing: {md_path}", file=sys.stderr)
        return 1
    text = ascii_safe(md_path.read_text(encoding="utf-8"))
    html = md_to_html(text)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    build_pdf(html, out_path)
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
