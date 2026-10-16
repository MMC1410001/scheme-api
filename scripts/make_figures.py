"""Draw the figures in docs/figures/ used by docs/Scheme_API_Requirements_and_Solution.md.

    uv run --with matplotlib --with pillow python scripts/make_figures.py
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from pathlib import Path
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "docs" / "figures"
C = {  # fill, edge
    "ext": ("#EEF0F3", "#6B7280"),
    "store": ("#E7F5EC", "#2F855A"),
    "ours": ("#E8F0FE", "#2B5FD9"),
    "client": ("#FFF4E5", "#C05621"),
    "warn": ("#FDECEC", "#C53030"),
    "note": ("#FFFFFF", "#9CA3AF"),
}
INK = "#1F2937"


def canvas(w=10, h=5.6):
    fig, ax = plt.subplots(figsize=(w, h), dpi=120)
    ax.set_xlim(0, 100); ax.set_ylim(0, 56 * h / 5.6); ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, text, kind="ours", size=9, bold_first=True, align="center"):
    fill, edge = C[kind]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2",
                                fc=fill, ec=edge, lw=1.4))
    lines = text.split("\n")
    tx = x + w / 2 if align == "center" else x + 1.2
    ha = "center" if align == "center" else "left"
    if bold_first:
        ax.text(tx, y + h - 1.6, lines[0], ha=ha, va="top", fontsize=size + 0.5, fontweight="bold", color=INK)
        if len(lines) > 1:
            ax.text(tx, y + h - 4.6, "\n".join(lines[1:]), ha=ha, va="top", fontsize=size - 0.5, color=INK, linespacing=1.35)
    else:
        ax.text(tx, y + h / 2, text, ha=ha, va="center", fontsize=size, color=INK, linespacing=1.35)
    return (x, y, w, h)


def arrow(ax, p1, p2, label="", color="#4B5563", style="-|>", dashed=False, lpos=0.5, loff=(0, 1.2), size=8):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=13, lw=1.3, color=color,
                                 linestyle=(0, (4, 3)) if dashed else "-", shrinkA=2, shrinkB=2))
    if label:
        mx = p1[0] + (p2[0] - p1[0]) * lpos + loff[0]
        my = p1[1] + (p2[1] - p1[1]) * lpos + loff[1]
        ax.text(mx, my, label, ha="center", va="bottom", fontsize=size, color="#374151",
                bbox=dict(fc="white", ec="none", pad=0.6))


def title(ax, text, y=None):
    ax.text(1, (y if y is not None else ax.get_ylim()[1] - 1.5), text, fontsize=11.5, fontweight="bold", color=INK, va="top")


def save(fig, name):
    path = OUT / f"{name}.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white", pad_inches=0.15)
    plt.close(fig)
    Image.open(path).convert("RGB").quantize(colors=48, method=Image.Quantize.MEDIANCUT).save(path, optimize=True)
    print(f"{name}.png {path.stat().st_size // 1024} KB")


# Figure 1 — business context
fig, ax = canvas(10, 5.2)
title(ax, "Figure 1 — Business context")
box(ax, 1, 12, 14, 16, "Client SAP\n(Vistex\nschemes)", "ext")
box(ax, 22, 12, 19, 16, "Cloud Storage (GCS)\nSAP exports by\nyear / month folder\n~550 files a month", "store")
box(ax, 48, 12, 19, 16, "Scheme API\n(this project)\nAPI 1: monthly schemes", "ours")
box(ax, 83, 12, 15, 16, "Client team\nsystems / tools\ncall the APIs", "client")
arrow(ax, (15.8, 20), (21.2, 20), "daily CSV\nexports", loff=(0, 0.8))
arrow(ax, (41.8, 20), (47.2, 20), "cron\nload", loff=(0, 0.8))
arrow(ax, (67.8, 22.5), (82.2, 22.5), "dealer + month", loff=(0, 0.6))
arrow(ax, (82.2, 17.5), (67.8, 17.5), "response: JSON rows", loff=(0, -4.2))
ax.text(1, 4, "Grey: existing systems   Green: storage   Blue: built in this project   Orange: API consumer",
        fontsize=8, color="#6B7280")
save(fig, "fig1_business_context")

# Figure 2 — input to output transformation (API 1)
fig, ax = canvas(10, 4.8)
title(ax, "Figure 2 — API 1: from SAP export to API response")
box(ax, 1, 10, 28, 29, "Input: SAP export (CSV)\n\n53 columns, 66 components\nOnly rows SAP updated that day\nUp to 50,000 rows per file\nDates DD/MM/YYYY\nNegatives as \"882.00-\"\nKey: Customer + Agreement\n        + Deal Suffix", "ext", align="left")
box(ax, 36, 10, 28, 29, "Business rules\n\nKeep 10 components, exact name\nNewest \"Last Updated\" wins\nQualification:\n  GSR (Q): EB slabs, Value,\n  Value %, Value FA\n  GSR (B): EB FULL MONTH\n  Invoiced Qty (Q): Luxury,\n  Premium\nOnly the requested month", "ours", align="left")
box(ax, 71, 10, 28, 29, "Output: API 1 response\n\n10 template fields +\nNextSlabRate per row\nUp to 10 rows per dealer:\n  EB SLAB 1, 2, 3, VALUE,\n  VALUE %, VALUE FA,\n  LUXURY, PREMIUM 01, 02,\n  EB FULL MONTH\nOrdered by ToDate, then\ntemplate order", "client", align="left")
arrow(ax, (29.8, 24.5), (35.2, 24.5))
arrow(ax, (64.8, 24.5), (70.2, 24.5))
save(fig, "fig2_input_output")

# Figure 3 — solution architecture
fig, ax = canvas(10, 6.4)
title(ax, "Figure 3 — Solution architecture")
box(ax, 1, 46, 17, 10, "SAP / the client\nexport job", "ext")
box(ax, 24, 31, 20, 25, "GCS bucket\n\nsap-exports/YYYY/MM/\n  sap-exports_*.csv", "store", align="left")
box(ax, 50, 38, 22, 18, "Ingestion job (cron)\npython -m ingestion.run\n\n1 list new files\n2 parse + validate\n3 upsert newest rows", "ours", align="left")
box(ax, 50, 4, 22, 27, "PostgreSQL\n\nscheme_records\n  (latest row per dealer,\n   agreement, suffix,\n   period)\ningested_files\n  (which files are loaded)", "store", align="left")
box(ax, 78, 22, 21, 34, "FastAPI service\nuvicorn x 4 workers\n\nSecurity middleware\n  headers, 4 KB limit\nBearer JWT check\nRoutes /v1/auth/token,\n  /v1/schemes/...\nRepository\n  pooled, parameterised\n  SQL (pool -> 503)", "ours", align="left")
box(ax, 78, 4, 21, 12, "the client systems\ntoken once an hour;\nPOST {dealer_code,\nmonth, year} <- rows", "client")
arrow(ax, (18.8, 51), (23.2, 51))
arrow(ax, (44.8, 47), (49.2, 47), "read", loff=(0, 0.6))
arrow(ax, (61, 37.6), (61, 31.6), "upsert", lpos=0.5, loff=(4.5, -1.5))
arrow(ax, (77.2, 26), (72.8, 22), "SELECT", lpos=0.5, loff=(0, 0.6))
arrow(ax, (88.5, 16.6), (88.5, 21.4), "", style="<|-|>")
ax.text(90, 18.2, "HTTPS via gateway\n(TLS, rate limit)", fontsize=7.5, color="#374151")
save(fig, "fig3_architecture")

# Figure 4 — ingestion flow
fig, ax = canvas(10, 5.4)
title(ax, "Figure 4 — Ingestion job: what happens to each file")
steps = [
    ("Cron starts\n(every\n30 min)", "ext"),
    ("List files in\nthis month's\nfolder", "ours"),
    ("Read CSV,\ncheck\ncolumns", "ours"),
    ("One of the\n10 compo-\nnents?", "ours"),
    ("Parse dates\nand\nnumbers", "ours"),
    ("Upsert if\nLast Updated\nis newer", "store"),
    ("Mark file\nloaded,\nlog summary", "ours"),
]
xs = [1, 15, 29, 43, 57, 71, 85]
W = 11
for (text, kind), x in zip(steps, xs):
    box(ax, x, 28, W, 13, text, kind, size=8, bold_first=False)
for a, b in zip(xs, xs[1:]):
    arrow(ax, (a + W + 0.5, 34.5), (b - 0.5, 34.5))
outs = [
    (15, "Already\nloaded\n-> skip file", "note"),
    (29, "Column\nmissing ->\nfile fails", "warn"),
    (43, "Other (e.g.\nALWD EB2)\n-> dropped", "note"),
    (57, "Bad value\n-> row\nskipped", "warn"),
    (71, "Older update\n-> stored\nrow kept", "note"),
]
for x, text, kind in outs:
    box(ax, x, 5, W, 13, text, kind, size=7.8, bold_first=False)
    arrow(ax, (x + W / 2, 27.4), (x + W / 2, 18.6), dashed=True)
ax.text(1, 1, "Every skip, failure and drop is counted in the job's log summary; a failed file makes the job exit with code 1.", fontsize=7.8, color="#6B7280")
save(fig, "fig4_ingestion_flow")

# Figure 5 — request lifecycle
fig, ax = canvas(10, 5.0)
title(ax, "Figure 5 — API request lifecycle and responses")
chain = [
    ("the client request\nPOST /v1/\nschemes/...", "client"),
    ("Body\n<= 4 KB?", "ours"),
    ("Bearer token\nvalid?", "ours"),
    ("body valid?\n(dealer, month)", "ours"),
    ("Query the\nrequested month", "store"),
    ("200 OK\nJSON array\n([] if none)", "client"),
]
xs = [1, 17.5, 34, 50.5, 67, 83.5]
W = 13
for (text, kind), x in zip(chain, xs):
    box(ax, x, 25, W, 12, text, kind, size=8.2, bold_first=False)
for i in range(len(xs) - 1):
    arrow(ax, (xs[i] + W + 0.5, 31), (xs[i + 1] - 0.5, 31), "yes" if 0 < i < 4 else "", loff=(0, 0.5), size=7.5)
errs = [(17.5, "413\nToo large"), (34, "401 bad or\nexpired token\n403 scope"), (50.5, "422 Validation\nerror (input\nnot echoed)"), (67, "503 + Retry-\nAfter if DB\npool busy")]
for x, text in errs:
    box(ax, x, 5, W, 12, text, "warn", size=7.8, bold_first=False)
    arrow(ax, (x + W / 2, 24.4), (x + W / 2, 17.6), "no" if "503" not in text else "", dashed=True, lpos=0.5, loff=(2.5, -1), size=7.5)
ax.text(1, 0.8, "Every response carries security headers: nosniff, HSTS, CSP, X-Frame-Options DENY, Cache-Control no-store.", fontsize=7.8, color="#6B7280")
save(fig, "fig5_request_lifecycle")

# Figure 6 — data model
fig, ax = canvas(10, 5.4)
title(ax, "Figure 6 — Data model (PostgreSQL)")
box(ax, 1, 9, 38, 37, "scheme_records  (API 1)\n\nKEY year, month, dealer_code,\n     agreement, deal_suffix,\n     valid_from, valid_to\ncomponent  (one of 8)\nqualification_basis  (GSR | INVOICE_QTY)\nqualification, gsr_b, rebate_rate,\nclear_cut, shortfall_amount, shortfall_pct,\nnext_slab_rate\nlast_updated_at  (decides latest)\nsource_file, raw  (original row, JSONB)", "store", align="left", size=8.6)
box(ax, 45, 24, 26, 22, "ingested_files\n\nid  PK\nkind  (scheme)\ngcs_path, generation\nsource_updated_at\nyear, month, row_count\nloaded_at\nstatus, rows_bad", "store", align="left", size=8.6)
box(ax, 76, 19, 23, 27, "Why upsert?\n\nDaily files hold only the\nrows SAP changed that day.\nEach row is stored once\nand replaced only by a\nnewer \"Last Updated\"\nversion, so files can be\nloaded in any order.", "note", align="left", size=8.4)
save(fig, "fig6_data_model")
