"""
Design system for the Pulse Foundry dashboard.

Light theme built from the logo: a cyan → violet → magenta → orange bolt on
white. The gradient is used in exactly two places (the header pulse line and
the active tab), and the calendar tints shift slots along the same gradient
by time of day. Everything else is warm white, soft grey and ink.
"""

from __future__ import annotations

import base64
import html as _html
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parent / "assets"
LOGO_PATH = ASSETS / "pulse_logo.png"

STATE_LABEL = {"action": "Action needed", "review": "Needs a look", "ok": "Up to date", "heads_up": "Heads-up"}


def esc(value) -> str:
    return _html.escape("" if value is None else str(value))


def logo_data_uri() -> str:
    try:
        return "data:image/png;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode()
    except OSError:
        return ""


def html(markup: str) -> None:
    """Render raw HTML. Lines are flattened so Markdown never mistakes
    indented HTML for a code block."""
    flat = " ".join(line.strip() for line in markup.splitlines() if line.strip())
    st.markdown(flat, unsafe_allow_html=True)


def segmented(label: str, options: list, key: str, default=None, format_func=str):
    """Segmented control with a radio fallback for older Streamlit."""
    default = default if default is not None else options[0]
    seg = getattr(st, "segmented_control", None)
    if seg is not None:
        value = seg(label, options, default=default, key=key, format_func=format_func,
                    label_visibility="collapsed", selection_mode="single")
        return value if value is not None else default
    return st.radio(label, options, index=options.index(default), key=key, horizontal=True,
                    format_func=format_func, label_visibility="collapsed")


# ---------------------------------------------------------------------------
# Small HTML components
# ---------------------------------------------------------------------------

def pill(text: str, tone: str = "neutral") -> str:
    return f'<span class="pf-pill pf-{tone}">{esc(text)}</span>'


def status_pill(status: str) -> str:
    return pill(STATE_LABEL.get(status, status), {"action": "red", "review": "amber", "ok": "green",
                                                    "heads_up": "neutral"}.get(status, "neutral"))


def team_chip(team: str) -> str:
    return f'<span class="pf-team">{esc(team)}</span>'


def check_icon(state: str) -> str:
    return {
        "pass": '<span class="pf-ck pf-ck-pass">✓</span>',
        "warn": '<span class="pf-ck pf-ck-warn">!</span>',
        "fail": '<span class="pf-ck pf-ck-fail">✕</span>',
        "na": '<span class="pf-ck pf-ck-na">–</span>',
    }.get(state, "")


def section(title: str, sub: str = "") -> None:
    html(f'<div class="pf-section"><h3>{esc(title)}</h3>{f"<p>{esc(sub)}</p>" if sub else ""}</div>')


def empty(text: str) -> None:
    html(f'<div class="pf-empty">{esc(text)}</div>')


def note(text: str, tone: str = "neutral") -> None:
    html(f'<div class="pf-note pf-note-{tone}">{text}</div>')


def table(headers: list[str], rows: list[list[str]], numeric: set[int] | None = None,
          row_classes: list[str] | None = None, compact: bool = False) -> str:
    """Rows contain pre-escaped HTML strings."""
    numeric = numeric or set()
    head = "".join(f'<th class="{"num" if i in numeric else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    body = []
    for r_i, row in enumerate(rows):
        cls = row_classes[r_i] if row_classes else ""
        cells = "".join(f'<td class="{"num" if i in numeric else ""}">{c}</td>' for i, c in enumerate(row))
        body.append(f'<tr class="{cls}">{cells}</tr>')
    return (f'<div class="pf-table-wrap"><table class="pf-table{" compact" if compact else ""}">'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700;800&display=swap');

:root {
  --pf-paper: #FFFFFF;
  --pf-surface: #F8F7F5;
  --pf-surface-2: #F2F0EC;
  --pf-line: #ECE9E4;
  --pf-line-2: #E0DCD5;
  --pf-ink: #1D1B26;
  --pf-ink-2: #5B5768;
  --pf-ink-3: #8C8898;
  --pf-cyan: #22B8DD;
  --pf-violet: #7B5CF0;
  --pf-magenta: #D946EF;
  --pf-orange: #F2762E;
  --pf-brand: linear-gradient(90deg, #22B8DD 0%, #7B5CF0 38%, #D946EF 66%, #F2762E 100%);
  --pf-red: #C93636;   --pf-red-bg: #FDECEC;   --pf-red-line: #F3C4C4;
  --pf-amber: #A86A06; --pf-amber-bg: #FDF3DE; --pf-amber-line: #F1D9A6;
  --pf-green: #18845A; --pf-green-bg: #E6F5EE; --pf-green-line: #BFE3D1;
  --pf-day: #E2F5FB;     --pf-day-ink: #0B6E8A;
  --pf-evening: #EFE9FE; --pf-evening-ink: #5B3BC9;
  --pf-night: #E9EBF5;   --pf-night-ink: #36437A;
  --pf-long: #FFEFE2;    --pf-long-ink: #B4511A;
  --pf-radius: 12px;
}

html, body, .stApp, [class*="css"], button, input, textarea, select {
  font-family: 'Figtree', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif !important;
}
.stApp { background: var(--pf-paper); color: var(--pf-ink); }
.block-container, [data-testid="stMainBlockContainer"] {
  padding-top: 1.6rem !important; padding-bottom: 4rem !important; max-width: 1280px;
}
header[data-testid="stHeader"] { background: transparent; }
h1, h2, h3, h4 { color: var(--pf-ink) !important; letter-spacing: -0.015em; }
p, li, label, span { color: inherit; }
code { color: var(--pf-violet); background: var(--pf-surface); }

/* Sidebar */
section[data-testid="stSidebar"] { background: var(--pf-surface); border-right: 1px solid var(--pf-line); }
section[data-testid="stSidebar"] .block-container { padding-top: 1.2rem !important; }
.pf-brand { display: flex; align-items: center; gap: 10px; margin: 0 0 18px; }
.pf-brand img { width: 38px; height: 38px; }
.pf-brand .name { font-size: 1.15rem; font-weight: 800; letter-spacing: -0.02em; color: var(--pf-ink); line-height: 1.1; }
.pf-brand .org { font-size: 0.8rem; color: var(--pf-ink-3); }
.pf-side-title { font-size: 0.82rem; font-weight: 700; color: var(--pf-ink-2); margin: 14px 0 8px; }
.pf-sources { display: grid; gap: 6px; margin-bottom: 10px; }
.pf-source { display: flex; align-items: center; gap: 10px; background: var(--pf-paper);
  border: 1px solid var(--pf-line); border-radius: 10px; padding: 8px 10px; }
.pf-source .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--pf-line-2); flex: none; }
.pf-source.on .dot { background: var(--pf-green); }
.pf-source .lbl { font-weight: 600; font-size: 0.88rem; color: var(--pf-ink); }
.pf-source .meta { margin-left: auto; font-size: 0.78rem; color: var(--pf-ink-3); text-align: right;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 120px; }
.pf-side-foot { font-size: 0.78rem; color: var(--pf-ink-3); margin-top: 18px; line-height: 1.45; }

/* Header */
.pf-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; }
.pf-header h1 { font-size: 1.85rem !important; font-weight: 800 !important; margin: 0 !important; padding: 0 !important; }
.pf-header p { margin: 4px 0 0; color: var(--pf-ink-2); font-size: 0.98rem; }
.pf-header .asof { font-size: 0.85rem; color: var(--pf-ink-3); white-space: nowrap; padding-bottom: 4px; }
.pf-pulse { height: 3px; border-radius: 3px; background: var(--pf-brand); margin: 14px 0 18px; opacity: .9; }

/* Status strip */
.pf-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 14px 0 6px; }
.pf-stat { border: 1px solid var(--pf-line); border-radius: var(--pf-radius); padding: 12px 14px; background: var(--pf-paper); }
.pf-stat .n { font-size: 1.6rem; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }
.pf-stat .l { font-size: 0.86rem; color: var(--pf-ink-2); margin-top: 6px; }
.pf-stat.red { border-color: var(--pf-red-line); background: var(--pf-red-bg); } .pf-stat.red .n { color: var(--pf-red); }
.pf-stat.amber { border-color: var(--pf-amber-line); background: var(--pf-amber-bg); } .pf-stat.amber .n { color: var(--pf-amber); }
.pf-stat.green .n { color: var(--pf-green); }
@media (max-width: 760px) { .pf-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

/* Tabs */
.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--pf-line); }
.stTabs [data-baseweb="tab"] { padding: 10px 14px; color: var(--pf-ink-3); font-weight: 600; background: transparent; }
.stTabs [data-baseweb="tab"] p { font-size: 0.95rem; font-weight: 600; }
.stTabs [aria-selected="true"] { color: var(--pf-ink) !important; }
.stTabs [data-baseweb="tab-highlight"] { background: var(--pf-brand) !important; height: 3px; border-radius: 3px; }
.stTabs [data-baseweb="tab-border"] { display: none; }

/* Buttons */
.stButton > button, .stDownloadButton > button {
  border-radius: 10px; border: 1px solid var(--pf-line-2); background: var(--pf-paper);
  color: var(--pf-ink); font-weight: 600; box-shadow: none; transition: border-color .15s, background .15s;
}
.stButton > button:hover, .stDownloadButton > button:hover { border-color: var(--pf-violet); color: var(--pf-violet); background: #FBFAFF; }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
  background: var(--pf-violet); border-color: var(--pf-violet); color: #fff;
}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover { background: #6847E6; color: #fff; }
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible { outline: 2px solid var(--pf-violet); outline-offset: 2px; }

/* Inputs */
[data-baseweb="select"] > div, [data-baseweb="input"] > div { border-radius: 10px !important; border-color: var(--pf-line-2) !important; background: var(--pf-paper) !important; }
[data-testid="stFileUploaderDropzone"] { background: var(--pf-paper); border: 1px dashed var(--pf-line-2); border-radius: 12px; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color: var(--pf-line) !important; border-radius: var(--pf-radius) !important; }
[data-testid="stExpander"] details { border-color: var(--pf-line) !important; border-radius: var(--pf-radius) !important; }
[data-testid="stExpander"] summary p { font-weight: 600; }

/* Search */
.pf-search-label { font-weight: 700; font-size: 0.92rem; margin: 2px 0 6px; color: var(--pf-ink); }

/* Section titles */
.pf-section { margin: 22px 0 10px; }
.pf-section h3 { font-size: 1.08rem !important; font-weight: 700 !important; margin: 0 !important; padding: 0 !important; }
.pf-section p { color: var(--pf-ink-2); font-size: 0.9rem; margin: 3px 0 0; }

/* Pills, chips */
.pf-pill { display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem; font-weight: 700;
  padding: 3px 9px; border-radius: 999px; border: 1px solid transparent; white-space: nowrap; }
.pf-pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.pf-red { color: var(--pf-red); background: var(--pf-red-bg); border-color: var(--pf-red-line); }
.pf-amber { color: var(--pf-amber); background: var(--pf-amber-bg); border-color: var(--pf-amber-line); }
.pf-green { color: var(--pf-green); background: var(--pf-green-bg); border-color: var(--pf-green-line); }
.pf-neutral { color: var(--pf-ink-2); background: var(--pf-surface); border-color: var(--pf-line); }
.pf-team { display: inline-block; font-size: 0.74rem; font-weight: 700; color: var(--pf-violet);
  background: #F3EFFE; border-radius: 6px; padding: 2px 7px; margin-right: 4px; }
.pf-tag { display: inline-block; font-size: 0.74rem; font-weight: 600; color: var(--pf-ink-2);
  background: var(--pf-surface-2); border-radius: 6px; padding: 1px 7px; margin-left: 4px; white-space: nowrap; }
.pf-tag.red { color: var(--pf-red); background: var(--pf-red-bg); }
.pf-tag.amber { color: var(--pf-amber); background: var(--pf-amber-bg); }

/* Notes and empty states */
.pf-note { border-radius: 10px; padding: 10px 14px; font-size: 0.9rem; line-height: 1.5; margin: 6px 0 12px;
  background: var(--pf-surface); color: var(--pf-ink-2); border: 1px solid var(--pf-line); }
.pf-note-amber { background: var(--pf-amber-bg); border-color: var(--pf-amber-line); color: #6E4A0A; }
.pf-note-red { background: var(--pf-red-bg); border-color: var(--pf-red-line); color: #7E2020; }
.pf-note-green { background: var(--pf-green-bg); border-color: var(--pf-green-line); color: #145E41; }
.pf-empty { border: 1px dashed var(--pf-line-2); border-radius: var(--pf-radius); padding: 22px; text-align: center;
  color: var(--pf-ink-3); font-size: 0.92rem; background: var(--pf-paper); }

/* Issue cards */
.pf-issue { position: relative; padding: 2px 0 2px 14px; }
.pf-issue::before { content: ""; position: absolute; left: 0; top: 2px; bottom: 2px; width: 4px; border-radius: 4px; background: var(--pf-line-2); }
.pf-issue.action::before { background: var(--pf-red); }
.pf-issue.review::before { background: #E3A51C; }
.pf-issue.heads_up::before { background: var(--pf-ink-3); }
.pf-issue .t { font-weight: 700; font-size: 0.98rem; color: var(--pf-ink); }
.pf-issue .who { font-size: 0.85rem; color: var(--pf-ink-2); margin-top: 2px; }
.pf-issue .who b { color: var(--pf-ink); font-weight: 600; }
.pf-issue .d { font-size: 0.9rem; color: var(--pf-ink-2); margin-top: 6px; }
.pf-issue .ev { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; }
.pf-issue .ev span { font-size: 0.8rem; background: var(--pf-surface); border: 1px solid var(--pf-line); border-radius: 6px; padding: 2px 8px; color: var(--pf-ink-2); }
.pf-issue .ev span b { color: var(--pf-ink); font-weight: 600; }
.pf-issue .nx { margin-top: 8px; font-size: 0.88rem; color: var(--pf-ink); }
.pf-issue .nx em { font-style: normal; font-weight: 700; color: var(--pf-violet); margin-right: 4px; }
.pf-level-head { display: flex; align-items: baseline; gap: 8px; margin: 18px 0 8px; }
.pf-level-head h4 { font-size: 0.98rem !important; font-weight: 700 !important; margin: 0 !important; padding: 0 !important; }
.pf-level-head span { color: var(--pf-ink-3); font-size: 0.86rem; }
.pf-scope { color: var(--pf-ink-2); font-size: 0.9rem; margin: 2px 0 10px; }
.pf-follow { display: flex; align-items: center; gap: 10px; background: #F7F4FF; border: 1px solid #E4DCFC;
  border-radius: 10px; padding: 10px 14px; font-size: 0.9rem; color: #3E2A8C; margin: 6px 0 4px; }

/* Tables */
.pf-table-wrap { overflow-x: auto; border: 1px solid var(--pf-line); border-radius: var(--pf-radius); background: var(--pf-paper); margin: 4px 0 8px; }
.pf-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.pf-table th { text-align: left; font-weight: 700; color: var(--pf-ink-2); font-size: 0.82rem; background: var(--pf-surface);
  padding: 9px 12px; border-bottom: 1px solid var(--pf-line); white-space: nowrap; }
.pf-table td { padding: 9px 12px; border-bottom: 1px solid var(--pf-line); color: var(--pf-ink); vertical-align: top; }
.pf-table tr:last-child td { border-bottom: none; }
.pf-table .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.pf-table tr.total td { font-weight: 700; background: var(--pf-surface); }
.pf-table tr.hl td { background: #FFFBF1; }
.pf-table.compact td, .pf-table.compact th { padding: 7px 10px; }
.pf-table .sub { display: block; font-size: 0.78rem; color: var(--pf-ink-3); margin-top: 1px; }
.pf-table .muted { color: var(--pf-ink-3); }
.pf-table .mono { font-variant-numeric: tabular-nums; }
.pf-yes { color: var(--pf-green); font-weight: 700; }
.pf-no { color: var(--pf-ink-3); }

/* Calendar */
.pf-legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 0.82rem; color: var(--pf-ink-2); margin: 4px 0 10px; }
.pf-legend span { display: inline-flex; align-items: center; gap: 6px; }
.pf-legend i { width: 12px; height: 12px; border-radius: 4px; display: inline-block; }
.pf-cal-wrap { overflow-x: auto; border: 1px solid var(--pf-line); border-radius: 14px; background: var(--pf-paper); }
.pf-cal { display: grid; grid-template-columns: 118px repeat(7, minmax(128px, 1fr)); min-width: 1020px; }
.pf-cal > div { border-right: 1px solid var(--pf-line); border-bottom: 1px solid var(--pf-line); }
.pf-cal > div:nth-child(8n) { border-right: none; }
.pf-cal-h { padding: 10px 10px 8px; background: var(--pf-surface); }
.pf-cal-h .dow { font-weight: 700; font-size: 0.9rem; }
.pf-cal-h .dt { font-size: 0.8rem; color: var(--pf-ink-3); margin-left: 4px; }
.pf-cal-h.today { background: #F4F0FF; }
.pf-cal-h.today .dt { color: var(--pf-violet); font-weight: 700; }
.pf-cal-h .cnt { display: block; font-size: 0.75rem; color: var(--pf-ink-3); margin-top: 2px; }
.pf-cal-h .cnt.alert { color: var(--pf-red); font-weight: 700; }
.pf-slot { padding: 10px; display: flex; flex-direction: column; gap: 2px; }
.pf-slot b { font-size: 0.86rem; }
.pf-slot span { font-size: 0.78rem; opacity: .85; }
.pf-cell { padding: 6px; display: flex; flex-direction: column; gap: 4px; min-height: 54px; }
.pf-cell.alert { background: #FFF7F7; }
.pf-cell .none { color: var(--pf-line-2); font-size: 0.85rem; padding: 4px; }
.tone-day { background: var(--pf-day); color: var(--pf-day-ink); }
.tone-evening { background: var(--pf-evening); color: var(--pf-evening-ink); }
.tone-night { background: var(--pf-night); color: var(--pf-night-ink); }
.tone-long { background: var(--pf-long); color: var(--pf-long-ink); }
.pf-chip { display: flex; align-items: center; gap: 6px; border-radius: 8px; padding: 4px 7px; font-size: 0.82rem;
  line-height: 1.2; border: 1px solid transparent; }
.pf-chip .rc { font-size: 0.68rem; font-weight: 800; letter-spacing: .02em; opacity: .9; min-width: 26px; }
.pf-chip .nm { color: var(--pf-ink); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pf-chip.warn { box-shadow: inset 0 0 0 1px var(--pf-amber-line); }
.pf-chip.warn .nm::after { content: " ●"; color: #E3A51C; font-size: 0.7rem; }
.pf-chip.alert { background: var(--pf-red-bg); color: var(--pf-red); border-color: var(--pf-red-line); flex-wrap: wrap; }
.pf-chip.alert .nm { color: var(--pf-red); text-decoration: line-through; text-decoration-thickness: 1px; }
.pf-chip.alert .flag { flex-basis: 100%; font-size: 0.72rem; font-weight: 700; color: var(--pf-red); }
.pf-chip.dim { opacity: .35; }
.pf-chip.compact { padding: 3px 6px; }

/* By-person grid */
.pf-pgrid { display: grid; grid-template-columns: 210px repeat(7, minmax(92px, 1fr)) 84px; min-width: 1040px; }
.pf-pgrid > div { border-bottom: 1px solid var(--pf-line); padding: 8px; display: flex; align-items: center; }
.pf-pgrid .h { background: var(--pf-surface); font-weight: 700; font-size: 0.84rem; flex-direction: column; align-items: flex-start; }
.pf-pgrid .h span { font-weight: 500; color: var(--pf-ink-3); font-size: 0.76rem; }
.pf-pgrid .h.today { background: #F4F0FF; }
.pf-pgrid .who { flex-direction: column; align-items: flex-start; }
.pf-pgrid .who b { font-size: 0.9rem; }
.pf-pgrid .who span { font-size: 0.78rem; color: var(--pf-ink-3); }
.pf-pgrid .hrs { justify-content: flex-end; font-variant-numeric: tabular-nums; font-weight: 700; font-size: 0.88rem; gap: 6px; }
.pf-pgrid .off { color: var(--pf-line-2); font-size: 0.82rem; }
.pf-shift { border-radius: 7px; padding: 4px 8px; font-size: 0.8rem; font-weight: 700; width: 100%; text-align: center; }
.pf-shift.alert { background: var(--pf-red-bg); color: var(--pf-red); box-shadow: inset 0 0 0 1px var(--pf-red-line); }
.pf-pgrid .row-alert { background: #FFF9F9; }

/* Cover panel */
.pf-cover-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px; margin: 2px 0 4px; }
.pf-cover-head b { font-size: 1rem; }
.pf-cover-head span { color: var(--pf-ink-2); font-size: 0.88rem; }
.pf-cand { display: block; margin: 1px 0; }
.pf-cand b { font-weight: 600; }

/* Profile */
.pf-prof-head { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.pf-prof-head h2 { font-size: 1.4rem !important; font-weight: 800 !important; margin: 0 !important; padding: 0 !important; }
.pf-prof-head .meta { color: var(--pf-ink-2); font-size: 0.92rem; }
.pf-prof-reason { color: var(--pf-ink-2); font-size: 0.92rem; margin: 6px 0 4px; }
.pf-checks { display: grid; gap: 4px; }
.pf-check { display: flex; gap: 10px; align-items: flex-start; padding: 6px 0; border-bottom: 1px solid var(--pf-line); font-size: 0.9rem; }
.pf-check:last-child { border-bottom: none; }
.pf-check .lbl { font-weight: 600; }
.pf-check .det { color: var(--pf-ink-3); font-size: 0.82rem; display: block; }
.pf-ck { flex: none; width: 20px; height: 20px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
  font-size: 0.72rem; font-weight: 800; margin-top: 1px; }
.pf-ck-pass { background: var(--pf-green-bg); color: var(--pf-green); }
.pf-ck-warn { background: var(--pf-amber-bg); color: var(--pf-amber); }
.pf-ck-fail { background: var(--pf-red-bg); color: var(--pf-red); }
.pf-ck-na { background: var(--pf-surface-2); color: var(--pf-ink-3); }
.pf-link { font-size: 0.86rem; color: var(--pf-ink-2); padding: 6px 0; border-bottom: 1px solid var(--pf-line); }
.pf-link:last-child { border-bottom: none; }
.pf-link b { color: var(--pf-ink); }
.pf-steps { font-size: 0.84rem; color: var(--pf-ink-2); line-height: 1.6; }
.pf-steps div { padding: 1px 0; }
.pf-week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
.pf-week .d { border: 1px solid var(--pf-line); border-radius: 10px; padding: 7px; text-align: center; }
.pf-week .d .n { font-size: 0.76rem; color: var(--pf-ink-3); }
.pf-week .d .s { margin-top: 4px; }

/* Welcome */
.pf-welcome { border: 1px solid var(--pf-line); border-radius: 16px; padding: 28px; background: var(--pf-surface); }
.pf-welcome h2 { font-size: 1.35rem !important; font-weight: 800 !important; margin: 0 0 6px !important; padding: 0 !important; }
.pf-welcome p { color: var(--pf-ink-2); max-width: 62ch; margin: 0 0 16px; }
.pf-welcome-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.pf-welcome-grid div { background: var(--pf-paper); border: 1px solid var(--pf-line); border-radius: 12px; padding: 12px 14px; }
.pf-welcome-grid b { display: block; font-size: 0.92rem; }
.pf-welcome-grid span { font-size: 0.82rem; color: var(--pf-ink-3); }
@media (max-width: 760px) { .pf-welcome-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
"""


def inject_theme() -> None:
    html(CSS)
