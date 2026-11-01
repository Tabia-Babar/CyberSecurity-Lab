"""Generic PDF builder using ReportLab. Blocks:
("h", text) ("p", text) ("kv", [[k, v], ...]) ("table", header, rows)"""
import io
from datetime import datetime
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


def build_pdf(title, subtitle, blocks):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=title,
                            leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    st = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=st["Normal"], fontSize=7.5, leading=9)
    els = [Paragraph(escape(title), st["Title"]),
           Paragraph(escape(subtitle) + f" | Generated {datetime.now():%Y-%m-%d %H:%M}", st["Normal"]),
           Spacer(1, 12)]
    width = A4[0] - 72

    def P(x):
        return Paragraph(escape("" if x is None else str(x)), cell)

    for b in blocks:
        kind = b[0]
        if kind == "h":
            els += [Spacer(1, 8), Paragraph(escape(b[1]), st["Heading2"])]
        elif kind == "p":
            els.append(Paragraph(escape(b[1] or "-").replace("\n", "<br/>"), st["Normal"]))
        elif kind == "kv":
            t = Table([[P(k), P(v)] for k, v in b[1]], colWidths=[width * .3, width * .7])
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .4, colors.grey),
                                   ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            els.append(t)
        elif kind == "table":
            header, rows = b[1], b[2]
            data = [[P(h) for h in header]] + [[P(c) for c in r] for r in rows]
            if not rows:
                data.append([P("No records")] + [P("") for _ in header[1:]])
            t = Table(data, repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .4, colors.grey),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            els.append(t)
        els.append(Spacer(1, 6))
    doc.build(els)
    buf.seek(0)
    return buf
