"""Build config/rules_seed.json from Equip_DimensionRules.xlsx (every sheet) and the approach note.

    python scripts/build_rules.py <Equip_DimensionRules.xlsx> [CSS_Design_Rules_AEML.docx] [config/rules_seed.json]

The Rules page loads this file; edits made in the page are kept separately in var/rules.json, so re-running this
never overwrites them. Every rule records where it came from (sheet and row)."""
import json
import re
import sys

import openpyxl

XLS = "Equip_DimensionRules.xlsx"
NOTE = "Automated Substation Design Approach Note"

SECTIONS = [
    ("plot", "Plot clearances: numeric values the checks use", "From the xlsx 'Plot Clerances' sheet. These numbers are enforced by the plot checks; the priority-2 rules above give the regulation behind them."),
    ("clearance", "Equipment operating clearances", "Front / back / left / right working clearance per equipment (mm)."),
    ("dims", "Equipment dimensions & weights", "Footprint, height and weights used for planning (mm, kg)."),
    ("panel", "Switchgear planning dimensions", "Board dimensions by make and the size to use in planning."),
    ("civil", "Civil & structural", "Heights, platforms, doors and ventilation openings."),
    ("cable", "Cable routing", "Trench, pipe and tray rules."),
    ("workflow", "Design workflow (process sheets)", "Activity sequence for CSS / DSS design and what the tool automates."),
    ("checks", "Plot due diligence & placement checks (SIPOC)", "What the design tool checks, marks and reports."),
    ("process", "Approach note: design pipeline", "Retrieval, generation, validation and CAD steps from the approach note."),
    ("kb", "Approach note: knowledge-base scope", "What is extracted from historic designs."),
]


def num(v):
    if isinstance(v, (int, float)):
        return float(v) if v != int(v) else int(v)
    return None


def txt(v):
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40]


def read_design_rules(path, add, sections):
    """CSS_Design_Rules_AEML.docx: priority table, draughtsman sequence, cross-check findings, reference documents."""
    import docx
    d = docx.Document(path)
    DOC = "CSS_Design_Rules_AEML.docx"
    rules_t = d.tables[0]
    sec = None
    for row in rules_t.rows[1:]:
        cells = [c.text.strip() for c in row.cells]
        if len(set(cells)) == 1 and cells[0].upper().startswith("PRIORITY"):
            m = re.match(r"PRIORITY\s+(\d+)\s*[—-]\s*(.*)", cells[0], re.I)
            sec = f"p{m.group(1)}"
            sections.append({"id": sec, "title": f"Priority {m.group(1)}: {m.group(2).split('(')[0].strip().rstrip('?').strip()}",
                             "about": m.group(2)})
            continue
        no, rule, value, applies, ref, status = (cells + [""] * 6)[:6]
        if not no or not sec:
            continue
        first, _, rest = rule.partition(". ")
        if len(first) > 150:
            first, rest = first[:first.rfind(" ", 0, 150)] + " …", rule
        add(sec, f"dr.{no}", first.rstrip("."), rest.strip(), value, None,
            {"All": "CSS+DSS"}.get(applies, applies) or "CSS+DSS", f"{DOC} › rule {no}", group=None, ref=ref, status=status, rule_no=no)
    # sequence table
    for row in d.tables[2].rows[1:]:
        c = [x.text.strip() for x in row.cells]
        add("sequence", f"seq.{c[0]}", f"Step {c[0]}: {c[1]}", f"{c[2]} | Rules applied: {c[3]} | {c[4]}", None, None, "CSS+DSS", f"{DOC} › section 4, step {c[0]}", group="Draughtsman sequence")
    # findings (section 5 paragraphs)
    on = False
    for p in d.paragraphs:
        t = p.text.strip()
        if t.startswith("5. Findings"):
            on = True
            continue
        if t.startswith("6. Original"):
            break
        if on and ":" in t:
            head, body = t.split(":", 1)
            add("findings", f"find.{slug(head)}", head.strip(), body.strip(), None, None, "CSS+DSS", f"{DOC} › section 5", group="Cross-check of the sources")
    # reference documents
    for i, row in enumerate(d.tables[3].rows[1:], 1):
        c = [x.text.strip() for x in row.cells]
        add("references", f"ref.{i}", c[0], f"Used for: {c[1]}", None, None, "CSS+DSS", f"{DOC} › section 6", group="Original reference documents")


def main(xls, docx_path=None, out="config/rules_seed.json"):
    wb = openpyxl.load_workbook(xls, data_only=True)
    rules = []

    def add(section, rid, title, text="", value=None, unit=None, applies="CSS+DSS", source="", values=None, group=None, enforce=None, match=None,
            ref="", status="", rule_no=None):
        rules.append({"id": rid, "section": section, "group": group, "title": title, "text": text, "value": value, "unit": unit,
                      "values": values, "applies_to": applies, "source": source, "reference": ref, "ref_status": status, "rule_no": rule_no,
                      "enforce": enforce, "match": match, "origin": "seed"})

    # ---- plot compliance
    ws = wb["Plot Clerances"]
    enforce = {"2.1": "compound_wall", "2.2": "dp_road", "2.3": "internal_road", "2.4": "nalla", "2.6": "structure", "4": "wayleave_width"}
    for r in range(5, ws.max_row + 1):
        no, part = ws.cell(r, 2).value, txt(ws.cell(r, 3).value)
        c, d = ws.cell(r, 4).value, ws.cell(r, 5).value
        if not part or (c is None and d is None):
            continue
        note = " ".join(txt(ws.cell(r, k).value) for k in (6, 7) if ws.cell(r, k).value)
        for who, v in (("CSS", c), ("DSS", d)):
            s = txt(v)
            if not s:
                continue
            minimum = s.lower().startswith("min")
            add("plot", f"plot.{who.lower()}.{slug(part)}", part, (note + (" " if note else "") + (s if num(v) is None else "")).strip(),
                value=num(v) if num(v) is not None else (int(m.group()) if (m := re.search(r"\d+", s)) and "%" not in s else s),
                unit="mm" if re.search(r"\d", s) and "%" not in s else ("% of RG" if "%" in s else None), applies=who,
                source=f"{XLS} › Plot Clerances › row {no}" + (" (minimum)" if minimum else ""),
                enforce=f"rules.plot_clearance_mm.{enforce[str(no)]}" if who == "CSS" and str(no) in enforce else None)
    add("plot", "plot.css.tree_radius", "Tree clearance radius", "Highlight 1.5 m radius around a tree; 4.5 m for doors and other entry/exit. In the first cut trees are not considered; they are considered in the revised layout.",
        1500, "mm", "CSS+DSS", f"{XLS} › Plot Clerances › row 3", enforce="rules.plot_clearance_mm.tree")
    add("plot", "plot.css.tree_at_door", "Tree clearance at doors / entry-exit", "", 4500, "mm", "CSS+DSS", f"{XLS} › Plot Clerances › row 3",
        enforce="rules.plot_clearance_mm.tree_at_door")

    # ---- equipment sheets
    other_rows = {}
    for sheet, who in (("CSS_Equipments", "CSS"), ("DSS Equipments", "DSS")):
        ws = wb[sheet]
        part = typ = None
        in_other = False
        for r in range(4, ws.max_row + 1):
            b, c, d = ws.cell(r, 2).value, ws.cell(r, 3).value, ws.cell(r, 4).value
            if txt(b) == "Other items":
                in_other = True
                continue
            if b:
                part = txt(b)
            if c:
                typ = txt(c)
            if not d and not in_other:
                continue
            if in_other:
                if d or c:
                    other_rows.setdefault((typ or "", txt(d)), []).append((who, sheet, r, [ws.cell(r, k).value for k in range(5, 8)]))
                continue
            name = txt(d)
            if not name:
                continue
            L, B, H, W, DW = (num(ws.cell(r, k).value) for k in (5, 6, 7, 8, 9))
            cl = {k: ws.cell(r, i).value for k, i in (("front", 10), ("back", 11), ("left", 12), ("right", 13))}
            src = f"{XLS} › {sheet} › row {r}"
            grp = f"{part} / {typ}" if typ and typ != part else part
            rid = f"{slug(who)}.{slug(sheet)}.{r}"
            remark = txt(ws.cell(r, 14).value)
            if any(v is not None and txt(v) not in ("", "-") for v in cl.values()):
                vals = {k: (num(v) if num(v) is not None else txt(v)) for k, v in cl.items() if v is not None and txt(v) not in ("", "-")}
                add("clearance", f"clr.{rid}", name, remark if remark != "-" else "", None, "mm", who, src, values=vals, group=grp,
                    match={"part": part, "type": typ, "name": name})
            if any(x is not None for x in (L, B, H, W, DW)):
                add("dims", f"dim.{rid}", name, remark if remark != "-" else "", None, "mm / kg", who, src,
                    values={k: v for k, v in (("L", L), ("B", B), ("H", H), ("weight_kg", W), ("dynamic_weight_kg", DW)) if v is not None},
                    group=grp, match={"part": part, "type": typ, "name": name})

    # ---- switchgear planning dims (33-11 kV panel sheet)
    ws = wb["33-11 kV panel"]
    for r in range(4, ws.max_row + 1):
        cfg = txt(ws.cell(r, 2).value)
        plan = txt(ws.cell(r, 8).value)
        if not cfg or cfg.lower() in ("dimensions",):
            continue
        makes = {n: txt(ws.cell(r, i).value) for n, i in (("Schneider", 3), ("ABB", 4), ("Siemens", 5), ("CG", 6), ("Megawin", 7)) if ws.cell(r, i).value}
        rem = txt(ws.cell(r, 9).value)
        add("panel", f"panel.row{r}", cfg, ((("Use: " + plan) if plan else "") + (" " + rem if rem else "")).strip() or "", None, "mm", "CSS+DSS",
            f"{XLS} › 33-11 kV panel › row {r}", values=makes or None, group="11 kV / 33 kV boards")

    # ---- other items -> civil / cable
    cable_types = {"Trench", "Tray"}
    for (typ, name), rows in other_rows.items():
        who = "+".join(sorted({w for w, *_ in rows}))
        _, sheet, r, (L, B, H) = rows[0]
        src = " / ".join(f"{XLS} › {s} › row {rr}" for _, s, rr, _ in rows)
        parts = [f"{k} {txt(v)}" for k, v in (("length", L), ("breadth/width", B), ("height/depth", H)) if v is not None and txt(v)]
        sec = "cable" if typ in cable_types else "civil"
        add(sec, f"other.{slug(typ)}.{slug(name or typ)}", f"{typ}: {name}" if name else typ, "; ".join(parts), None, "mm", who, src,
            values={k: (num(v) if num(v) is not None else txt(v)) for k, v in (("length", L), ("breadth", B), ("height", H)) if v is not None},
            group=typ)
    add("civil", "civil.css_min_height", "CSS minimum clear height", "", 4200, "mm", "CSS", "config/settings.yaml › rules.css_min_height_mm", enforce="rules.css_min_height_mm")
    add("civil", "civil.tray_below_beam", "Cable tray below beam bottom", "", 450, "mm", "CSS+DSS", f"{XLS} › CSS_Equipments › Tray", enforce="rules.tray_below_beam_mm")
    add("civil", "civil.transformer_door", "Transformer door width", "", 2750, "mm", "CSS+DSS", f"{XLS} › CSS_Equipments › Doors", enforce="rules.transformer_door_mm")

    # ---- process sheets
    for sheet, who in (("Process", "CSS"), ("Process_DSS", "DSS")):
        ws = wb[sheet]
        for r in range(2, ws.max_row + 1):
            no, act, resp, auto = (txt(ws.cell(r, k).value) for k in (1, 2, 3, 4))
            if not act:
                continue
            add("workflow", f"wf.{who.lower()}.{r}", f"{no}. {act.split(chr(10))[0][:110]}", f"{act} | Responsibility: {resp}" + (f" | Automation: {auto}" if auto and auto != "-" else ""),
                None, None, who, f"{XLS} › {sheet} › row {r}", group=f"{who} process")
    # ---- SIPOC: collect each task's process-step descriptions
    ws = wb["SIPOC"]
    tasks = {2: "Task 1: due diligence of the plot", 9: "Task 2: equipment placement", 16: "Task 3: detailed layout"}
    for c0, label in tasks.items():
        for r in range(5, ws.max_row + 1):
            desc = txt(ws.cell(r, c0 + 3).value)
            step = ws.cell(r, c0 + 2).value
            out_ = txt(ws.cell(r, c0 + 4).value)
            if desc:
                add("checks", f"sipoc.{c0}.{r}", f"{label} · step {step}", desc + (f" → Output: {out_}" if out_ else ""), None, None, "CSS+DSS",
                    f"{XLS} › SIPOC › row {r}", group=label)
    for r in range(5, ws.max_row + 1):
        inp = txt(ws.cell(r, 3).value)
        if inp:
            add("checks", f"sipoc.input.{r}", f"Input: {inp}", "", None, None, "CSS+DSS", f"{XLS} › SIPOC › row {r}", group="Inputs and objects the tool uses")
    ws = wb["Sheet2"]
    add("workflow", "wf.sheet2", "Placement decision flow", " → ".join(txt(c.value) for row in ws.iter_rows(min_row=3, max_row=ws.max_row) for c in row[:1] if c.value),
        None, None, "CSS+DSS", f"{XLS} › Sheet2", group="Decision flow")

    # ---- approach note
    steps = [
        ("Retrieve before generating", "Retrieval runs first. Generation, validation and optimisation run only when no retrieved design clears the match threshold."),
        ("Return at least 3 matches", "The system returns at least 3 matches with a similarity score; above the threshold they go to the developer for use and adaptation."),
        ("Filter by known attributes", "Known attributes narrow the search first, e.g. an open-sky substation is matched only against open-sky sites."),
        ("Encode the site", "The proposed plot is converted into a data structure so shape and size similarity can be computed against historic plots."),
        ("Generate block by block", "When nothing clears the threshold, the layout is generated one block at a time; each block's output is summarised and passed to the next."),
        ("Validate against compliance", "Once all blocks are generated, the intermediate output is checked against the compliance requirements."),
        ("Optimise with clearance constraints", "If any check fails, optimisation routines with clearance constraints adjust the layout to reach the best compliant result."),
        ("Deliver 3 compliant options", "The process repeats until 3 compliant layout options are obtained."),
        ("Convert to 2D CAD", "Intermediate layouts are produced in a CAD-compatible structure and converted to 2D CAD files using Python CAD packages."),
    ]
    for i, (t, d) in enumerate(steps, 1):
        add("process", f"note.step{i}", t, d, None, None, "CSS+DSS", f"{NOTE} › Approach overview / Steps 2–5", group="Pipeline")
    kb = [("Electrical configuration and equipment", "Transformer ratings, busbar arrangement, number of feeders, switchgear type, fault levels; equipment dimensions, weight, connection orientation, operating clearances."),
          ("Site constraints", "Available area, plot shape, building footprint, floor level, indoor/outdoor (open-sky) installation, centralised or distributed installation, standalone building or inside an existing building, whether on a platform."),
          ("Civil and structural", "Plinths, floor loading, cable trenches, equipment supports, substation height, beams/columns."),
          ("Cable routing and connectivity", "Cable entry/exit points, trench paths, cable capacity, equipment interconnections."),
          ("Access and ventilation", "Access paths, ventilation arrangement.")]
    for i, (t, d) in enumerate(kb, 1):
        add("kb", f"note.kb{i}", t, d, None, None, "CSS+DSS", f"{NOTE} › Step 1 table", group="Extracted from every historic design")
    add("process", "note.goal", "Objectives", "Speed up the design process; standardise substation layouts. Most substations are built from a small set of electrical configurations, so find a similar past design first and adapt it.",
        None, None, "CSS+DSS", f"{NOTE} › Problem and objectives", group="Goals")

    sections = []
    if docx_path:
        read_design_rules(docx_path, add, sections)
    sections += [{"id": i, "title": t, "about": a} for i, t, a in SECTIONS]
    extra = {"sequence": ("Draughtsman sequence", "The 12 steps from the developer's plan to issued drawings, with the rules applied at each."),
             "findings": ("Cross-check findings", "Where the AEML sheet, NBC and other sources agree or leave gaps."),
             "references": ("Original reference documents", "Statutes, codes and AEML documents the rules come from.")}
    for k, (t, a) in extra.items():
        sections.append({"id": k, "title": t, "about": a})
    doc = {"sections": sections, "rules": rules}
    json.dump(doc, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    from collections import Counter
    print(len(rules), "rules", dict(Counter(r["section"] for r in rules)))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], *(a[1:2] or []), *(a[2:3] or []))
