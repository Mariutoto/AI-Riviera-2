from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "deliverables"
OUTPUT_PATH = OUTPUT_DIR / "AI_Riviera_PostgreSQL_Guide.pdf"

NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#2E6F9E")
LIGHT_BLUE = colors.HexColor("#EAF3F8")
TEAL = colors.HexColor("#2A8C82")
LIGHT_TEAL = colors.HexColor("#E8F5F2")
GOLD = colors.HexColor("#D89B2B")
LIGHT_GOLD = colors.HexColor("#FBF3DF")
PURPLE = colors.HexColor("#7357A4")
LIGHT_PURPLE = colors.HexColor("#F1ECF8")
GREY = colors.HexColor("#5C6873")
LIGHT_GREY = colors.HexColor("#F4F6F8")
LINE = colors.HexColor("#C7D0D9")
WHITE = colors.white


def register_fonts():
    pdfmetrics.registerFont(TTFont("Arial", r"C:\Windows\Fonts\arial.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-Bold", r"C:\Windows\Fonts\arialbd.ttf"))


class NumberedCanvasMixin:
    pass


def draw_page(canvas, doc):
    canvas.saveState()
    width, height = landscape(A4)
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 12 * mm, width - 18 * mm, 12 * mm)
    canvas.setFont("Arial", 8)
    canvas.setFillColor(GREY)
    canvas.drawString(18 * mm, 7.5 * mm, "AI Riviera — PostgreSQL Relationship Guide")
    canvas.drawRightString(width - 18 * mm, 7.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


class SchemaMap(Flowable):
    def __init__(self, width=245 * mm, height=128 * mm):
        super().__init__()
        self.width = width
        self.height = height

    def box(self, canvas, x, y, w, h, title, lines, fill, accent):
        canvas.setFillColor(fill)
        canvas.setStrokeColor(accent)
        canvas.setLineWidth(1)
        canvas.roundRect(x, y, w, h, 5, fill=1, stroke=1)
        canvas.setFillColor(accent)
        canvas.roundRect(x, y + h - 18, w, 18, 5, fill=1, stroke=0)
        canvas.rect(x, y + h - 18, w, 8, fill=1, stroke=0)
        canvas.setFillColor(WHITE)
        canvas.setFont("Arial-Bold", 8.2)
        canvas.drawString(x + 6, y + h - 12, title)
        canvas.setFillColor(NAVY)
        canvas.setFont("Arial", 6.8)
        cursor = y + h - 29
        for line in lines:
            canvas.drawString(x + 6, cursor, line)
            cursor -= 10

    def relation(self, canvas, x1, y1, x2, y2, left="1", right="N"):
        canvas.setStrokeColor(GREY)
        canvas.setLineWidth(1.1)
        canvas.line(x1, y1, x2, y2)
        canvas.setFillColor(WHITE)
        canvas.circle(x1, y1, 5, fill=1, stroke=0)
        canvas.circle(x2, y2, 5, fill=1, stroke=0)
        canvas.setFillColor(GREY)
        canvas.setFont("Arial-Bold", 6.5)
        canvas.drawCentredString(x1, y1 - 2, left)
        canvas.drawCentredString(x2, y2 - 2, right)

    def draw(self):
        c = self.canv
        # Document and retrieval cluster
        self.box(c, 6, 272, 130, 72, "documents", ["PK id", "source_url, title, type", "hashes, date, metadata"], LIGHT_BLUE, BLUE)
        self.box(c, 178, 272, 145, 72, "document_chunks", ["PK chunk_id", "FK document_id", "content, search_vector", "embedding_vector"], LIGHT_BLUE, BLUE)
        self.relation(c, 136, 307, 178, 307)

        # Political cluster
        self.box(c, 6, 144, 140, 82, "political_objects", ["PK object_id", "type, title, dates", "status and lifecycle", "structured facts"], LIGHT_TEAL, TEAL)
        self.box(c, 187, 166, 150, 60, "political_object_documents", ["FK object_id", "FK document_id", "relation_type"], LIGHT_TEAL, TEAL)
        self.box(c, 380, 166, 140, 60, "political_object_people", ["FK object_id", "FK person_id", "role, party_at_time"], LIGHT_TEAL, TEAL)
        self.box(c, 560, 166, 120, 60, "people", ["PK person_id", "name, party", "variants, roles"], LIGHT_TEAL, TEAL)
        self.relation(c, 146, 185, 187, 196)
        self.relation(c, 337, 196, 380, 196)
        self.relation(c, 520, 196, 560, 196, left="N", right="1")
        self.relation(c, 72, 272, 242, 226, left="1", right="N")

        # Finance cluster
        self.box(c, 6, 20, 145, 72, "financial_summary_tables", ["PK id", "FK document_id", "year, metric, currency"], LIGHT_GOLD, GOLD)
        self.box(c, 190, 20, 145, 72, "financial_summary_rows", ["PK id", "FK table_id", "service and values"], LIGHT_GOLD, GOLD)
        self.box(c, 380, 20, 155, 72, "financial_account_lines", ["PK id", "FK document_id", "account, service, values"], LIGHT_GOLD, GOLD)
        self.relation(c, 151, 56, 190, 56)
        self.relation(c, 70, 272, 70, 92)
        self.relation(c, 115, 272, 457, 92)

        # Operations cluster
        self.box(c, 575, 70, 118, 64, "ingestion_runs", ["PK id", "status, timing", "run statistics"], LIGHT_PURPLE, PURPLE)
        self.box(c, 575, 0, 118, 54, "ingestion_logs", ["PK id", "FK run_id", "level, message"], LIGHT_PURPLE, PURPLE)
        self.relation(c, 634, 70, 634, 54)


class SimpleRelationship(Flowable):
    def __init__(self, width=236 * mm, height=45 * mm):
        super().__init__()
        self.width = width
        self.height = height

    def draw_box(self, c, x, y, w, h, title, rows):
        c.setFillColor(LIGHT_BLUE)
        c.setStrokeColor(BLUE)
        c.roundRect(x, y, w, h, 4, fill=1, stroke=1)
        c.setFillColor(BLUE)
        c.rect(x, y + h - 17, w, 17, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Arial-Bold", 8)
        c.drawString(x + 6, y + h - 11.5, title)
        c.setFont("Arial", 7)
        c.setFillColor(NAVY)
        cursor = y + h - 28
        for row in rows:
            c.drawString(x + 6, cursor, row)
            cursor -= 10

    def draw(self):
        c = self.canv
        self.draw_box(c, 10, 10, 165, 100, "documents", ["id = D42  ← primary key", "title = Budget 2026", "city = La Tour-de-Peilz"])
        self.draw_box(c, 330, 10, 190, 100, "document_chunks", ["chunk_id = D42-C1", "document_id = D42  ← foreign key", "content = first passage …", "", "chunk_id = D42-C2", "document_id = D42"])
        c.setStrokeColor(GREY)
        c.setLineWidth(1.5)
        c.line(175, 60, 330, 60)
        c.setFont("Arial-Bold", 8)
        c.setFillColor(GREY)
        c.drawString(190, 66, "1 document")
        c.drawRightString(315, 66, "many chunks")
        c.setFillColor(WHITE)
        c.circle(180, 60, 8, fill=1, stroke=0)
        c.circle(325, 60, 8, fill=1, stroke=0)
        c.setFillColor(GREY)
        c.drawCentredString(180, 57, "1")
        c.drawCentredString(325, 57, "N")


def build_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="TitleCustom", parent=styles["Title"], fontName="Arial-Bold",
        fontSize=27, leading=31, textColor=NAVY, alignment=TA_CENTER, spaceAfter=10,
    ))
    styles.add(ParagraphStyle(
        name="Subtitle", parent=styles["Normal"], fontName="Arial",
        fontSize=12, leading=17, textColor=GREY, alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        name="H1Custom", parent=styles["Heading1"], fontName="Arial-Bold",
        fontSize=18, leading=22, textColor=NAVY, spaceBefore=2, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="H2Custom", parent=styles["Heading2"], fontName="Arial-Bold",
        fontSize=12.5, leading=15, textColor=BLUE, spaceBefore=4, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="BodyCustom", parent=styles["BodyText"], fontName="Arial",
        fontSize=9.2, leading=13, textColor=NAVY, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="Small", parent=styles["BodyText"], fontName="Arial",
        fontSize=8, leading=11, textColor=GREY,
    ))
    styles.add(ParagraphStyle(
        name="Callout", parent=styles["BodyText"], fontName="Arial-Bold",
        fontSize=10, leading=14, textColor=NAVY, backColor=LIGHT_GOLD,
        borderColor=GOLD, borderWidth=0.8, borderPadding=8, spaceBefore=5, spaceAfter=8,
    ))
    return styles


def bullet(text, styles):
    return Paragraph(f"• {text}", styles["BodyCustom"])


def info_table(rows, widths, header=True):
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    commands = [
        ("FONTNAME", (0, 0), (-1, -1), "Arial"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 10.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [WHITE, LIGHT_GREY]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Arial-Bold"),
        ]
    table.setStyle(TableStyle(commands))
    return table


def build_pdf():
    register_fonts()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    page_width, page_height = landscape(A4)
    doc = BaseDocTemplate(
        str(OUTPUT_PATH), pagesize=(page_width, page_height),
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=17 * mm,
        title="AI Riviera PostgreSQL Relationship Guide",
        author="AI Riviera",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="guide", frames=[frame], onPage=draw_page)])
    s = build_styles()
    story = []

    # Cover
    story += [Spacer(1, 34 * mm)]
    story += [Paragraph("AI Riviera PostgreSQL Guide", s["TitleCustom"])]
    story += [Paragraph("How the tables store municipal documents, political objects, people and financial data", s["Subtitle"])]
    story += [Spacer(1, 14 * mm)]
    story += [Paragraph(
        "A relational database is a collection of tables connected by identifiers. "
        "The important idea is simple: store each fact once, then link related rows instead of copying the same data everywhere.",
        s["Callout"],
    )]
    story += [Spacer(1, 9 * mm)]
    cover_rows = [
        ["TABLE", "A named collection of similar records"],
        ["ROW", "One record, such as one document or one person"],
        ["COLUMN", "One attribute, such as title, date or status"],
        ["PRIMARY KEY", "The unique identifier of a row"],
        ["FOREIGN KEY", "A stored identifier that points to a row in another table"],
    ]
    story += [info_table(cover_rows, [42 * mm, 135 * mm], header=False)]
    story += [PageBreak()]

    # Fundamentals
    story += [Paragraph("1. How table relationships work", s["H1Custom"])]
    story += [Paragraph(
        "Imagine one municipal PDF. The <b>documents</b> table stores the PDF once. The PDF is divided into several searchable passages, so the <b>document_chunks</b> table stores many rows that all point back to that document.",
        s["BodyCustom"],
    )]
    story += [SimpleRelationship(), Spacer(1, 4 * mm)]
    columns = [
        [Paragraph("Primary key (PK)", s["H2Custom"]), Paragraph("A unique value that identifies one row. <b>documents.id</b> identifies one document.", s["BodyCustom"])],
        [Paragraph("Foreign key (FK)", s["H2Custom"]), Paragraph("A value copied into another table to create a link. <b>document_chunks.document_id</b> points to <b>documents.id</b>.", s["BodyCustom"])],
        [Paragraph("One-to-many (1:N)", s["H2Custom"]), Paragraph("One document can contain many chunks, but each chunk belongs to one document.", s["BodyCustom"])],
    ]
    layout = Table([columns], colWidths=[79 * mm] * 3)
    layout.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    story += [layout]
    story += [Paragraph(
        "Deletion rule: the schema uses <b>ON DELETE CASCADE</b> for chunks. If a document is deleted, its chunks are automatically deleted too, preventing orphaned data.",
        s["Callout"],
    )]
    story += [PageBreak()]

    # Whole schema
    story += [Paragraph("2. AI Riviera database map", s["H1Custom"])]
    story += [Paragraph(
        "The schema has four areas: document retrieval (blue), political knowledge (green), financial extraction (gold), and ingestion monitoring (purple). Labels 1 and N show one-to-many relationships.",
        s["BodyCustom"],
    )]
    story += [SchemaMap()]
    story += [Paragraph(
        "The <b>city</b> value is currently stored directly in several tables. There is no separate cities table in the active schema.",
        s["Small"],
    )]
    story += [PageBreak()]

    # Core search details
    story += [Paragraph("3. Core document and search tables", s["H1Custom"])]
    core_rows = [
        ["Table", "Purpose", "Important fields", "Relationship"],
        ["documents", "One row per official source document.", "id, city, source_url, title, doc_type, hashes, date, metadata", "Parent of chunks, political links and financial rows."],
        ["document_chunks", "Searchable passages extracted from each document.", "chunk_id, document_id, content, search_vector, embedding_vector", "Many chunks belong to one document."],
    ]
    story += [info_table(core_rows, [38 * mm, 68 * mm, 82 * mm, 55 * mm])]
    story += [Spacer(1, 5 * mm)]
    story += [Paragraph("How a question uses these tables", s["H2Custom"])]
    for item in [
        "The user asks a question.",
        "PostgreSQL searches <b>document_chunks.search_vector</b> for French keyword matches.",
        "When real embeddings are added, pgvector searches <b>embedding_vector</b> for passages with similar meaning.",
        "The winning chunks point through <b>document_id</b> to the parent document.",
        "The answer shows the document title and official source URL so the user can verify it.",
    ]:
        story += [bullet(item, s)]
    story += [Paragraph(
        "Current detail: <b>embedding</b> is also stored as JSONB, while <b>embedding_vector</b> is the optional pgvector column used for efficient cosine-similarity search. Real BGE-M3 embeddings are a proposed improvement; the current fallback vectors are not strong semantic embeddings.",
        s["Callout"],
    )]
    story += [Paragraph("Why hashes are stored", s["H2Custom"])]
    story += [Paragraph(
        "document_hash and content_hash help detect changes. If a PDF has not changed, the ingestion pipeline can avoid processing and indexing it again.",
        s["BodyCustom"],
    )]
    story += [PageBreak()]

    # Political relationships
    story += [Paragraph("4. Political objects and many-to-many relationships", s["H1Custom"])]
    story += [Paragraph(
        "A motion can have several authors, and one person can author several motions. That is a <b>many-to-many</b> relationship. Relational databases represent it with a junction table.",
        s["BodyCustom"],
    )]
    political_rows = [
        ["Table", "What one row represents", "Key relationship"],
        ["political_objects", "One motion, postulate, interpellation or other political object.", "Referenced by the two junction tables."],
        ["people", "One normalized person, with name variants and party information.", "Connected to objects through political_object_people."],
        ["political_object_people", "One person’s role on one political object.", "object_id → political_objects; person_id → people."],
        ["political_object_documents", "One document’s relationship to one political object.", "object_id → political_objects; document_id → documents."],
    ]
    story += [info_table(political_rows, [48 * mm, 105 * mm, 90 * mm])]
    story += [Spacer(1, 6 * mm)]
    story += [Paragraph("Example", s["H2Custom"])]
    example_rows = [
        ["political_objects", "OBJ-17", "Motion: Safer school route"],
        ["people", "P-04", "Alice Martin"],
        ["people", "P-09", "Marc Dupont"],
        ["political_object_people", "OBJ-17 + P-04", "role = author"],
        ["political_object_people", "OBJ-17 + P-09", "role = co-author"],
    ]
    story += [info_table(example_rows, [58 * mm, 58 * mm, 112 * mm], header=False)]
    story += [Paragraph(
        "The names are not copied into every political object. The junction rows store the relationships, which reduces duplication and makes updates safer.",
        s["Callout"],
    )]
    story += [PageBreak()]

    # Finance and operations
    story += [Paragraph("5. Financial and ingestion tables", s["H1Custom"])]
    finance_rows = [
        ["Table", "Purpose", "Relationship"],
        ["financial_summary_tables", "Represents one extracted summary table from a budget or account document.", "Many summary tables can belong to one document."],
        ["financial_summary_rows", "Stores the individual services and values inside a summary table.", "Many rows belong to one summary table."],
        ["financial_account_lines", "Stores detailed account lines for structured financial questions.", "Many account lines can belong to one document."],
        ["ingestion_runs", "Records one complete import or scheduled update.", "Parent of ingestion logs."],
        ["ingestion_logs", "Stores warnings, errors and information generated during a run.", "Many logs belong to one run."],
    ]
    story += [info_table(finance_rows, [50 * mm, 120 * mm, 73 * mm])]
    story += [Spacer(1, 6 * mm)]
    story += [Paragraph("How to read the schema quickly", s["H2Custom"])]
    checklist = [
        "Start with the table’s primary key: it tells you what makes a row unique.",
        "Look for columns ending in <b>_id</b>: they are usually foreign keys pointing to another table.",
        "A foreign key is normally on the <b>many</b> side of a one-to-many relationship.",
        "A table containing two foreign keys often acts as a junction table for a many-to-many relationship.",
        "JSONB holds flexible metadata; normal columns hold facts that need reliable filtering and sorting.",
        "Indexes do not create relationships. They make searches and joins faster.",
    ]
    for item in checklist:
        story += [bullet(item, s)]
    story += [Paragraph(
        "Mental model: documents are the evidence, chunks make that evidence searchable, political tables organize exact facts, financial tables make numbers queryable, and ingestion tables tell us whether the data pipeline worked.",
        s["Callout"],
    )]

    doc.build(story)
    return OUTPUT_PATH


if __name__ == "__main__":
    print(build_pdf())
