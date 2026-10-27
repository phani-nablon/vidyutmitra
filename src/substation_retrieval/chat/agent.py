"""Chat assistant: requirement in natural language -> structured query -> retrieval -> grounded answer.

With an LLM key the model (1) turns the message plus the conversation into query fields, merged with
the rule parser so nothing the user said is lost, and (2) writes the answer from the retrieval JSON
only. Without a key the rule parser and a templated answer are used. Follow-up questions about the
last results ("why is #2 lower?", "what conflicts does Sienna have?") are answered from those results.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid

from ..extraction.llm import LLMUnavailable, parse_json
from ..retrieval.query import build_query

PARSE_SYSTEM = """You convert a substation requirement into JSON fields for retrieval. Use only what the user states.
Fields (omit unknown): installation (indoor|open_to_sky|semi_open), arrangement (centralized|distributed),
context (in_building|standalone_building|outdoor_yard|temporary), floor (basement|stilt|podium|ground|first|second|terrace),
voltage_kv, substation_count, multiple_substations, platform (bool), plinth_mm, length_mm, width_mm, area_m2,
clear_height_mm, beam_bottom_mm, door_width_mm, shape (rectangular|L_shape|chamfered|irregular),
equipment (object type->count, types: transformer, rmu, metering, ltp, ht_switchgear, packaged, aux), kva (list of transformer kVA),
dt_kind (dry|oil|ester), civil (object of booleans: rolling_shutter, fire_exit, ventilation, cutout, beam, staircase; door_count int),
site (object: access, trench), site_distances (object feature->mm for nalla, dp_road, internal_road, tree, compound_wall, structure).
"room" means the substation. Return {"intent": "search" | "followup" | "smalltalk", "fields": {...}}.
"followup" = a question about results already shown, not a new requirement."""

ANSWER_SYSTEM = """You are the CSS design-reuse assistant for AEML substation engineers. Answer from the RESULTS JSON only.
Lead with the recommendation (best design and verdict reuse/adapt/new), then 2-4 short bullets per relevant design:
why it matches (matched facets, equipment, geometry), the conflicts/warnings that block reuse, and what to adapt.
Mention which engine produced the ranking (ontology KG or RAG fallback) and the coverage when it is low
(the query gave few details). Never invent numbers. Keep it under 220 words. Plain sentences, no marketing tone."""


class ChatSessions:
    def __init__(self, max_sessions: int = 200):
        self._d: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.max = max_sessions

    def get(self, sid: str | None) -> dict:
        with self._lock:
            if not sid or sid not in self._d:
                sid = sid or uuid.uuid4().hex[:12]
                self._d[sid] = {"id": sid, "history": [], "query": None, "last": None, "siteplan": None, "t": time.time()}
                if len(self._d) > self.max:
                    oldest = min(self._d.values(), key=lambda s: s["t"])
                    self._d.pop(oldest["id"], None)
            s = self._d[sid]
            s["t"] = time.time()
            return s


class ChatAgent:
    def __init__(self, services):
        self.sv = services
        self.sessions = ChatSessions()

    def _llm_fields(self, message: str, history: list[dict]) -> dict | None:
        llm = self.sv.llm
        if not llm.available:
            return None
        convo = "\n".join(f'{m["role"]}: {m["content"][:600]}' for m in history[-6:])
        try:
            out = llm.complete(PARSE_SYSTEM, f"Conversation so far:\n{convo}\n\nUser: {message}", json_mode=True, max_tokens=1200)
            return out if isinstance(out, dict) else None
        except Exception:
            return None

    def handle(self, message: str, session_id: str | None = None, siteplan: dict | None = None, mode: str | None = None,
               structured: dict | None = None, k: int = 5) -> dict:
        s = self.sessions.get(session_id)
        if siteplan:
            s["siteplan"] = siteplan
        s["history"].append({"role": "user", "content": message})
        parsed = self._llm_fields(message, s["history"][:-1])
        intent = (parsed or {}).get("intent") or ("followup" if (s["last"] and _looks_followup(message)) else "search")
        if intent == "followup" and s["last"]:
            answer = self._answer(message, s["last"], s["history"], followup=True)
            s["history"].append({"role": "assistant", "content": answer})
            return {"session_id": s["id"], "answer": answer, "intent": "followup", "search": None}
        if intent == "smalltalk" and not structured:
            answer = ("Describe the new site and I will find the closest historic CSS designs: installation (indoor / open to sky), "
                      "floor, equipment (e.g. 2 x 1500 kVA DT, 1 RMU, 4 LTP), room size or an uploaded site plan, clear height, "
                      "doors / rolling shutter, trench, and nearby nalla / DP road distances.")
            s["history"].append({"role": "assistant", "content": answer})
            return {"session_id": s["id"], "answer": answer, "intent": "smalltalk", "search": None}
        fields = dict((parsed or {}).get("fields") or {})
        if structured:
            fields.update(structured)
        q = build_query(message, None, s["siteplan"])
        # LLM fields only fill gaps the rule parser left (rules are deterministic on numbers)
        if fields:
            llm_q = build_query("", {k_: v for k_, v in fields.items() if v not in (None, "", [], {})})
            for k_, v in llm_q.items():
                if k_ not in q or q[k_] in (None, "", [], {}):
                    q[k_] = v
        if s["query"] and _is_refinement(message):
            base = dict(s["query"])
            base.update({k_: v for k_, v in q.items() if v not in (None, "", [], {}) and k_ != "text"})
            base["text"] = (s["query"].get("text", "") + " " + message).strip()
            q = base
        s["query"] = q
        res = self.sv.orchestrator.search(q, k, mode)
        s["last"] = res
        answer = self._answer(message, res, s["history"])
        s["history"].append({"role": "assistant", "content": answer})
        return {"session_id": s["id"], "answer": answer, "intent": "search", "search": res}

    def _answer(self, message: str, res: dict, history: list[dict], followup: bool = False) -> str:
        llm = self.sv.llm
        compact = _compact(res)
        if llm.available:
            try:
                msgs = [{"role": m["role"], "content": m["content"][:1500]} for m in history[-6:-1]]
                msgs.append({"role": "user", "content": f"{message}\n\nRESULTS JSON:\n{json.dumps(compact)[:14000]}"})
                return llm.chat(ANSWER_SYSTEM, msgs, max_tokens=900).strip()
            except (LLMUnavailable, Exception):
                pass
        return _template(compact)


def _looks_followup(m: str) -> bool:
    return bool(re.search(r"\b(why|explain|which one|compare|what about|#\d|first|second|third|top|conflict|that one|this design)\b", m.lower())) \
        and not re.search(r"\d+\s*k\s*va|\bdts?\b|\brmu\b|\bltp\b", m.lower())


def _is_refinement(m: str) -> bool:
    return bool(re.search(r"^\s*(also|and|but|now|same|with|without|change|make it|instead)\b", m.lower()))


def _compact(res: dict) -> dict:
    return {"engine": res.get("engine"), "trace": res.get("trace"), "asked": res.get("asked"), "feasibility": res.get("feasibility"),
            "results": [{"rank": i + 1, "file": r["file_name"], "substation": r["substation_name"], "score": r["score"],
                         "verdict": r["verdict"], "coverage": r["coverage"], "components": r["components"],
                         "conflicts": r["conflicts"], "warnings": [c["detail"] for c in r["checks"] if c["status"] == "warn"],
                         "passes": [c["detail"] for c in r["checks"] if c["status"] == "pass"][:4],
                         "why": r["explanation"][:6], "status": r.get("status")} for i, r in enumerate(res.get("results", [])[:6])]}


def _template(c: dict) -> str:
    rs = c["results"]
    if not rs:
        return "No historic design matched. Add details such as installation, floor, equipment and room size, or upload a site plan."
    eng = {"kg": "ontology knowledge-graph matching", "rag": "RAG matching (fallback)", "both": "both engines"}.get(c["engine"], c["engine"])
    top = rs[0]
    lines = [f"Best match: {top['file']} ({top['substation']}), score {top['score']:.2f}, verdict {top['verdict']} - ranked by {eng}."]
    for r in rs[:3]:
        bits = []
        if r["conflicts"]:
            bits.append("conflicts: " + "; ".join(r["conflicts"][:2]))
        if r["warnings"]:
            bits.append("warnings: " + "; ".join(r["warnings"][:2]))
        if r["why"]:
            bits.append("matched: " + ", ".join(r["why"][:3]))
        lines.append(f"- #{r['rank']} {r['file']} - {r['score']:.2f} {r['verdict']}" + (" | " + " | ".join(bits) if bits else ""))
    if c.get("trace"):
        lines.append("Note: " + "; ".join(c["trace"]))
    if rs and rs[0]["coverage"] < 0.4:
        lines.append(f"Coverage is low ({rs[0]['coverage']:.0%}): the requirement gave few details, so rankings are tentative.")
    return "\n".join(lines)
