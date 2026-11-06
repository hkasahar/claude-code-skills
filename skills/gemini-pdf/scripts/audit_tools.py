#!/usr/bin/env python3
"""Local audit parsing, decisions, repair hints, and schema-1 run reports.

The shell owns dispatch and deadlines. This module only reads and writes local
files; model strings never become shell commands or template replacements.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

from audit_report import chunk_id, score_summary, summarize, truncation_signature


SCHEMA_PATH = Path(__file__).with_name("audit_schema.json")
SEVERITIES = ("critical", "major", "minor")
FRONT_MATTER = re.compile(r"\A(?:\ufeff)?---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)", re.S)
OMISSION_CLAIM = re.compile(r"entire page|whole page|abruptly (?:truncat|cut)|omitted|missing from the candidate|is missing", re.I)
TAG = re.compile(r"\\tag\s*\{\s*([^{}]+?)\s*\}")
PRINTED_TAG = re.compile(r"\(\s*((?:[A-Za-z]\.)?\d+(?:\.\d+)*[A-Za-z]?)\s*\)")


def read_json(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def atomic_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: str | Path, data: Any) -> None:
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def validate_schema(value: Any, schema: dict[str, Any], where: str = "audit", *, enforce_caps: bool = True) -> None:
    """Validate the contract, optionally leaving output caps to the parser."""
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    expected = schema.get("type")
    if expected and type(value) is not types[expected]:
        raise ValueError(f"schema:{where}:type")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"schema:{where}:const")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"schema:{where}:enum")
    if isinstance(value, dict):
        if any(key not in value for key in schema.get("required", [])):
            raise ValueError(f"schema:{where}:required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"schema:{where}:extra-member")
        for key, child in properties.items():
            if key in value:
                validate_schema(value[key], child, f"{where}.{key}", enforce_caps=enforce_caps)
    if isinstance(value, list):
        if enforce_caps and (len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf"))):
            raise ValueError(f"schema:{where}:items")
        for index, item in enumerate(value):
            validate_schema(item, schema.get("items", {}), f"{where}[{index}]", enforce_caps=enforce_caps)
    if enforce_caps and isinstance(value, str) and len(value) > schema.get("maxLength", float("inf")):
        raise ValueError(f"schema:{where}:length")
    if type(value) is int and value < schema.get("minimum", -float("inf")):
        raise ValueError(f"schema:{where}:minimum")


def clamp_audit(result: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Bound stored evidence only after every original member was validated."""
    clamped: list[dict[str, Any]] = []
    for collection in ("pages", "findings"):
        contract = schema["properties"][collection]
        if collection == "findings" and len(result[collection]) > contract["maxItems"]:
            cap = contract["maxItems"]
            clamped.append({"path": collection, "from": len(result[collection]), "to": cap})
            result[collection] = result[collection][:cap]
        for index, item in enumerate(result[collection]):
            for field, field_schema in contract["items"]["properties"].items():
                cap = field_schema.get("maxLength")
                if cap is not None and field in item and len(item[field]) > cap:
                    clamped.append({"path": f"{collection}[{index}].{field}", "from": len(item[field]), "to": cap})
                    item[field] = item[field][:cap] + "…"
    if clamped:
        result["truncated"] = True
    return clamped


def unfence(text: str) -> str:
    text = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.S | re.I)
    return match.group(1).strip() if match else text


def decode_object(text: str) -> Any:
    # NaN and duplicate keys can otherwise acquire contradictory meanings in
    # validators and JSON consumers, so reject them at the transport boundary.
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("parse:duplicate-key")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise ValueError("parse:nonfinite")

    return json.loads(unfence(text), object_pairs_hook=pairs, parse_constant=constant)


def extract_payload(raw: str, nonce: str, path: str = "") -> tuple[dict[str, Any], str]:
    text = unfence(raw)
    if not text:
        raise ValueError("tool-denied")
    prefix = f"{path}." if path else ""
    if "AUDIT-JSON-BEGIN" in text or "AUDIT-JSON-END" in text:
        begin = f"<!-- AUDIT-JSON-BEGIN {nonce} -->"
        end = f"<!-- AUDIT-JSON-END {nonce} -->"
        # An envelope response contains escaped marker lines; decode that
        # envelope first. A text-mode answer must have one exact nonce pair.
        if not text.startswith("{"):
            lines = text.splitlines()
            if lines.count(begin) != 1 or lines.count(end) != 1:
                raise ValueError("parse:sentinel")
            first, last = lines.index(begin), lines.index(end)
            if first >= last or text.count("AUDIT-JSON-BEGIN") != 1 or text.count("AUDIT-JSON-END") != 1:
                raise ValueError("parse:sentinel")
            if any(line.strip() for line in lines[:first] + lines[last + 1:]):
                raise ValueError("parse:sentinel-trailing-text")
            result = decode_object("\n".join(lines[first + 1:last]))
            if not isinstance(result, dict):
                raise ValueError("parse:not-object")
            return result, prefix + "sentinel"
    try:
        payload = decode_object(text)
    except (json.JSONDecodeError, ValueError):
        first, last = text.find("{"), text.rfind("}")
        if first < 0 or last <= first:
            raise ValueError("parse:json") from None
        payload = decode_object(text[first:last + 1])
        prefix += "braces."
    if not isinstance(payload, dict):
        raise ValueError("parse:not-object")
    if payload.get("denied_actions"):
        raise ValueError("tool-denied")
    if "response" in payload and not str(payload["response"] or "").strip():
        raise ValueError("tool-denied")
    if payload.get("status") == "ERROR":
        raise ValueError("agy-error")
    if payload.get("event") == "result" and isinstance(payload.get("result"), dict):
        return extract_payload(json.dumps(payload["result"]), nonce, prefix + "result")
    if "structured_output" in payload:
        structured = payload["structured_output"]
        if isinstance(structured, dict):
            return structured, prefix + "structured_output"
        if isinstance(structured, str) and structured.strip():
            return extract_payload(structured, nonce, prefix + "structured_output")
    if "schema" in payload:
        return payload, prefix + "json"
    if isinstance(payload.get("response"), str):
        return extract_payload(payload["response"], nonce, prefix + "response")
    for key, value in payload.items():
        if isinstance(value, str):
            try:
                candidate, candidate_path = extract_payload(value, nonce, prefix + key)
                if "schema" in candidate:
                    return candidate, candidate_path
            except (ValueError, json.JSONDecodeError):
                continue
    return payload, prefix + "json"


def parse_audit(raw: str, nonce: str, first: int, last: int, major_max: int = 2) -> dict[str, Any]:
    result, parse_path = extract_payload(raw, nonce)
    schema = read_json(SCHEMA_PATH)
    # Even findings that will be dropped must have valid structure and pages.
    # Both transport modes share this path; model length overruns are evidence,
    # not a reason to discard an otherwise usable audit.
    validate_schema(result, schema, enforce_caps=False)
    if [page["page"] for page in result["pages"]] != list(range(first, last + 1)):
        raise ValueError("pages_incomplete")
    for page in result["pages"]:
        if not page["readable"]:
            raise ValueError(f"unreadable-page:{page['page']}")
        if not page["verified"]:
            raise ValueError(f"unverified-page:{page['page']}")
    if any(not first <= item["page"] <= last for item in result["findings"]):
        raise ValueError("finding-page-out-of-range")
    # Only deterministic local evidence can annotate a model finding.
    for item in result["findings"]:
        item.pop("contradicted", None)
        item.pop("contradicted_by", None)
    clamped = clamp_audit(result, schema)
    if clamped:
        parse_path += ";clamped=" + json.dumps(clamped, ensure_ascii=False, separators=(",", ":"))
    counts = {severity: sum(item["severity"] == severity for item in result["findings"]) for severity in SEVERITIES}
    passed = counts["critical"] == 0 and counts["major"] <= major_max
    return {**result, "valid": True, "status": "pass" if passed else "fail", "reason": "", "contradicted": 0,
            "parse_path": parse_path, "clamped": clamped, **counts,
            "verdict_mismatch": result["verdict"] != ("PASS" if passed else "FAIL")}


def unavailable(reason: str, status: str = "skipped") -> dict[str, Any]:
    return {"valid": False, "status": status, "reason": reason, "findings": [], "critical": 0, "major": 0, "minor": 0, "contradicted": 0}


def load_audit(reference: str) -> dict[str, Any]:
    if reference.startswith("UNAUDITED:"):
        reason = reference.split(":", 1)[1]
        status = "skipped" if reason in {"deadline", "lock", "audit-model-unavailable", "too-large"} else "unparseable"
        return unavailable(reason, status)
    try:
        audit = read_json(reference)
    except (OSError, ValueError):
        return unavailable("parse", "unparseable")
    return audit


def det_pass(det: dict[str, Any]) -> bool:
    return det.get("chunk_gate", {}).get("pass") is True


def equation_tags(text: str) -> set[str]:
    """Include tags rendered by pdftotext at either end of a display line."""
    labels = {re.sub(r"\s+", "", match[1]) for match in TAG.finditer(text)}
    for line in text.splitlines():
        for match in PRINTED_TAG.finditer(line):
            if not line[:match.start()].strip() or not line[match.end():].strip():
                labels.add(re.sub(r"\s+", "", match[1]))
    return labels


def read_page_texts(directory: str | Path | None) -> dict[int, str]:
    pages = {}
    if directory:
        for path in Path(directory).glob("*.txt"):
            match = re.fullmatch(r"(?:page_)?(\d+)\.txt", path.name)
            if match:
                pages[int(match[1])] = path.read_text(encoding="utf-8", errors="replace")
    return pages


def cross_check_omissions(audit: dict[str, Any], det: dict[str, Any], major_max: int = 2,
                          candidate_text: str | None = None,
                          page_texts: dict[int, str] | None = None) -> None:
    """Reconcile only broad omission claims, preserving all original findings."""
    if audit.get("valid") is not True:
        return
    candidate_tags = equation_tags(candidate_text) if candidate_text is not None else set()
    for item in audit.get("findings", []):
        item.pop("contradicted", None)
        item.pop("contradicted_by", None)
        if item.get("kind") not in {"MISSING_TEXT", "SEAM_GAP", "TABLE_ERROR"}:
            continue
        claim = " ".join(str(item.get(field, "")) for field in ("detail", "pdf_text"))
        if item.get("kind") != "SEAM_GAP" and not OMISSION_CLAIM.search(claim):
            continue
        page = item.get("page")
        check = det.get("page_checks", {}).get(str(page), {})
        source_trigrams = check.get("trigrams")
        note = ""
        if isinstance(source_trigrams, (int, float)) and source_trigrams >= 40:
            missing = check.get("missing_sentences", {})
            if check.get("trigram_recall", 0) >= 0.40 and isinstance(missing, dict) and missing.get("count") == 0:
                note = (f"page_checks[{page}]: {source_trigrams} source trigrams, "
                        f"trigram_recall={check['trigram_recall']:.4f} >= 0.40, missing_sentences.count=0")
        elif (isinstance(source_trigrams, (int, float)) and 0 <= source_trigrams < 40
              and check.get("numeric_recall", 0) >= 0.9 and candidate_text is not None
              and page_texts is not None and page in page_texts):
            tags = equation_tags(page_texts[page])
            if tags <= candidate_tags:
                note = (f"page_checks[{page}]: {source_trigrams} source trigrams < 40, "
                        f"numeric_recall={check['numeric_recall']:.4f} >= 0.9; "
                        f"all {len(tags)} printed equation tags present in candidate")
        if note:
            item.update(contradicted=True, contradicted_by=note)
    active = [item for item in audit.get("findings", []) if item.get("contradicted") is not True]
    counts = {severity: sum(item.get("severity") == severity for item in active) for severity in SEVERITIES}
    passed = counts["critical"] == 0 and counts["major"] <= major_max
    audit.update(**counts, contradicted=sum(item.get("contradicted") is True for item in audit.get("findings", [])),
                 status="pass" if passed else "fail",
                 verdict_mismatch=audit.get("verdict") != ("PASS" if passed else "FAIL"))


def predicate(audit: dict[str, Any], det: dict[str, Any], major_max: int = 2,
              candidate_text: str | None = None, page_texts: dict[int, str] | None = None) -> str:
    if not audit.get("valid", False):
        return "UNAUDITED_OK" if det_pass(det) else "UNAUDITED_DETFAIL"
    cross_check_omissions(audit, det, major_max, candidate_text, page_texts)
    findings = [item for item in audit.get("findings", []) if item.get("contradicted") is not True]
    critical = sum(item.get("severity") == "critical" for item in findings)
    major = sum(item.get("severity") == "major" for item in findings)
    return "PASS" if critical == 0 and major <= major_max and det_pass(det) else "REPAIR"


def one_line(text: Any, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()[:limit]


def ordered_findings(audit: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted((item for item in audit.get("findings", []) if item.get("contradicted") is not True),
                  key=lambda item: SEVERITIES.index(item.get("severity", "minor")))


def deterministic_hints(det: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for page, check in sorted(det.get("page_checks", {}).items(), key=lambda pair: int(pair[0])):
        missing = check.get("missing_sentences", {})
        heads = missing.get("heads", []) if isinstance(missing, dict) else check.get("missing_sentence_heads", [])
        for head in heads:
            lines.append(f'[page {page}] MISSING_TEXT (deterministic): PDF sentence absent: "{one_line(head, 90)}"')
        if check.get("trigrams", 0) >= 40 and check.get("trigram_recall", 1) < 0.35:
            lines.append(f"[page {page}] MISSING_TEXT (deterministic): prose recall {check['trigram_recall']:.2f}; a block is likely missing.")
        if check.get("missing_numerals"):
            lines.append(f"[page {page}] NUMBER_ERROR (deterministic): Source numerals absent: {one_line(', '.join(map(str, check['missing_numerals'])), 240)}")
    for doi in det.get("injected_doi_list", []):
        lines.append(f"[page ?] EXTRA_TEXT (deterministic): DOI not found in the PDF text layer: {one_line(doi, 240)} — verify against the PDF; delete it ONLY if the PDF does not print it")
    gate = det.get("chunk_gate", {})
    for floor in gate.get("failed", []):
        value = gate.get("values", {}).get(floor, "unavailable")
        lines.append(f"[page ?] deterministic: failed floor {one_line(floor)} (observed {one_line(value)}).")
    if det.get("missing_numerals"):
        lines.append(f"[page ?] NUMBER_ERROR (deterministic): Source numerals absent: {one_line(', '.join(map(str, det['missing_numerals'])), 240)}")
    return lines


def hints(audit: dict[str, Any], det: dict[str, Any]) -> str:
    lines = []
    if truncation_signature(audit, det):
        lines.append("truncation_signature: true — grossly incomplete first attempts require a fresh conversion before edit repair.")
    for finding in ordered_findings(audit):
        description = one_line(finding.get("detail", ""))
        quotes = []
        for key, label in (("pdf_text", "PDF"), ("candidate_text", "candidate"), ("anchor", "anchor")):
            if finding.get(key):
                quotes.append(f'{label}: "{one_line(finding[key])}"')
        suffix = ("; " + "; ".join(quotes)) if quotes else ""
        lines.append(f"[page {finding['page']}] {finding['kind']} ({finding['severity']}): {description}{suffix}")
    lines.extend(deterministic_hints(det))
    return "\n".join(f"{index}. {line}" for index, line in enumerate(lines[:25], 1)) + ("\n" if lines else "")


def comment_text(text: Any) -> str:
    # Model-produced detail must not close its HTML comment and become content.
    return one_line(text).replace("--", "—").replace("<", "&lt;").replace(">", "&gt;")


def markers(audit: dict[str, Any], det: dict[str, Any], limit: int = 10) -> str:
    lines = [f"<!-- AUDIT: {item['kind']} — {comment_text(item['detail'])} (p. {item['page']}) -->"
             for item in ordered_findings(audit)]
    for hint in deterministic_hints(det):
        match = re.match(r"\[page ([^]]+)\] (.*)", hint)
        if match:
            lines.append(f"<!-- AUDIT: {comment_text(match[2])} (p. {match[1]}) -->")
    return "\n".join(lines[:max(0, min(limit, 10))]) + ("\n" if lines and limit > 0 else "")


def insert_markers(part: str, marker_text: str) -> str:
    if not marker_text.strip():
        return part
    match = FRONT_MATTER.match(part)
    boundary = match.end() if match else 0
    return part[:boundary] + marker_text.rstrip() + "\n\n" + part[boundary:].lstrip("\n")


def read_attempts(path: str | Path) -> list[dict[str, Any]]:
    attempts = []
    if not Path(path).is_file():
        return attempts
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if not row or row[0] == "index":
                continue
            if len(row) != 5:
                raise ValueError("attempts.tsv requires index,kind,md,det,audit")
            index, kind, md, det_path, audit_path = row
            try:
                det = read_json(det_path)
            except (OSError, ValueError):
                det = {}
            attempts.append({"index": int(index), "kind": kind, "md": md,
                             "det": det, "audit": load_audit(audit_path)})
    return attempts


def selection_key(attempt: dict[str, Any], major_max: int = 2) -> tuple[Any, ...]:
    audit, det = attempt["audit"], attempt["det"]
    path = Path(attempt["md"])
    decision = predicate(audit, det, major_max, path.read_text(encoding="utf-8", errors="replace"),
                         read_page_texts(path.parent / "page_texts"))
    rank = 3 if decision == "PASS" else 2 if decision == "UNAUDITED_OK" else 1 if det_pass(det) else 0
    counts = {severity: sum(item.get("severity") == severity for item in ordered_findings(audit)) for severity in SEVERITIES}
    return (rank, -counts["critical"], -counts["major"], det.get("overall_score", 0),
            -counts["minor"], -det.get("uncertain_markers", 0))


def select_attempt(attempts: list[dict[str, Any]], major_max: int = 2) -> dict[str, Any] | None:
    candidates = [item for item in attempts if item["md"] and Path(item["md"]).is_file()
                  and Path(item["md"]).read_text(encoding="utf-8", errors="replace").strip()]
    return max(candidates, key=lambda item: selection_key(item, major_max)) if candidates else None


def report(ledger_dir: Path, settings: dict[str, Any]) -> tuple[dict[str, Any], int]:
    chunks = []
    for state_path in ledger_dir.glob("chunk_*/state.json"):
        state = read_json(state_path)
        attempts = read_attempts(state_path.with_name("attempts.tsv"))
        selected = next((item for item in attempts if item["index"] == state.get("selected")), None)
        if selected is None:
            selected = select_attempt(attempts, int(settings.get("audit_major_max", 2)))
        selected = selected or {"audit": unavailable(state.get("reason", "no-content")), "det": {}}
        audit = dict(selected["audit"])
        audit.setdefault("contradicted", sum(item.get("contradicted") is True for item in audit.get("findings", [])))
        audit["attempts"] = int(state.get("audit_attempts", sum(item["audit"].get("attempts", 1) for item in attempts)))
        score = score_summary(selected["det"])
        residual = [] if state["status"] == "verified" else [
            {key: item.get(key) for key in ("page", "kind", "severity", "detail")}
            for item in audit.get("findings", []) if item.get("contradicted") is not True]
        chunk = {"id": state["id"], "first_page": state["first_page"], "last_page": state["last_page"],
                 "status": state["status"], "final_source": state.get("final_source", "pro"), "audit": audit,
                 "score": score, "repair": state.get("repair", {"attempted": False, "outcome": "not-needed"}),
                 "mathpix": state.get("mathpix", {"attempted": False, "pages": 0, "reason": ""}),
                 "attempts": [{"index": item["index"], "kind": item["kind"], "audit": item["audit"],
                               "score": score_summary(item["det"]), "selected": item is selected,
                               "truncation_signature": item["index"] == 0 and truncation_signature(item["audit"], item["det"])} for item in attempts],
                 "residual": residual, "reason": state.get("reason", ""),
                 "degradation": state.get("degradation", [])}
        if state.get("bisected"):
            chunk["bisected"] = True
        chunks.append(chunk)
    chunks.sort(key=lambda item: (item["first_page"], str(item["id"])))
    skipped = []
    skip_path = ledger_dir / "skipped.tsv"
    if skip_path.is_file():
        with skip_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.reader(handle, delimiter="\t"):
                if row and row[0] != "chunk":
                    if len(row) != 4:
                        raise ValueError("skipped.tsv requires chunk,step,reason,pages")
                    item = {"chunk": chunk_id(row[0]), "step": row[1], "reason": row[2], "pages": row[3]}
                    if item not in skipped:
                        skipped.append(item)
    return summarize(chunks, skipped, settings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    parse_cmd = commands.add_parser("parse")
    parse_cmd.add_argument("raw")
    parse_cmd.add_argument("--nonce", required=True)
    parse_cmd.add_argument("--first", type=int, required=True)
    parse_cmd.add_argument("--last", type=int, required=True)
    parse_cmd.add_argument("--out", required=True)
    parse_cmd.add_argument("--major-max", type=int, default=2)
    for name in ("predicate", "hints", "markers"):
        command = commands.add_parser(name)
        command.add_argument("--audit", required=True)
        command.add_argument("--det", required=True)
        if name == "predicate":
            command.add_argument("--major-max", type=int, default=2)
            command.add_argument("--with-truncation-signature", action="store_true")
            command.add_argument("--candidate")
            command.add_argument("--page-texts-dir")
        elif name == "hints":
            command.add_argument("--out", required=True)
        else:
            command.add_argument("--limit", type=int, default=10)
    insert = commands.add_parser("insert-markers")
    insert.add_argument("part")
    insert.add_argument("markers")
    select = commands.add_parser("select")
    select.add_argument("attempts")
    select.add_argument("--major-max", type=int, default=2)
    report_cmd = commands.add_parser("report")
    report_cmd.add_argument("--ledger-dir", type=Path, required=True)
    report_cmd.add_argument("--out", required=True)
    report_cmd.add_argument("--settings", required=True)
    args = parser.parse_args()
    try:
        if args.command == "parse":
            try:
                audit = parse_audit(Path(args.raw).read_text(encoding="utf-8", errors="replace"), args.nonce, args.first, args.last, args.major_max)
            except (OSError, ValueError, RecursionError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else "parse:read"
                atomic_json(args.out, unavailable(reason, "unparseable"))
                print(one_line(reason), file=sys.stderr)
                return 2
            atomic_json(args.out, audit)
        elif args.command in ("predicate", "hints", "markers"):
            audit, det = load_audit(args.audit), read_json(args.det)
            if args.command == "predicate":
                candidate = Path(args.candidate).read_text(encoding="utf-8", errors="replace") if args.candidate else None
                decision = predicate(audit, det, args.major_max, candidate, read_page_texts(args.page_texts_dir))
                if audit.get("valid") is True:
                    atomic_json(args.audit, audit)
                print(decision)
                if args.with_truncation_signature:
                    print(str(truncation_signature(audit, det)).lower())
                print("; ".join(filter(None, [audit.get("reason", ""), *det.get("chunk_gate", {}).get("failed", [])])))
            elif args.command == "hints":
                atomic_text(args.out, hints(audit, det))
            else:
                print(markers(audit, det, args.limit), end="")
        elif args.command == "insert-markers":
            atomic_text(args.part, insert_markers(Path(args.part).read_text(encoding="utf-8"), Path(args.markers).read_text(encoding="utf-8")))
        elif args.command == "select":
            winner = select_attempt(read_attempts(args.attempts), args.major_max)
            if winner is None:
                return 2
            print(f"{winner['index']}\t{winner['md']}")
        elif args.command == "report":
            result, code = report(args.ledger_dir, read_json(args.settings))
            atomic_json(args.out, result)
            print(code)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"audit_tools: {one_line(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
