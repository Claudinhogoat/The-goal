import openpyxl
from openpyxl.styles import (
    PatternFill, Font, Alignment, Border, Side, GradientFill
)
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule
from openpyxl.chart import BarChart, Reference
from datetime import date, timedelta

wb = openpyxl.Workbook()

# ── Colours ──────────────────────────────────────────────────────────────────
SUBJECT_COLOURS = {
    "Maths":              ("1565C0", "E3F2FD"),  # deep blue / light blue
    "English Literature": ("6A1B9A", "F3E5F5"),  # purple / lavender
    "English Language":   ("AD1457", "FCE4EC"),  # pink / blush
    "Physics":            ("00695C", "E0F2F1"),  # teal / mint
    "Biology":            ("2E7D32", "E8F5E9"),  # green / light green
    "Chemistry":          ("E65100", "FFF3E0"),  # orange / cream
    "RS":                 ("4527A0", "EDE7F6"),  # indigo / lavender
    "History":            ("BF360C", "FBE9E7"),  # brick / peach
    "Latin":              ("37474F", "ECEFF1"),  # slate / light grey
    "French":             ("283593", "E8EAF6"),  # navy / pale blue
}
SUBJECTS = list(SUBJECT_COLOURS.keys())

START = date(2026, 3, 29)
END   = date(2026, 5, 6)
TOTAL_DAYS = (END - START).days  # 38

def thin_border(top=False, bottom=False, left=False, right=False):
    s = Side(style="thin")
    return Border(
        top=s if top else None,
        bottom=s if bottom else None,
        left=s if left else None,
        right=s if right else None,
    )

def all_border():
    s = Side(style="thin")
    return Border(top=s, bottom=s, left=s, right=s)

def fill(hex_colour):
    return PatternFill("solid", fgColor=hex_colour)

def bold(size=11, colour="000000"):
    return Font(bold=True, size=size, color=colour)

def center():
    return Alignment(horizontal="center", vertical="center", wrap_text=True)

def set_col_width(ws, col, width):
    ws.column_dimensions[get_column_letter(col)].width = width

def header_row(ws, row, texts, bg, fg="FFFFFF", sizes=None):
    for i, txt in enumerate(texts, 1):
        c = ws.cell(row=row, column=i, value=txt)
        c.fill = fill(bg)
        c.font = Font(bold=True, color=fg, size=(sizes[i-1] if sizes else 11))
        c.alignment = center()
        c.border = all_border()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
ws_dash = wb.active
ws_dash.title = "📊 Dashboard"
ws_dash.sheet_view.showGridLines = False
ws_dash.row_dimensions[1].height = 50

# Title banner
ws_dash.merge_cells("A1:H1")
c = ws_dash["A1"]
c.value = "🎓  GCSE STUDY TRACKER  —  Target: 6 May 2026"
c.fill = fill("1A237E")
c.font = Font(bold=True, size=18, color="FFFFFF")
c.alignment = center()

# Days remaining formula note
ws_dash.merge_cells("A2:H2")
c = ws_dash["A2"]
c.value = f'Start: 29 Mar 2026  |  End: 6 May 2026  |  Total study days: {TOTAL_DAYS}'
c.fill = fill("3949AB")
c.font = Font(bold=False, size=11, color="E8EAF6")
c.alignment = center()

# Section header
ws_dash.merge_cells("A4:H4")
c = ws_dash["A4"]
c.value = "SUBJECT SUMMARY  (auto-totals from your Study Log)"
c.fill = fill("E8EAF6")
c.font = bold(12, "1A237E")
c.alignment = center()

header_row(ws_dash, 5,
           ["Subject", "Total Hours", "Sessions", "Avg Confidence",
            "Last Studied", "Topics Covered", "% of 40 hrs target", "Status"],
           "283593", "FFFFFF")

for row_i, subj in enumerate(SUBJECTS, start=6):
    dk, lt = SUBJECT_COLOURS[subj]
    # Subject name
    c = ws_dash.cell(row=row_i, column=1, value=subj)
    c.fill = fill(lt)
    c.font = Font(bold=True, color=dk, size=11)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    c.border = all_border()

    # Columns B–H: formulas pulling from Study Log
    log = "'📝 Study Log'"
    # Total hours = sum of duration where subject matches
    # We'll use SUMIF on the Study Log sheet (column C = subject, column E = hours)
    formulas = [
        f"=IFERROR(SUMIF({log}!$C:$C,A{row_i},{log}!$E:$E),0)",        # hours
        f"=IFERROR(COUNTIF({log}!$C:$C,A{row_i}),0)",                   # sessions
        f'=IFERROR(AVERAGEIF({log}!$C:$C,A{row_i},{log}!$G:$G),"—")',  # avg conf
        f'=IFERROR(TEXT(MAXIFS({log}!$A:$A,{log}!$C:$C,A{row_i}),"dd mmm"),"—")',  # last studied
        f"=IFERROR(COUNTIFS({log}!$C:$C,A{row_i},{log}!$D:$D,\"<>\"),0)",  # topics
        f"=IFERROR(ROUND(B{row_i}/40*100,1)&\"%\",\"0%\")",             # % of 40h
        f'=IF(B{row_i}>=40,"✅ Done",IF(B{row_i}>=20,"🔥 Good",IF(B{row_i}>=10,"📖 Going","⚠️ Start!")))',
    ]
    for col_i, formula in enumerate(formulas, start=2):
        c = ws_dash.cell(row=row_i, column=col_i, value=formula)
        c.fill = fill("FFFFFF") if col_i % 2 == 0 else fill("F5F5F5")
        c.alignment = center()
        c.border = all_border()
        c.font = Font(size=11)

# Totals row
tot_row = 6 + len(SUBJECTS)
ws_dash.cell(row=tot_row, column=1, value="TOTAL").font = bold(11)
ws_dash.cell(row=tot_row, column=1).fill = fill("283593")
ws_dash.cell(row=tot_row, column=1).font = Font(bold=True, color="FFFFFF")
ws_dash.cell(row=tot_row, column=1).alignment = center()
ws_dash.cell(row=tot_row, column=1).border = all_border()
for col_i in range(2, 9):
    c = ws_dash.cell(row=tot_row, column=col_i)
    if col_i == 2:
        c.value = f"=SUM(B6:B{tot_row-1})"
    elif col_i == 3:
        c.value = f"=SUM(C6:C{tot_row-1})"
    else:
        c.value = ""
    c.fill = fill("3949AB")
    c.font = Font(bold=True, color="FFFFFF")
    c.alignment = center()
    c.border = all_border()

# Column widths
widths = [22, 13, 11, 16, 13, 16, 18, 14]
for i, w in enumerate(widths, 1):
    set_col_width(ws_dash, i, w)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. STUDY LOG
# ═══════════════════════════════════════════════════════════════════════════════
ws_log = wb.create_sheet("📝 Study Log")
ws_log.sheet_view.showGridLines = False
ws_log.freeze_panes = "A3"

ws_log.merge_cells("A1:G1")
c = ws_log["A1"]
c.value = "📝  DAILY STUDY LOG  —  fill in a row every time you study"
c.fill = fill("1B5E20")
c.font = Font(bold=True, size=14, color="FFFFFF")
c.alignment = center()
ws_log.row_dimensions[1].height = 36

header_row(ws_log, 2,
           ["Date", "Day", "Subject", "Topic / What you covered",
            "Hours spent", "Notes / Questions", "Confidence (1–5)"],
           "2E7D32", "FFFFFF")
ws_log.row_dimensions[2].height = 30

# Pre-fill 60 blank rows with a date formula
for r in range(3, 63):
    row_data = ["", "", "", "", "", "", ""]
    for ci, val in enumerate(row_data, 1):
        c = ws_log.cell(row=r, column=ci, value=val)
        c.border = all_border()
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=(ci in (4, 6)))
        if r % 2 == 0:
            c.fill = fill("F1F8E9")

# Data validation: subject dropdown
from openpyxl.worksheet.datavalidation import DataValidation
subj_list = ",".join(SUBJECTS)
dv_subj = DataValidation(type="list", formula1=f'"{subj_list}"', allow_blank=True, showDropDown=False)
dv_subj.sqref = "C3:C200"
ws_log.add_data_validation(dv_subj)

dv_conf = DataValidation(type="list", formula1='"1,2,3,4,5"', allow_blank=True, showDropDown=False)
dv_conf.sqref = "G3:G200"
ws_log.add_data_validation(dv_conf)

# Column widths
col_widths_log = [12, 10, 22, 40, 13, 35, 16]
for i, w in enumerate(col_widths_log, 1):
    set_col_width(ws_log, i, w)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. WEEKLY PLANNER
# ═══════════════════════════════════════════════════════════════════════════════
ws_plan = wb.create_sheet("📅 Weekly Planner")
ws_plan.sheet_view.showGridLines = False

ws_plan.merge_cells("A1:H1")
c = ws_plan["A1"]
c.value = "📅  WEEKLY PLANNER  —  Plan ahead, tick off as you go"
c.fill = fill("4A148C")
c.font = Font(bold=True, size=14, color="FFFFFF")
c.alignment = center()
ws_plan.row_dimensions[1].height = 36

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Build weeks
current = START
week_num = 1
row = 3

while current <= END:
    # Week header
    week_end = min(current + timedelta(days=6), END)
    ws_plan.merge_cells(start_row=row, end_row=row, start_column=1, end_column=8)
    c = ws_plan.cell(row=row, column=1,
                     value=f"  WEEK {week_num}  —  {current.strftime('%d %b')} to {week_end.strftime('%d %b %Y')}")
    c.fill = fill("6A1B9A")
    c.font = Font(bold=True, color="FFFFFF", size=12)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws_plan.row_dimensions[row].height = 24
    row += 1

    # Day columns header
    header_cells = ["Session", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for ci, txt in enumerate(header_cells, 1):
        c = ws_plan.cell(row=row, column=ci, value=txt)
        c.fill = fill("CE93D8")
        c.font = Font(bold=True, color="4A148C", size=10)
        c.alignment = center()
        c.border = all_border()
    row += 1

    # 3 sessions per day
    for session in ["AM", "PM", "Eve"]:
        for ci in range(1, 9):
            c = ws_plan.cell(row=row, column=ci,
                             value=session if ci == 1 else "")
            c.border = all_border()
            c.alignment = center()
            c.fill = fill("F3E5F5") if row % 2 == 0 else fill("FFFFFF")
            if ci == 1:
                c.font = Font(bold=True, color="4A148C", size=10)
        ws_plan.row_dimensions[row].height = 20
        row += 1

    row += 1  # blank gap between weeks
    current += timedelta(days=7)
    week_num += 1

# Column widths
for ci, w in enumerate([10, 14, 14, 14, 14, 14, 14, 14], 1):
    set_col_width(ws_plan, ci, w)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TOPIC CHECKLISTS (one sheet per subject)
# ═══════════════════════════════════════════════════════════════════════════════
TOPIC_MAP = {
    "Maths": [
        "Number & Fractions", "Algebra – Equations", "Algebra – Graphs",
        "Ratio & Proportion", "Percentages", "Statistics & Probability",
        "Geometry – Angles", "Geometry – Circles", "Trigonometry",
        "Vectors", "Sequences", "Transformations",
    ],
    "English Literature": [
        "Macbeth – Plot", "Macbeth – Characters", "Macbeth – Themes",
        "A Christmas Carol – Plot", "A Christmas Carol – Themes",
        "Power & Conflict poems (x15)", "Unseen poem technique",
        "Essay structure & PEE", "Quotation bank",
    ],
    "English Language": [
        "Reading Q1 – Find & copy", "Reading Q2 – Language analysis",
        "Reading Q3 – Structure", "Reading Q4 – Evaluate",
        "Writing – Descriptive", "Writing – Narrative",
        "Writing – Persuasive/Argue", "Spelling, punctuation & grammar",
    ],
    "Physics": [
        "Forces & Motion", "Energy", "Waves", "Electricity",
        "Magnetism & Electromagnetism", "Particle Model",
        "Atomic Structure", "Space (if applicable)",
        "Required practicals",
    ],
    "Biology": [
        "Cell Biology", "Organisation", "Infection & Response",
        "Bioenergetics", "Homeostasis & Response",
        "Inheritance & Variation", "Ecology",
        "Required practicals",
    ],
    "Chemistry": [
        "Atomic Structure", "Bonding", "Quantitative Chemistry",
        "Chemical Changes", "Energy Changes", "Rate & Equilibrium",
        "Organic Chemistry", "Chemical Analysis",
        "Earth & Atmosphere", "Required practicals",
    ],
    "RS": [
        "Christian beliefs", "Christian practices",
        "Muslim beliefs", "Muslim practices",
        "Relationships & Families", "Religion & Life",
        "Crime & Punishment", "Peace & Conflict",
    ],
    "History": [
        "Medicine Through Time – key turning points",
        "Medicine Through Time – Western Front",
        "Early Elizabethan England",
        "Superpower Relations & Cold War",
        "Source/Interpretation skills",
        "Extended writing practice",
    ],
    "Latin": [
        "Vocabulary (list 1–5)", "Vocabulary (list 6–10)",
        "Noun declensions", "Verb conjugations",
        "Pronouns & adjectives", "Translation practice",
        "Prose comprehension", "Verse (if applicable)",
    ],
    "French": [
        "Identity & Culture", "Local, National & Global areas",
        "Current & Future Study / Employment",
        "Listening skills", "Reading skills",
        "Writing – short tasks", "Writing – long tasks",
        "Speaking – conversation", "Grammar review",
    ],
}

for subj in SUBJECTS:
    dk, lt = SUBJECT_COLOURS[subj]
    ws = wb.create_sheet(subj[:3].upper() + " Topics")
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:E1")
    c = ws["A1"]
    c.value = f"  {subj.upper()} — Topic Checklist"
    c.fill = fill(dk)
    c.font = Font(bold=True, size=14, color="FFFFFF")
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 36

    header_row(ws, 2,
               ["#", "Topic / Area", "1st Pass ✓", "2nd Pass ✓", "Confidence (1–5)"],
               dk, "FFFFFF")
    ws.row_dimensions[2].height = 28

    topics = TOPIC_MAP.get(subj, [])
    for ti, topic in enumerate(topics, start=1):
        r = ti + 2
        bg = lt if ti % 2 == 0 else "FFFFFF"
        vals = [ti, topic, "", "", ""]
        for ci, val in enumerate(vals, 1):
            c = ws.cell(row=r, column=ci, value=val)
            c.fill = fill(bg)
            c.border = all_border()
            c.alignment = Alignment(
                horizontal="center" if ci != 2 else "left",
                vertical="center",
                indent=1 if ci == 2 else 0,
            )
            if ci == 2:
                c.font = Font(size=11)

    # Extra blank rows
    for extra in range(len(topics)+1, len(topics)+6):
        r = extra + 2
        for ci in range(1, 6):
            c = ws.cell(row=r, column=ci)
            c.border = all_border()
            c.fill = fill("FAFAFA")

    # Confidence data validation
    dv = DataValidation(type="list", formula1='"1,2,3,4,5"', allow_blank=True)
    dv.sqref = f"E3:E{len(topics)+7}"
    ws.add_data_validation(dv)

    # Column widths
    for ci, w in enumerate([5, 38, 12, 12, 16], 1):
        set_col_width(ws, ci, w)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. COUNTDOWN & MOTIVATION
# ═══════════════════════════════════════════════════════════════════════════════
ws_cd = wb.create_sheet("🚀 Countdown")
ws_cd.sheet_view.showGridLines = False

ws_cd.merge_cells("A1:D1")
c = ws_cd["A1"]
c.value = "🚀  EXAM COUNTDOWN"
c.fill = fill("B71C1C")
c.font = Font(bold=True, size=18, color="FFFFFF")
c.alignment = center()
ws_cd.row_dimensions[1].height = 50

# Key dates
milestones = [
    ("Today",        "29 Mar 2026", "🗓️"),
    ("Half way",     "17 Apr 2026", "⚡"),
    ("2 weeks left", "22 Apr 2026", "🔥"),
    ("1 week left",  "29 Apr 2026", "😤"),
    ("EXAM DAY",     "6 May 2026",  "🎯"),
]

ws_cd.cell(row=3, column=1, value="Milestone").font = bold(12, "B71C1C")
ws_cd.cell(row=3, column=2, value="Date").font = bold(12, "B71C1C")
ws_cd.cell(row=3, column=3, value="").font = bold(12)
for r, (label, dt, emoji) in enumerate(milestones, start=4):
    ws_cd.cell(row=r, column=1, value=label).font = Font(size=12)
    ws_cd.cell(row=r, column=2, value=dt).font = Font(size=12)
    ws_cd.cell(row=r, column=3, value=emoji)
    for ci in range(1, 4):
        ws_cd.cell(row=r, column=ci).border = all_border()
        ws_cd.cell(row=r, column=ci).alignment = center()
        ws_cd.cell(row=r, column=ci).fill = fill("FFEBEE" if r % 2 == 0 else "FFFFFF")

# Motivational quotes
quotes = [
    ('"The secret of getting ahead is getting started."', "— Mark Twain"),
    ('"Don\'t watch the clock; do what it does. Keep going."', "— Sam Levenson"),
    ('"You don\'t have to be great to start, but you have to start to be great."', "— Zig Ziglar"),
    ('"Success is the sum of small efforts, repeated day in and day out."', "— Robert Collier"),
    ('"Push yourself, because no one else is going to do it for you."', ""),
]
ws_cd.cell(row=11, column=1, value="Daily Motivation 💪").font = bold(13, "B71C1C")
for r, (q, attr) in enumerate(quotes, start=12):
    c = ws_cd.cell(row=r, column=1, value=q)
    c.font = Font(italic=True, size=11, color="4A148C")
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws_cd.merge_cells(start_row=r, end_row=r, start_column=1, end_column=4)
    c2 = ws_cd.cell(row=r+1, column=1, value=attr)
    c2.font = Font(size=10, color="888888", italic=True)
    ws_cd.row_dimensions[r].height = 30

for ci, w in enumerate([30, 16, 6, 6], 1):
    set_col_width(ws_cd, ci, w)


# ── Tab colours ──────────────────────────────────────────────────────────────
ws_dash.sheet_properties.tabColor  = "1A237E"
ws_log.sheet_properties.tabColor   = "1B5E20"
ws_plan.sheet_properties.tabColor  = "4A148C"
ws_cd.sheet_properties.tabColor    = "B71C1C"

# ── Save ─────────────────────────────────────────────────────────────────────
out = "/home/user/The-goal/GCSE_Study_Tracker.xlsx"
wb.save(out)
print(f"Saved → {out}")
