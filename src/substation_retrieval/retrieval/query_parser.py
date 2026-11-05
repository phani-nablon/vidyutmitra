"""Natural-language (or JSON) requirement -> structured, *partial* query.

Only what the user actually states is filled in; the ranker scores just those fields.

Example
  "indoor CSS on ground floor of a residential building, 2 x 1500 kVA dry DT, 1 RMU 1+3,
   2 LTP 10 way, room 11 x 5 m, clear height 3.5 m, raised platform, trench to DP road, rolling shutter"
"""
import json
import re

WORDNUM = {"one": 1, "single": 1, "a": 1, "an": 1, "two": 2, "twin": 2, "double": 2, "three": 3, "four": 4,
           "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12}
NUM = r"(\d+|one|single|two|twin|three|four|five|six|seven|eight|nine|ten|twelve)"

EQUIP_PATTERNS = [
    ("packaged", r"package[d]?\s*(?:ss|sub\s*-?\s*station|css)?|unitised|unitized|\bpss\b|compact\s+s(?:ub)?s(?:tation)?|pad[\s-]*mounted"),
    ("metering", r"ct\s*/?\s*pt|\bctpt\b|metering\s+rmu|metering\s+panel|\bmetering\b(?!\s+(?:css|sub|only))"),
    ("ht_switchgear", r"\bgis\b|\bvcb\b|ht\s+switch\s*(?:gear|board)|ht\s+panels?|33\s*kv\s+(?:swgr|switchgear|panel)"),
    ("transformer", r"\b(?:dts?|dtr|transformers?|trafos?|tx|x'?mers?)\b"),
    ("rmu", r"\brmus?\b|ring\s+main\s+units?"),
    ("ltp", r"\bltps?\b|lt\s+panels?|feeder\s+pillars?|\bicogs?\b|hybrid\s+(?:lt\s*)?panels?|lt\s*pcc|ltpcc"),
    ("apfc", r"\bapfc\b|capacitor"),
    ("aux", r"\bacdb\b|\bdcdb\b|batter(?:y|ies)|\bscada\b|\bfrtu\b|\brtu\b"),
]


# Network Planning sends equipment as a numbered list: "1. 1.5 MVA DT – 3 nos; 2. 2+2 RMU – 1 nos".
# The quantity sits after a dash at the end of each item; the generic parser would read the list numbers
# ("2.", "3.") as counts and miss the MVA rating, so list-shaped text is split per item first.
_NP_QTY = re.compile(r"^\s*(?:\d{1,2}\s*[.)]\s*)?(.+?)\s*-\s*(\d+)\s*(?:nos?\.?|no\.?|numbers?|units?|sets?)?\s*[.,]?\s*$", re.I)


def parse_np_list(text):
    t = (text or "").replace("\u2013", "-").replace("\u2014", "-").lower()
    segs = re.split(r"[\n;]+|\s(?=\d{1,2}[.)]\s)", t)
    items = []
    for seg in segs:
        m = _NP_QTY.match(seg)
        if not m:
            continue
        spec = re.sub(r"(\d+(?:\.\d+)?)\s*mva\b", lambda x: f"{int(round(float(x.group(1)) * 1000))} kva", m.group(1))
        spec = re.sub(r"(\d)([a-z])", r"\1 \2", spec)          # "2+2RMU" -> "2+2 rmu": the patterns need a word boundary
        spec = re.sub(r"(\d)\s+(\+)\s*", r"\1\2", spec)
        found = parse_equipment(spec)
        if not found:
            continue
        it = dict(found[0], count=int(m.group(2)), text=m.group(1).strip())
        items.append(it)
    return items


def _n(s):
    s = s.lower()
    return int(s) if s.isdigit() else WORDNUM.get(s)


def _mm(v, unit):
    v = float(v)
    unit = (unit or "").lower()
    if unit == "mm":
        return v
    if unit in ("m", "mtr", "mtrs", "meter", "meters", "metre", "metres"):
        return v * 1000
    return v * 1000 if v < 100 else v


def parse_equipment(text):
    t = text.lower()
    hits = []
    for typ, pat in EQUIP_PATTERNS:
        for m in re.finditer(pat, t):
            if any(m.start() < e and m.end() > s for s, e, _ in hits):
                continue
            hits.append((m.start(), m.end(), typ))
    hits.sort()
    items = []
    for i, (s, e, typ) in enumerate(hits):
        prev_end = hits[i - 1][1] if i else 0
        nxt = hits[i + 1][0] if i + 1 < len(hits) else len(t)
        pre = t[prev_end:s]
        suf = t[e:nxt]
        # cut the prefix at clause boundaries
        pre = re.split(r"[;,]|\band\b|\bwith\b|\bplus\b", pre)[-1]
        suf_c = re.split(r"[;,]|\band\b|\bplus\b", suf)[0]
        kva = None
        mk = re.findall(r"(\d{3,4})\s*k\s*va", pre) or re.findall(r"^\W*\(?\s*(\d{3,4})\s*k\s*va", suf_c)
        if mk:
            kva = int(mk[-1])
        pre_clean = re.sub(r"\d+\s*k\s*v\b|\d{3,4}\s*k\s*va|\d\s*\+\s*\d|\d+\s*-?\s*way|\d+(\.\d+)?\s*(mm|m)\b", " ", pre)
        cnt = None
        mc = re.findall(NUM + r"\s*(?:x|nos?\.?|numbers?\s+of|units?\s+of|no\.)?\s*(?:[a-z/\-]+\s+){0,3}$", pre_clean)
        if mc:
            cnt = _n(mc[-1] if isinstance(mc[-1], str) else mc[-1][0])
        if cnt is None:
            ms = re.match(r"^\W*(?:x\s*)?(\d+)\s*(?:nos?|units?|numbers?)\b", suf_c)
            if ms:
                cnt = int(ms.group(1))
        ways = None
        mw = re.search(r"(\d)\s*\+\s*(\d)", suf_c) or re.search(r"(\d)\s*\+\s*(\d)", pre)
        subtype = None
        if mw:
            ways = int(mw.group(1)) + int(mw.group(2))
            subtype = f"{mw.group(1)}+{mw.group(2)}"
        else:
            mw = re.search(r"(\d+)\s*-?\s*w(?:ay)?\b", suf_c) or re.search(r"(\d+)\s*-?\s*w(?:ay)?\b", pre)
            if mw:
                ways = int(mw.group(1))
        dt_kind = "dry" if re.search(r"\bdry\b", pre) else "oil" if re.search(r"\boil\b", pre) else \
            "ester" if "ester" in pre else None
        span = t[s:e]
        if typ == "ltp":
            if "icog" in span:
                subtype = "icog"
            elif "pcc" in span:
                subtype = "ltpcc"
            elif "hybrid" in span:
                subtype = "hybrid"
        items.append({"type": typ, "count": cnt or 1, "kva": kva, "ways": ways, "subtype": subtype,
                      "dt_kind": dt_kind if typ == "transformer" else None, "text": t[s:e]})
    return items


def parse(text):
    """Return a structured query dict. Accepts a JSON string as well."""
    text = (text or "").strip()
    if text.startswith("{"):
        q = json.loads(text)
        q.setdefault("text", "")
        return _finalise(q)
    t = text.lower()
    q = {"text": text}

    # ---- installation / arrangement / context
    if re.search(r"open[\s\-]*to[\s\-]*sky|open\s+sky|outdoor|open\s+plot|\brg\b|recreation(al)?\s+ground|open\s+yard", t):
        q["installation"] = "open_to_sky"
    elif re.search(r"semi[\s\-]*open|\bshed\b|patra", t):
        q["installation"] = "semi_open"
    elif re.search(r"indoor|inside|part\s+of\s+(the\s+)?building|basement|stilt|podium|within\s+building", t):
        q["installation"] = "indoor"
    if re.search(r"distributed|split\s+(across|over|between)|separate\s+rooms|different\s+floors|g\s*\+\s*[12]", t):
        q["arrangement"] = "distributed"
    elif re.search(r"centrali[sz]ed|single\s+room|one\s+room", t):
        q["arrangement"] = "centralized"
    if re.search(r"standalone|stand[\s-]alone|separate\s+building|independent\s+building|single[\s-]storey", t):
        q["context"] = "standalone_building"
    elif "temporary" in t or "temp " in t:
        q["context"] = "temporary"
    elif re.search(r"part\s+of\s+(the\s+)?building|inside|residential|commercial|tower|hotel|hospital|office|building", t) \
            and q.get("installation") != "open_to_sky":
        q["context"] = "in_building"
    elif q.get("installation") == "open_to_sky":
        q["context"] = "outdoor_yard"

    # ---- floor
    floor = None
    for pat, fl in ((r"upper\s+basement|lower\s+basement|basement|cellar", "basement"),
                    (r"\bstilt", "stilt"), (r"podium", "podium"),
                    (r"\b(1st|first)\s*(floor|fl\b)", "first"), (r"\b(2nd|second)\s*floor", "second"),
                    (r"terrace|roof", "terrace"), (r"ground\s*(floor|level)|\bgf\b|\bgrade\b", "ground")):
        if re.search(pat, t):
            floor = fl
            break
    if floor:
        q["floor"] = floor
    if re.search(r"g\s*\+\s*[12]|multi[\s-]*(storey|floor)|two\s+floors", t):
        q["multi_storey"] = True

    # ---- platform / plinth / voltage / multiple
    if re.search(r"no\s+platform|without\s+platform", t):
        q["platform"] = False
    elif re.search(r"platform|raised\s+floor|steel\s+structure|ms\s+plate|chequered", t):
        q["platform"] = True
    m = re.search(r"plinth\D{0,12}(\d+(?:\.\d+)?)\s*(mm|m)?", t) or \
        re.search(r"(\d+(?:\.\d+)?)\s*(mm|m)\s+(?:high\s+|raised\s+|thick\s+)?(?:steel\s+|structural\s+|ms\s+)*(?:platform|plinth)", t)
    if m:
        q["plinth_mm"] = _mm(m.group(1), m.group(2))
    if re.search(r"\b33\s*kv|\bgis\b", t):
        q["voltage_kv"] = 33
    elif re.search(r"\b11\s*kv", t):
        q["voltage_kv"] = 11
    m = re.search(NUM + r"\s+(?:separate\s+)?(?:sub\s*-?\s*stations?|css|substation\s+rooms)", t)
    if m and (_n(m.group(1)) or 1) > 1:
        q["substation_count"] = _n(m.group(1))
        q["multiple_substations"] = True
    elif re.search(r"multiple\s+(sub\s*-?\s*stations?|css)", t):
        q["multiple_substations"] = True

    # ---- geometry  ("24x7 access" is an access phrase, not a room size)
    t = re.sub(r"\b24\s*[x×*]\s*7\b", " round-the-clock ", t)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(mm|m)?\s*(?:x|×|\*|by)\s*(\d+(?:\.\d+)?)\s*(mm|m)?(?!\s*k\s*va)(?!\d)", t)
    if m:
        a, b = _mm(m.group(1), m.group(2) or m.group(4)), _mm(m.group(3), m.group(4) or m.group(2))
        if 1500 <= min(a, b) and max(a, b) <= 60000:
            q["length_mm"], q["width_mm"] = max(a, b), min(a, b)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:sq\.?\s*m(?:tr|t|eter|etre)?s?|sqm|m2|m²|square\s+met(?:er|re)s?)", t)
    if m:
        q["area_m2"] = float(m.group(1))
    elif q.get("length_mm"):
        q["area_m2"] = round(q["length_mm"] * q["width_mm"] / 1e6, 1)
    m = re.search(r"(?:clear\s+height|height|ceiling|headroom|floor\s+height|floor[\s-]+to[\s-]+floor|slab\s+height)"
                  r"\D{0,15}?(\d+(?:\.\d+)?)\s*(mm|m)?", t)
    if m:
        q["clear_height_mm"] = _mm(m.group(1), m.group(2))
    if re.search(r"l[\s-]*shaped?|\bl\s+shape", t):
        q["shape"] = "L_shape"
    elif re.search(r"chamfer|trapez|angled|irregular", t):
        q["shape"] = "chamfered"
    elif re.search(r"rectang|square", t):
        q["shape"] = "rectangular"

    # ---- civil / openings / site
    feats = {}
    for key, pat in (("rolling_shutter", r"rolling\s*shutter|\bshutters?\b"), ("fire_exit", r"fire\s*exit|emergency\s+exit|\bfrd\b"),
                     ("ventilation", r"ventilat|louv|exhaust"), ("cutout", r"cut\s*-?\s*outs?|core\s*cut|slab\s+opening"),
                     ("beam", r"\bbeams?\b|ismc|ismb"), ("trench", r"trench|cable\s+tray|\btray\b|hdpe"),
                     ("staircase", r"stair"), ("platform", r"landing\s+platform|loading\s+platform")):
        if re.search(pat, t):
            feats[key] = True
    m = re.search(NUM + r"\s+doors?", t)
    if m:
        feats["door_count"] = _n(m.group(1))
    elif re.search(r"\bdoors?\b", t):
        feats["door"] = True
    if feats:
        q["civil"] = feats
    site = {}
    if re.search(r"way\s*-?\s*leave|access\s+(road|path|for)|approach|driveway|dp\s+road", t):
        site["access"] = True
    if re.search(r"trench|hdpe|cable\s+route|cable\s+tray", t):
        site["trench"] = True
    if re.search(r"round-the-clock|24\s*[x×]\s*7", t):
        site["access"] = True
    if site:
        q["site"] = site
    from ..core.places import building_type, localities
    b = building_type(t)
    if b:
        q["building"] = b
    loc = localities(t)
    if loc:
        q["locality"] = loc[0]

    # ---- equipment
    items = parse_np_list(text) or parse_equipment(text)
    if items:
        q["equipment_items"] = items
    return _finalise(q)


def _finalise(q):
    items = q.get("equipment_items") or []
    # accept a compact JSON form {"equipment": {"transformer": 2, "rmu": 1}, "kva": [1500]}
    if not items and isinstance(q.get("equipment"), dict):
        for k, v in q["equipment"].items():
            items.append({"type": k, "count": int(v), "kva": None, "ways": None, "subtype": None, "dt_kind": None})
        for i, k in enumerate(q.get("kva", [])):
            for it in items:
                if it["type"] == "transformer" and not it.get("kva"):
                    it["kva"] = int(k)
                    break
        q["equipment_items"] = items
    if items:
        counts = {}
        for it in items:
            counts[it["type"]] = counts.get(it["type"], 0) + int(it.get("count") or 1)
        q["counts"] = counts
        kv = []
        for it in items:
            if it["type"] in ("transformer", "packaged") and it.get("kva"):
                kv += [it["kva"]] * int(it.get("count") or 1)
        if kv:
            q["kva"] = kv
        rw = [it["ways"] for it in items if it["type"] == "rmu" and it.get("ways")]
        lw = [it["ways"] for it in items if it["type"] == "ltp" and it.get("ways")]
        if rw:
            q["rmu_ways"] = rw
        if lw:
            q["ltp_ways"] = lw
        dk = {it["dt_kind"] for it in items if it.get("dt_kind")}
        if dk:
            q["dt_kind"] = sorted(dk)
        if "kind" not in q:
            if counts.get("packaged"):
                q["kind"] = "package"
            elif counts.get("transformer"):
                q["kind"] = "dt_css"
            elif counts.get("ht_switchgear"):
                q["kind"] = "ht_switching"
            elif counts.get("rmu") or counts.get("metering"):
                q["kind"] = "metering_only"
    return q
