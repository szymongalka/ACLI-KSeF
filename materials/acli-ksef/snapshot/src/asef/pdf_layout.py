"""Branded, screen-sized PDF views of FA(3) invoices."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


PAGE_SIZE = (500, 1500)


@dataclass(frozen=True)
class Palette:
    page: colors.Color
    ink: colors.Color
    muted: colors.Color
    line: colors.Color
    soft: colors.Color
    stripe: colors.Color
    accent: colors.Color
    badge: colors.Color
    due: colors.Color
    due_ink: colors.Color


LIGHT = Palette(*map(colors.HexColor, (
    "#FFFFFF", "#172B37", "#5C7180", "#D9E4E9", "#EEF4F5",
    "#FAFCFC", "#145D6D", "#FFF6E7", "#172B37", "#FFFFFF",
)))
CRT = Palette(*map(colors.HexColor, (
    "#0B0B0B", "#E8E8E8", "#999999", "#373737", "#1B1B1B",
    "#121212", "#E8E8E8", "#242424", "#242424", "#F2F2F2",
)))


def _fonts() -> tuple[str, str, str]:
    paths = {
        "ASEFRegular": (
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ),
        "ASEFBold": (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        "ASEFMono": (
            "/System/Library/Fonts/Supplemental/Courier New.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ),
    }
    registered = set(pdfmetrics.getRegisteredFontNames())
    for name, candidates in paths.items():
        path = next((Path(value) for value in candidates if Path(value).exists()), None)
        if path and name not in registered:
            pdfmetrics.registerFont(TTFont(name, str(path)))
    normal = "ASEFRegular" if "ASEFRegular" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    bold = "ASEFBold" if "ASEFBold" in pdfmetrics.getRegisteredFontNames() else normal
    mono = "ASEFMono" if "ASEFMono" in pdfmetrics.getRegisteredFontNames() else normal
    pdfmetrics.registerFontFamily(normal, normal=normal, bold=bold)
    return normal, bold, mono


class Brand(Flowable):
    def __init__(self, width: float, palette: Palette, bold: str, mono: str):
        super().__init__()
        self.width = width
        self.height = 49
        self.palette = palette
        self.bold = bold
        self.mono = mono

    def draw(self) -> None:
        canvas = self.canv
        palette = self.palette
        canvas.setFillColor(palette.accent)
        canvas.roundRect(0, 5, 43, 43, 8, fill=1, stroke=0)
        canvas.setLineWidth(3)
        canvas.setLineCap(1)
        canvas.setLineJoin(1)
        canvas.setStrokeColor(palette.page)
        path = canvas.beginPath()
        path.moveTo(10, 15)
        path.lineTo(21, 39)
        path.lineTo(33, 15)
        path.moveTo(15, 23)
        path.lineTo(28, 23)
        canvas.drawPath(path, fill=0, stroke=1)
        canvas.setFillColor(colors.HexColor("#D3A456") if palette == LIGHT else colors.HexColor("#F1C96D"))
        canvas.circle(36, 37, 2.5, fill=1, stroke=0)
        canvas.setFont(self.bold, 20)
        canvas.setFillColor(palette.accent)
        canvas.drawString(56, 26, "ASEF")
        canvas.setFont(self.mono, 8.2)
        canvas.setFillColor(palette.muted)
        canvas.drawString(56, 10, "AGENCYJNY SYSTEM ELEKTRONICZNYCH FAKTUR")


class VerificationCode(Flowable):
    def __init__(self, url: str):
        super().__init__()
        self.width = self.height = 105
        self.url = url

    def draw(self) -> None:
        self.canv.setFillColor(colors.white)
        self.canv.rect(0, 0, 105, 105, fill=1, stroke=0)
        drawing = Drawing(105, 105)
        drawing.add(QrCodeWidget(self.url, x=6, y=6, barWidth=93, barHeight=93))
        renderPDF.draw(drawing, self.canv, 0, 0)


def render_mobile_pdf(data: dict[str, Any], theme: str = "light") -> bytes:
    if theme not in {"light", "crt"}:
        raise ValueError("Motyw PDF: light albo crt.")
    palette = LIGHT if theme == "light" else CRT
    page_width, page_height = PAGE_SIZE
    margin = 28
    # SimpleDocTemplate adds 6 pt of frame padding on each side.
    content_width = page_width - 2 * (margin + 6)
    regular_font, bold_font, mono_font = _fonts()

    body = ParagraphStyle("body", fontName=regular_font, fontSize=12, leading=17, textColor=palette.ink)
    small = ParagraphStyle("small", parent=body, fontSize=10, leading=14, textColor=palette.muted)
    label = ParagraphStyle("label", parent=small, fontName=mono_font, fontSize=9.4, leading=13,
                           textColor=palette.accent)
    title = ParagraphStyle("title", parent=body, fontName=bold_font, fontSize=29, leading=34)
    invoice_number = ParagraphStyle("invoice-number", parent=body, fontName=bold_font, fontSize=22, leading=27)
    heading = ParagraphStyle("heading", parent=body, fontName=bold_font, fontSize=17, leading=22)
    strong = ParagraphStyle("strong", parent=body, fontName=bold_font, fontSize=13.5, leading=18)
    party_name = ParagraphStyle("party-name", parent=strong, fontSize=12.5, leading=16)
    party_body = ParagraphStyle("party-body", parent=body, fontSize=10.5, leading=14)
    cell = ParagraphStyle("cell", parent=body, fontSize=10.5, leading=14.4)
    cell_bold = ParagraphStyle("cell-bold", parent=cell, fontName=bold_font)
    cell_right = ParagraphStyle("cell-right", parent=cell, alignment=TA_RIGHT)
    item_cell = ParagraphStyle("item-cell", parent=cell, fontSize=9, leading=12)
    item_bold = ParagraphStyle("item-bold", parent=item_cell, fontName=bold_font)
    item_right = ParagraphStyle("item-right", parent=item_cell, fontSize=8, leading=11, alignment=TA_RIGHT)
    item_center = ParagraphStyle("item-center", parent=item_cell, alignment=TA_CENTER)
    header = ParagraphStyle("header", parent=label, fontName=bold_font, fontSize=8.2, leading=11)
    header_right = ParagraphStyle("header-right", parent=header, alignment=TA_RIGHT)
    header_center = ParagraphStyle("header-center", parent=header, alignment=TA_CENTER)
    due_label = ParagraphStyle("due-label", parent=label, textColor=palette.due_ink)
    due_amount = ParagraphStyle("due-amount", parent=invoice_number, fontSize=24, leading=29,
                                textColor=palette.due_ink)

    def p(value: Any, paragraph_style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(escape(str(value if value not in (None, "") else "—")), paragraph_style)

    def section_table(rows: list[list[Any]], widths: list[float]) -> Table:
        table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        commands: list[tuple[Any, ...]] = [
            ("BACKGROUND", (0, 0), (-1, 0), palette.soft),
            ("BOX", (0, 0), (-1, -1), 0.75, palette.line),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, palette.line),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, 0), 10),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
            ("TOPPADDING", (0, 1), (-1, -1), 11),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 11),
        ]
        for index in range(2, len(rows), 2):
            commands.append(("BACKGROUND", (0, index), (-1, index), palette.stripe))
        for index in range(1, len(rows) - 1):
            commands.append(("LINEBELOW", (0, index), (-1, index), 0.45, palette.line))
        table.setStyle(TableStyle(commands))
        return table

    invoice = data["invoice"]
    output = BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=PAGE_SIZE, leftMargin=margin, rightMargin=margin,
        topMargin=32, bottomMargin=36, title=f"Faktura {invoice['number']} | ASEF {theme}", author="ASEF",
    )
    story: list[Any] = [Brand(content_width, palette, bold_font, mono_font), Spacer(1, 26)]
    intro = [
        p("DOKUMENT / FA(3)", label), Spacer(1, 4),
        p("Faktura korygująca" if invoice["kind"] == "correction" else "Faktura VAT", title),
        Spacer(1, 8),
        p("NUMER FAKTURY", label),
        p(invoice["number"], invoice_number),
    ]
    if data["verification"]:
        intro_table = Table(
            [[intro, [p("WERYFIKACJA KSEF", label), Spacer(1, 5),
                      VerificationCode(data["verification"]["url"])]]],
            colWidths=[content_width - 120, 120], hAlign="LEFT",
        )
        intro_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(intro_table)
    else:
        story.extend(intro)
    story.append(Spacer(1, 15))

    status = data["status"]
    status_text = (
        "PODGLĄD PRZED WYSYŁKĄ" if status in {"draft", "approved"} else
        "PRZYJĘTA W KSEF" if status in {"accepted", "received"} else
        "ODRZUCONA" if status == "rejected" else "W TRAKCIE WYSYŁKI"
    )
    badge = Table([[p(status_text, label)]], colWidths=[content_width], hAlign="LEFT")
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), palette.badge),
        ("BOX", (0, 0), (-1, -1), .75, palette.line),
        ("LEFTPADDING", (0, 0), (-1, -1), 11), ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([badge, Spacer(1, 22)])

    meta = [("DATA WYSTAWIENIA", invoice["issue_date"])]
    if invoice.get("sale_date"):
        meta.append(("DATA ZAKOŃCZENIA DOSTAWY / USŁUGI", invoice["sale_date"]))
    if invoice.get("issue_place"):
        meta.append(("MIEJSCE WYSTAWIENIA", invoice["issue_place"]))
    meta.extend([("WALUTA", invoice["currency"]),
                 ("TYP DOKUMENTU", "Korekta" if invoice["kind"] == "correction" else "Faktura")])
    meta_rows = []
    for index in range(0, len(meta), 2):
        cells = [Table([[p(name, label)], [p(value, strong)]], colWidths=[content_width / 2 - 10])
                 for name, value in meta[index:index + 2]]
        cells += [""] * (2 - len(cells))
        meta_rows.append(cells)
    meta_table = Table(meta_rows, colWidths=[content_width / 2] * 2, hAlign="LEFT")
    meta_table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), .75, palette.line),
        ("LINEBELOW", (0, -1), (-1, -1), .75, palette.line),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
    ]))
    story.extend([meta_table, Spacer(1, 22)])

    def party(role: str, values: dict[str, str]) -> Table:
        lines = [p(role.upper(), label), p(values["name"], party_name), p(f"NIP {values['nip']}", party_body)]
        lines.extend(p(values[key], party_body) for key in ("address1", "address2") if values.get(key))
        if values.get("country"):
            lines.append(p(f"Kraj: {values['country']}", small))
        if role == "Nabywca":
            for field, description in (("jst", "Jednostka podrzędna JST"), ("gv", "Członek grupy GV")):
                if data["buyer_flags"].get(field):
                    lines.append(p(f"{description}: {data['buyer_flags'][field]}", small))
        table = Table([[line] for line in lines], colWidths=[content_width], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), palette.soft),
            ("BOX", (0, 0), (-1, -1), .75, palette.line),
            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, 0), 10), ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
        ]))
        return table

    story.extend([
        KeepTogether([party("Sprzedawca", data["seller"])]), Spacer(1, 8),
        KeepTogether([party("Nabywca", data["buyer"])]), Spacer(1, 19),
    ])
    correction = data["correction"]
    if correction["reason"] or correction["type"] or correction["invoices"] or correction["period"]:
        story.extend([p("DANE KOREKTY", heading), Spacer(1, 8)])
        for key, title_text in (("reason", "Przyczyna"), ("type", "Skutek korekty"), ("period", "Okres")):
            if correction[key]:
                story.extend([p(title_text.upper(), label), p(correction[key]), Spacer(1, 8)])
        for original in correction["invoices"]:
            story.extend([p("FAKTURA KORYGOWANA", label), p(original["number"], strong)])
            if original["issue_date"]:
                story.append(p(f"Data wystawienia: {original['issue_date']}", small))
            if original["ksef_number"]:
                story.append(p(f"Numer KSeF: {original['ksef_number']}", small))
            story.append(Spacer(1, 9))

    if data["rows"]:
        story.extend([p("Pozycje faktury", heading), Spacer(1, 11)])
        item_rows: list[list[Any]] = [[
            p("Lp.", header_center), p("Towar / usługa", header), p("Ilość", header_center), p("Jm", header_center),
            p("Cena netto", header_right), p("VAT", header_center), p("Kwota VAT", header_right),
            p("Cena brutto", header_right),
        ]]
        for item in data["rows"]:
            rate = str(item["vat_rate"] or "—")
            if rate.isdigit():
                rate += "%"
            item_rows.append([
                p(item["number"], item_center),
                p((item["description"] or "—") + (" · stan przed korektą" if item["before"] else "") +
                  (f" · dostawa {item['sale_date']}" if item["sale_date"] else ""), item_bold),
                p(item["quantity"], item_center), p(item["unit"], item_center),
                p(item["unit_price"], item_right), p(rate, item_center),
                p(item["vat_amount"], item_right), p(item["gross_unit_price"], item_right),
            ])
        item_table = section_table(item_rows, [26, 182, 36, 28, 48, 28, 39, 45])
        item_table.setStyle(TableStyle([
            ("TOPPADDING", (0, 1), (-1, -1), 15),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 15),
            ("LEFTPADDING", (5, 0), (5, -1), 2),
            ("RIGHTPADDING", (5, 0), (5, -1), 2),
            ("LINEBEFORE", (2, 0), (-1, -1), .35, palette.line),
        ]))
        story.extend([item_table, Spacer(1, 22)])

    tax = data["tax"]
    if tax["rows"]:
        story.extend([p("Zestawienie VAT", heading), Spacer(1, 9)])
        tax_rows = [[p("STAWKA VAT", header), p("NETTO", header_right), p("VAT", header_right), p("BRUTTO", header_right)]]
        for rate in tax["rows"]:
            tax_rows.append([p(rate["label"], item_cell), p(rate["net"], item_right),
                             p(rate["vat"], item_right), p(rate["gross"], item_right)])
        tax_rows.append([p("RAZEM", item_bold), p(tax["net"], item_right),
                         p(tax["vat"], item_right), p(tax["gross"], item_right)])
        story.extend([section_table(tax_rows, [168, 88, 88, 88]), Spacer(1, 21)])

    if data["ksef_number"]:
        story.extend([p("NUMER KSEF", label), p(data["ksef_number"], strong), Spacer(1, 19)])

    due = Table([[p(invoice["amount_label"].upper(), due_label)],
                 [p(f"{invoice['gross_amount']} {invoice['currency']}", due_amount)]],
                colWidths=[content_width], hAlign="LEFT")
    due.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), palette.due),
        ("BOX", (0, 0), (-1, -1), .75, colors.HexColor("#888888") if theme == "crt" else palette.due),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, 0), 11), ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
        ("TOPPADDING", (0, 1), (-1, 1), 2), ("BOTTOMPADDING", (0, 1), (-1, 1), 13),
    ]))
    story.extend([due, Spacer(1, 9)])
    settlement = data["settlement"]
    for amount, caption in ((settlement["due"], "DO ZAPŁATY"),
                            (settlement["credit"], "DO ROZLICZENIA LUB ZWROTU")):
        if amount:
            box = Table([[p(caption, label)], [p(f"{amount} {invoice['currency']}", strong)]],
                        colWidths=[content_width], hAlign="LEFT")
            box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), palette.soft),
                ("BOX", (0, 0), (-1, -1), .75, palette.line),
                ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
            ]))
            story.extend([box, Spacer(1, 9)])
    payment = data["payment"]
    details = [("INFORMACJA O PŁATNOŚCI", payment["status"]),
               ("DATA ZAPŁATY", payment["paid_date"]),
               ("TERMIN PŁATNOŚCI", payment["due_date"]),
               ("FORMA PŁATNOŚCI", payment["method"])]
    details = [(name, value) for name, value in details if value]
    if details:
        columns = 3 if len(details) == 3 else 2 if len(details) > 1 else 1
        column_width = content_width / columns
        detail_rows = []
        for index in range(0, len(details), columns):
            pair = [Table([[p(name, label)], [p(value, strong)]], colWidths=[column_width - 24])
                    for name, value in details[index:index + columns]]
            pair += [""] * (columns - len(pair))
            detail_rows.append(pair)
        payment_table = Table(detail_rows, colWidths=[column_width] * columns, hAlign="LEFT")
        payment_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), palette.soft),
            ("BOX", (0, 0), (-1, -1), .75, palette.line),
            ("INNERGRID", (0, 0), (-1, -1), .5, palette.line),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(payment_table)
    for account in payment["accounts"]:
        account_lines = [p("RACHUNEK BANKOWY", label), p(account["number"], strong)]
        for key, prefix in (("bank", "Bank"), ("swift", "SWIFT"), ("description", "Opis"),
                            ("own_bank_account", "Rachunek własny banku")):
            if account[key]:
                account_lines.append(p(f"{prefix}: {account[key]}", small))
        account_table = Table([[line] for line in account_lines], colWidths=[content_width], hAlign="LEFT")
        account_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), .75, palette.line),
            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
        ]))
        story.extend([Spacer(1, 10), account_table])
    for partial in payment["partial"]:
        story.extend([Spacer(1, 8), p("WPŁATA CZĘŚCIOWA", label),
                      p(" · ".join(value for value in [f"{partial['amount']} {invoice['currency']}", partial['date'], partial.get('method', '')] if value))])
    for contract in data.get("contracts", []):
        story.extend([Spacer(1, 8), p("UMOWA", label),
                      p(" · ".join(value for value in [contract['number'], contract['date']] if value))])
    for description in data.get("descriptions", []):
        caption = description['key'] + (f" (pozycja {description['row']})" if description['row'] else "")
        story.extend([Spacer(1, 8), p(caption.upper(), label), p(description['value'])])
    if data["due_difference"]:
        story.extend([Spacer(1, 10), p(
            f"Kwota należności różni się od sumy zestawienia VAT o {data['due_difference']} {invoice['currency']}.", small,
        )])
    def decorate(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFillColor(palette.page)
        canvas.rect(0, 0, page_width, page_height, fill=1, stroke=0)
        if theme == "crt":
            canvas.setStrokeColor(colors.HexColor("#101010"))
            canvas.setLineWidth(.3)
            for y in range(15, page_height, 14):
                canvas.line(0, y, page_width, y)
            canvas.setStrokeColor(palette.line)
            canvas.setLineWidth(.65)
            canvas.rect(14, 14, page_width - 28, page_height - 28, fill=0, stroke=1)
        canvas.setFillColor(colors.HexColor("#373737") if theme == "crt" else palette.accent)
        canvas.rect(0, page_height - (2 if theme == "crt" else 5), page_width,
                    2 if theme == "crt" else 5, fill=1, stroke=0)
        if theme == "light":
            canvas.setFillColor(colors.HexColor("#D3A456"))
            canvas.rect(page_width - 80, page_height - 5, 80, 5, fill=1, stroke=0)
        canvas.setFont(regular_font, 8)
        canvas.setFillColor(palette.muted)
        canvas.drawString(margin + 6, 17, "ASEF · Autor: Szymon Gałka · kontakt@szymongalka.dev · szymongalka.dev")
        canvas.drawRightString(page_width - margin - 6, 17, f"{doc.page:02d}")
        canvas.restoreState()

    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return output.getvalue()
