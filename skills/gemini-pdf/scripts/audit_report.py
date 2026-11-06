#!/usr/bin/env python3
"""Shared report vocabulary and stdlib-only recovery from durable chunk evidence.

Recovery does not import the normal audit parser: it still works if that helper
or jq fails. Chunk states own content status; missing auxiliary evidence cannot
invent chunks or replace a recorded status.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


def truncation_signature(audit: dict[str, Any], det: dict[str, Any]) -> bool:
    """Use the same first-attempt evidence in dispatch and both report paths."""
    values = det.get("chunk_gate", {}).get("values", {})
    ratio = values.get("completeness_ratio")
    missing = det.get("missing_sentences", values.get("missing_sentences"))
    critical_missing = sum(item.get("severity") == "critical" and item.get("kind") == "MISSING_TEXT"
                           and item.get("contradicted") is not True
                           for item in audit.get("findings", [])) if audit.get("valid") is True else 0
    return ((isinstance(ratio, (int, float)) and ratio < 0.6)
            or (isinstance(missing, (int, float)) and missing >= 5)
            or critical_missing >= 3)


def chunk_id(value: str) -> int | str:
    """Keep legacy integer IDs while preserving one-level bisection IDs."""
    if value.isdigit():
        return int(value)
    if re.fullmatch(r"\d+\.[12]", value):
        return value
    raise ValueError(f"invalid chunk id: {value}")


def score_summary(det: dict[str, Any]) -> dict[str, Any]:
    values = det.get("chunk_gate", {}).get("values", {})
    dimensions = det.get("dimensions", {})
    return {"pass": det.get("chunk_gate", {}).get("pass") is True, "numeric_recall": values.get("numeric", values.get("numeric_recall")),
            "trigram_recall": values.get("trigram", values.get("trigram_recall")),
            "completeness_ratio": values.get("completeness_ratio"),
            "math_ok": dimensions.get("math_quality", {}).get("score", 0) >= 85,
            "markers": det.get("audit_markers", 0), "chunk_score": det.get("overall_score", 0),
            "chunk_gate": det.get("chunk_gate", {"pass": False, "failed": ["unavailable"], "values": {}}),
            "missing_sentences": det.get("missing_sentences", 0), "injected_dois": det.get("injected_dois", 0)}


def summarize(chunks: list[dict[str, Any]], skipped: list[dict[str, Any]],
              settings: dict[str, Any]) -> tuple[dict[str, Any], int]:
    counts = settings.get("counts", {})
    for chunk in chunks:
        for skip in skipped:
            if str(skip["chunk"]) == str(chunk["id"]):
                degradation = f"{skip['reason']}:{skip['step']}"
                if degradation not in chunk["degradation"]:
                    chunk["degradation"].append(degradation)
    totals = {"chunks": len(chunks), **{name: sum(chunk["status"] == name for chunk in chunks)
              for name in ("verified", "unaudited", "unverified", "failed")},
              "repaired": sum(chunk["status"] == "verified" and chunk["repair"].get("attempted", False) for chunk in chunks),
              "mathpix": sum(chunk["final_source"] == "mathpix" for chunk in chunks),
              **{name: int(counts.get(name, 0)) for name in ("convert_calls", "audit_calls", "repair_calls")}}
    enabled = settings.get("audit_enabled", True) not in (False, 0, "0", "false")
    degradations = list(settings.get("degradations", []))
    for skip in skipped:
        if skip["step"] == "sanitize" and "sanitize-failed" not in degradations:
            degradations.append("sanitize-failed")
        degradation = f"{skip['reason']}:{skip['step']}:{skip['pages']}"
        if degradation not in degradations:
            degradations.append(degradation)
    for chunk in chunks:
        for degradation in chunk["degradation"]:
            if degradation not in degradations:
                degradations.append(degradation)
    if not enabled:
        status = "disabled"
    elif totals["failed"] or settings.get("aborted", False) or not chunks:
        status = "failed"
    elif totals["unverified"]:
        status = "partial"
    elif totals["unaudited"] or skipped or degradations:
        status = "degraded"
    else:
        status = "verified"
    residual = [{"chunk": chunk["id"], "pages": f"{chunk['first_page']}-{chunk['last_page']}",
                 "reason": chunk["reason"] or chunk["audit"].get("reason") or "repair-rounds-spent"}
                for chunk in chunks if chunk["status"] != "verified"]
    contradicted = sum(chunk["audit"].get("contradicted", 0) for chunk in chunks)
    deadline = dict(settings.get("deadline", {}))
    deadline.setdefault("seconds", 3300)
    deadline.setdefault("reserve", 120)
    deadline["hit"] = bool(deadline.get("hit", False) or any(skip["reason"] == "deadline" for skip in skipped))
    deadline["elapsed_s"] = int(settings.get("elapsed_s", deadline.get("elapsed_s", 0)))
    mathpix = {"cap_pages": 20, "pages_used": 0, "dispatches": 0, "est_usd": 0,
               "cap_exceeded": False, "ledger_path": "", **settings.get("mathpix", {})}
    models = settings.get("models", {"convert": "", "audit": "", "identity_check": {"convert": "unknown", "audit": "unknown"}})
    if status == "disabled":
        summary = f"AUDIT: status=disabled reason={settings.get('disabled_reason', 'flag')} chunks={totals['chunks']}"
    else:
        skip_summary = ",".join(dict.fromkeys(f"{item['step']}:{item['pages']}" for item in skipped)) or "none"
        external = sum(totals[key] for key in ("convert_calls", "audit_calls", "repair_calls")) + int(counts.get("hybrid_calls", 0))
        summary = (f"AUDIT: status={status} chunks={totals['chunks']} verified={totals['verified']} "
                   f"unaudited={totals['unaudited']} unverified={totals['unverified']} failed={totals['failed']} "
                   f"repaired={totals['repaired']} mathpix_chunks={totals['mathpix']} "
                   f"mathpix_pages={mathpix['pages_used']}/{mathpix['cap_pages']} "
                   f"cap_exceeded={'yes' if mathpix['cap_exceeded'] else 'no'} mathpix_usd={float(mathpix['est_usd']):.3f} "
                   f"deadline={'hit' if deadline['hit'] else 'ok'} residual={len(residual)} contradicted={contradicted} skipped={skip_summary} "
                   f"external=agy:{external}/mathpix:{mathpix['dispatches']} elapsed={deadline['elapsed_s']}s "
                   f"convert_model={models['convert']} audit_model={models['audit']}")
    result = {"schema": 1, "status": status, "run_nonce": settings.get("run_nonce", ""),
              "input": settings.get("input", {}), "models": models, "deadline": deadline, "mathpix": mathpix,
              "totals": totals, "chunks": chunks, "skipped_steps": skipped, "degradations": degradations,
              "residual": residual, "summary_line": summary}
    if not chunks and enabled:
        result["aborted"] = True
    strict = settings.get("strict", False) in (True, 1, "1", "true")
    code = 2 if status == "failed" else 3 if strict and status == "degraded" else 4 if strict and status == "partial" else 0
    return result, code


def read_object(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (OSError, ValueError):
        pass
    return dict(default or {})


def read_rows(path: Path) -> list[list[str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.reader(handle, delimiter="\t"))
    except OSError:
        return []


def recover(ledger: Path | None, settings: dict[str, Any]) -> tuple[dict[str, Any], int]:
    chunks = []
    for path in ledger.glob("chunk_*/state.json") if ledger else ():
        # A damaged state must not silently disappear from totals or become an
        # invented failed chunk. Let the caller surface unreadable evidence.
        state = json.loads(path.read_text(encoding="utf-8"))
        missing = {"valid": False, "status": "skipped", "reason": state.get("reason") or "report-error",
                   "findings": [], "critical": 0, "major": 0, "minor": 0, "contradicted": 0}
        attempts = []
        for row in read_rows(path.with_name("attempts.tsv")):
            if len(row) != 5 or not row[0].isdigit():
                continue
            index, kind, md, det, audit = row
            unavailable = dict(missing)
            if audit.startswith("UNAUDITED:"):
                unavailable["reason"] = audit.split(":", 1)[1]
                if unavailable["reason"] not in {"deadline", "lock", "audit-model-unavailable", "too-large"}:
                    unavailable["status"] = "unparseable"
            audit_data, det_data = read_object(audit, unavailable), read_object(det)
            attempts.append({"index": int(index), "kind": kind,
                             "audit": audit_data, "score": score_summary(det_data),
                             "truncation_signature": int(index) == 0 and truncation_signature(audit_data, det_data),
                             "selected": int(index) == state.get("selected")})
        selected = next((item for item in attempts if item["selected"]), {})
        audit = dict(selected.get("audit", missing))
        audit.setdefault("contradicted", sum(item.get("contradicted") is True for item in audit.get("findings", [])))
        audit["attempts"] = int(state.get("audit_attempts", len(attempts)))
        chunks.append({"id": state["id"], "first_page": state["first_page"], "last_page": state["last_page"],
                       "status": state["status"], "final_source": state.get("final_source", "pro"),
                       "reason": state.get("reason", ""), "audit": audit,
                       "score": selected.get("score", score_summary({})),
                       "repair": state.get("repair", {"attempted": False, "outcome": "not-needed"}),
                       "mathpix": state.get("mathpix", {"attempted": False, "pages": 0, "reason": ""}),
                       "attempts": attempts, "degradation": state.get("degradation", []),
                       "residual": [] if state["status"] == "verified" else [
                           {key: item.get(key) for key in ("page", "kind", "severity", "detail")}
                           for item in audit.get("findings", []) if item.get("contradicted") is not True]})
        if state.get("bisected"):
            chunks[-1]["bisected"] = True
    chunks.sort(key=lambda item: (item["first_page"], str(item["id"])))
    skipped = []
    for row in read_rows(ledger / "skipped.tsv") if ledger else ():
        if len(row) == 4:
            try:
                parsed_id = chunk_id(row[0])
            except ValueError:
                continue
            item = {"chunk": parsed_id, "step": row[1], "reason": row[2], "pages": row[3]}
            if item not in skipped:
                skipped.append(item)
    settings = {**settings, "degradations": list(settings.get("degradations", []))}
    if "report-error" not in settings["degradations"]:
        settings["degradations"].append("report-error")
    return summarize(chunks, skipped, settings)


def recovery_settings() -> dict[str, Any]:
    # The shell supplies current counters independently of the failed settings
    # writer; a stale or truncated settings.json cannot reset accounting.
    def value(name: str, default: str = "") -> str:
        return os.environ.get("REPORT_" + name, default)
    def number(name: str) -> int:
        return int(value(name, "0"))
    return {"run_nonce": value("NONCE"),
            "input": {"pdf": value("PDF"), "pages": number("PAGES"), "sha256": value("SHA"), "text_digest": ""},
            "models": {"convert": value("CONVERT"), "audit": value("AUDIT"),
                       "identity_check": {"convert": value("CI"), "audit": value("AI")}},
            "deadline": {"seconds": number("DEADLINE"), "reserve": number("RESERVE"), "hit": value("HIT") == "true"},
            "elapsed_s": number("ELAPSED"),
            "mathpix": {"cap_pages": number("CAP"), "pages_used": number("USED"), "dispatches": number("DISPATCHES"),
                        "est_usd": number("USED") * float(value("RATE", "0")),
                        "cap_exceeded": value("EXCEEDED") == "true", "ledger_path": value("LEDGER")},
            "strict": value("STRICT") == "1", "counts": {
                "convert_calls": number("CC"), "audit_calls": number("AC"),
                "repair_calls": number("RC"), "hybrid_calls": number("HC")}}


def main() -> None:
    ledger, output = sys.argv[1:]
    result, code = recover(Path(ledger) if ledger else None, recovery_settings())
    target = Path(output)
    temporary = target.with_name(target.name + ".tmp-" + str(os.getpid()))
    try:
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print(code)
    print(result["status"])
    print(result["summary_line"])


if __name__ == "__main__":
    main()
