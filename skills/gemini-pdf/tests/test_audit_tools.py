#!/usr/bin/env python3
"""T13: audit transport, decisions, literal hints, selection, and reporting."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_tools.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("audit_tools", SCRIPT)
audit_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_tools)


def clean_audit(first=11, last=12):
    return {"schema": "gemini-pdf-audit/1",
            "pages": [{"page": page, "readable": True, "verified": True, "note": ""} for page in range(first, last + 1)],
            "seam_checked": first != 1, "findings": [], "truncated": False, "verdict": "PASS"}


def finding(severity="critical", detail="missing equation (12)"):
    return {"kind": "MISSING_TEXT", "severity": severity, "page": 11,
            "detail": detail, "anchor": "before", "pdf_text": "restored", "candidate_text": ""}


def det(passed=True, score=100):
    return {"overall_score": score, "chunk_gate": {"pass": passed, "failed": [] if passed else ["numeric"],
            "values": {"numeric": 1 if passed else 0.4, "trigram": 0.9, "completeness_ratio": 1}},
            "dimensions": {"math_quality": {"score": 100}}, "uncertain_markers": 0, "audit_markers": 0}


class AuditToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gemini-pdf-T13-")
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def parse(self, payload, nonce="n"):
        return audit_tools.parse_audit(payload, nonce, 11, 12)

    def write(self, name, value):
        path = self.root / name
        if isinstance(value, str):
            audit_tools.atomic_text(path, value)
        else:
            audit_tools.atomic_json(path, value)
        return path

    def test_transport_paths(self):
        obj = clean_audit()
        text = json.dumps(obj)
        samples = [(f"<!-- AUDIT-JSON-BEGIN n -->\n{text}\n<!-- AUDIT-JSON-END n -->", "sentinel"),
                   (text, "json"), (f"```json\n{text}\n```", "json"),
                   (json.dumps({"status": "SUCCESS", "response": text}), "response.json"),
                   (json.dumps({"status": "SUCCESS", "response": text, "structured_output": obj}), "structured_output"),
                   (json.dumps({"message": text}), "message.json"),
                   ("Here is the answer: " + text + " Thanks.", "braces.json")]
        for raw, expected in samples:
            with self.subTest(expected=expected):
                parsed = self.parse(raw)
                self.assertEqual(parsed["parse_path"], expected)
                self.assertEqual(parsed["clamped"], [])
                self.assertTrue(parsed["valid"])
                self.assertEqual(parsed["status"], "pass")

    def test_envelope_with_sentinel_response(self):
        text = f"<!-- AUDIT-JSON-BEGIN n -->\n{json.dumps(clean_audit())}\n<!-- AUDIT-JSON-END n -->"
        self.assertEqual(self.parse(json.dumps({"status": "SUCCESS", "response": text}))["parse_path"], "response.sentinel")

    def test_synthetic_envelopes_preserve_expected_shape(self):
        directory = SCRIPT.parents[1] / "tests" / "fakes" / "samples"
        for name in ("agy_json_envelope.sample.json", "agy_json_schema_envelope.sample.json"):
            envelope = json.loads((directory / name).read_text())
            envelope["response"] = json.dumps(clean_audit())
            if "structured_output" in envelope:
                envelope["structured_output"] = clean_audit()
            self.assertTrue(self.parse(json.dumps(envelope))["valid"])

    def test_denied_empty_and_error_envelopes_never_pass(self):
        for raw in ("", "   ", json.dumps({"status": "SUCCESS", "response": "", "structured_output": clean_audit()}),
                    json.dumps({"status": "SUCCESS", "response": json.dumps(clean_audit()),
                                "denied_actions": [{"action": "command", "display_name": "RunCommand"}]})):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "tool-denied"):
                    self.parse(raw)
        with self.assertRaises(ValueError):
            self.parse(json.dumps({"status": "ERROR", "response": "failed", "structured_output": clean_audit()}))

    def test_unavailable_transport_status_distinguishes_skips(self):
        for reason in ("deadline", "lock", "audit-model-unavailable", "too-large"):
            self.assertEqual(audit_tools.load_audit("UNAUDITED:" + reason)["status"], "skipped")
        for reason in ("tool-denied", "unparseable", "parse:json", "timeout", "agy-exit:1"):
            self.assertEqual(audit_tools.load_audit("UNAUDITED:" + reason)["status"], "unparseable")

    def test_wrong_duplicate_or_trailing_sentinels(self):
        text = json.dumps(clean_audit())
        for raw in (f"<!-- AUDIT-JSON-BEGIN wrong -->\n{text}\n<!-- AUDIT-JSON-END wrong -->",
                    f"<!-- AUDIT-JSON-BEGIN n -->\n<!-- AUDIT-JSON-BEGIN n -->\n{text}\n<!-- AUDIT-JSON-END n -->",
                    f"<!-- AUDIT-JSON-BEGIN n -->\n{text}\n<!-- AUDIT-JSON-END n -->\ntrailing"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.parse(raw)

    def test_schema_and_page_rejections(self):
        mutations = [lambda value: value.update(extra=True),
                     lambda value: value.update(verdict="MAYBE"),
                     lambda value: value.update(schema="wrong"),
                     lambda value: value.update(pages=list(reversed(value["pages"]))),
                     lambda value: value.update(pages=value["pages"][:1]),
                     lambda value: value["pages"][0].update(readable=False),
                     lambda value: value["pages"][0].update(verified=False),
                     lambda value: value["pages"][0].update(page=True),
                     lambda value: value.update(findings=[{**finding(), "page": 10}]),
                     lambda value: value.update(findings=[{**finding(), "kind": "UNKNOWN"}])]
        for mutation in mutations:
            value = clean_audit()
            mutation(value)
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.parse(json.dumps(value))

    def test_length_and_findings_caps_clamp_without_discarding_audit(self):
        value = clean_audit()
        value["findings"] = [finding("minor") for _ in range(70)]
        value["findings"][6]["anchor"] = "x" * 350
        parsed = self.parse(f"<!-- AUDIT-JSON-BEGIN n -->\n{json.dumps(value)}\n<!-- AUDIT-JSON-END n -->")
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["status"], "pass")
        self.assertEqual(parsed["findings"][6]["anchor"], "x" * 200 + "…")
        self.assertEqual(len(parsed["findings"]), 60)
        self.assertEqual(parsed["minor"], 60)
        self.assertTrue(parsed["truncated"])
        expected = [{"path": "findings", "from": 70, "to": 60},
                    {"path": "findings[6].anchor", "from": 350, "to": 200}]
        self.assertEqual(parsed["clamped"], expected)
        transport, clamps = parsed["parse_path"].split(";clamped=", 1)
        self.assertEqual(transport, "sentinel")
        self.assertEqual(json.loads(clamps), expected)

    def test_every_string_cap_is_tolerant_in_both_transport_modes(self):
        value = clean_audit()
        value["pages"][0]["note"] = "n" * 220
        value["findings"] = [{**finding("minor"), "anchor": "a" * 350,
                              "pdf_text": "p" * 450, "candidate_text": "c" * 450, "detail": "d" * 350}]
        expected = [{"path": "pages[0].note", "from": 220, "to": 200},
                    {"path": "findings[0].anchor", "from": 350, "to": 200},
                    {"path": "findings[0].pdf_text", "from": 450, "to": 400},
                    {"path": "findings[0].candidate_text", "from": 450, "to": 400},
                    {"path": "findings[0].detail", "from": 350, "to": 300}]
        encoded = json.dumps(value)
        transports = [(f"<!-- AUDIT-JSON-BEGIN n -->\n{encoded}\n<!-- AUDIT-JSON-END n -->", "sentinel"),
                      (encoded, "json"),
                      (json.dumps({"structured_output": value}), "structured_output"),
                      (json.dumps({"structured_output": encoded}), "structured_output.json")]
        for raw, transport in transports:
            with self.subTest(transport=transport):
                parsed = self.parse(raw)
                self.assertTrue(parsed["valid"])
                self.assertTrue(parsed["truncated"])
                self.assertEqual(parsed["pages"][0]["note"], "n" * 200 + "…")
                for field, letter, cap in (("anchor", "a", 200), ("pdf_text", "p", 400),
                                           ("candidate_text", "c", 400), ("detail", "d", 300)):
                    self.assertEqual(parsed["findings"][0][field], letter * cap + "…")
                self.assertEqual(parsed["clamped"], expected)
                self.assertTrue(parsed["parse_path"].startswith(transport + ";clamped="))
                self.assertEqual(json.loads(parsed["parse_path"].split(";clamped=", 1)[1]), expected)
        value = clean_audit()
        value["truncated"] = True
        parsed = self.parse(json.dumps(value))
        self.assertTrue(parsed["truncated"])
        self.assertEqual(parsed["clamped"], [])

    def test_caps_do_not_hide_incomplete_pages_or_unknown_enums(self):
        value = clean_audit()
        value["findings"] = [finding(detail="x" * 350)]
        value["pages"].pop()
        with self.assertRaisesRegex(ValueError, "^pages_incomplete$"):
            self.parse(json.dumps(value))
        value["pages"] = []
        with self.assertRaisesRegex(ValueError, "^pages_incomplete$"):
            self.parse(json.dumps(value))
        value = clean_audit()
        value["findings"] = [{**finding(), "anchor": "x" * 350, "kind": "UNKNOWN"}]
        with self.assertRaisesRegex(ValueError, "^schema:audit.findings\\[0\\].kind:enum$"):
            self.parse(json.dumps(value))

    def test_dropped_findings_still_require_valid_structure_and_page(self):
        for bad, reason in (({**finding(), "kind": "UNKNOWN"}, "enum"),
                            ({**finding(), "detail": 3}, "type"),
                            ({"kind": "MISSING_TEXT"}, "required"),
                            ({**finding(), "page": 13}, "finding-page-out-of-range")):
            with self.subTest(reason=reason):
                value = clean_audit()
                value["findings"] = [finding("minor") for _ in range(69)] + [bad]
                with self.assertRaisesRegex(ValueError, reason):
                    self.parse(json.dumps(value))

    def test_duplicate_keys_and_nonfinite_rejected(self):
        text = json.dumps(clean_audit())
        for raw in (text.replace('"verdict": "PASS"', '"verdict": "PASS", "verdict": "FAIL"'),
                    text.replace('"seam_checked": true', '"seam_checked": NaN')):
            with self.assertRaises(ValueError):
                self.parse(raw)

    def test_local_validator_agrees_with_schema(self):
        import jsonschema
        schema = audit_tools.read_json(audit_tools.SCHEMA_PATH)
        valid = clean_audit()
        audit_tools.validate_schema(valid, schema)
        jsonschema.Draft202012Validator(schema).validate(valid)
        valid["pages"][0]["page"] = True
        with self.assertRaises(ValueError):
            audit_tools.validate_schema(valid, schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(schema).validate(valid)
        for mutation in (lambda value: value["pages"][0].update(note="x" * 201),
                         lambda value: value.update(findings=[finding(detail="x" * 301)]),
                         lambda value: value.update(findings=[finding()] * 61)):
            value = clean_audit()
            mutation(value)
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    audit_tools.validate_schema(value, schema)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.Draft202012Validator(schema).validate(value)
                self.assertTrue(self.parse(json.dumps(value))["valid"])

    def test_truncation_signature_boundaries(self):
        from audit_report import truncation_signature
        audit = self.parse(json.dumps(clean_audit()))
        score = det()
        self.assertFalse(truncation_signature(audit, score))
        for ratio, expected in ((0.6, False), (0.599, True)):
            score["chunk_gate"]["values"]["completeness_ratio"] = ratio
            self.assertEqual(truncation_signature(audit, score), expected)
        score = det()
        for count, expected in ((4, False), (5, True)):
            score["chunk_gate"]["values"]["missing_sentences"] = count
            self.assertEqual(truncation_signature(audit, score), expected)
        for count, expected in ((2, False), (3, True)):
            value = clean_audit()
            value["findings"] = [finding() for _ in range(count)]
            self.assertEqual(truncation_signature(self.parse(json.dumps(value)), det()), expected)
        audit["findings"] = [finding() for _ in range(3)]
        audit["valid"] = False
        self.assertFalse(truncation_signature(audit, det()))
        audit["valid"] = True
        for findings in ([{**finding(), "kind": "EQUATION_ERROR"}] * 3,
                         [finding("major")] * 3):
            audit["findings"] = findings
            self.assertFalse(truncation_signature(audit, det()))

    def test_predicate_uses_evidence_and_and_gate(self):
        clean = self.parse(json.dumps(clean_audit()))
        self.assertEqual(audit_tools.predicate(clean, det()), "PASS")
        self.assertEqual(audit_tools.predicate(clean, det(False)), "REPAIR")
        self.assertEqual(audit_tools.predicate(audit_tools.unavailable("deadline"), det()), "UNAUDITED_OK")
        self.assertEqual(audit_tools.predicate(audit_tools.unavailable("deadline"), det(False)), "UNAUDITED_DETFAIL")
        value = clean_audit()
        value["verdict"] = "FAIL"
        parsed = self.parse(json.dumps(value))
        self.assertTrue(parsed["verdict_mismatch"])
        self.assertEqual(audit_tools.predicate(parsed, det()), "PASS")
        value["verdict"] = "PASS"
        value["findings"] = [finding()]
        parsed = self.parse(json.dumps(value))
        self.assertTrue(parsed["verdict_mismatch"])
        self.assertEqual(audit_tools.predicate(parsed, det()), "REPAIR")

    def test_major_threshold(self):
        value = clean_audit()
        value["findings"] = [finding("major")] * 2
        self.assertEqual(audit_tools.predicate(self.parse(json.dumps(value)), det()), "PASS")
        value["findings"].append(finding("major"))
        self.assertEqual(audit_tools.predicate(self.parse(json.dumps(value)), det()), "REPAIR")

    def test_hints_keep_literal_text_and_missing_sentence_heads(self):
        literal = r'& \beta `touch harmless` $(printf nope) %%PDF_NAME%%'
        value = clean_audit()
        value["findings"] = [finding("minor", "format"), finding("critical", literal)]
        score = det(False)
        score.update(page_checks={"11": {"trigrams": 50, "trigram_recall": 0.1,
                                         "missing_sentences": {"count": 2, "heads": ["The complete missing sentence has enough words to be detected"]},
                                         "missing_numerals": ["0.019"]}},
                     injected_dois=1, injected_doi_list=["10.1234/fabricated"])
        output = audit_tools.hints(self.parse(json.dumps(value)), score)
        self.assertIn(literal, output)
        self.assertLess(output.index(literal), output.index("format"))
        self.assertIn('[page 11] MISSING_TEXT (deterministic): PDF sentence absent: "The complete missing sentence', output)
        self.assertIn("EXTRA_TEXT (deterministic): DOI not found in the PDF text layer: 10.1234/fabricated — verify against the PDF; delete it ONLY if the PDF does not print it", output)
        self.assertIn("0.019", output)
        self.assertLessEqual(len(output.splitlines()), 25)

    def test_dense_page_omission_crosscheck_boundaries_and_protected_kinds(self):
        cases = [
            ({}, "MISSING_TEXT", "entire page 11 is missing", True),
            ({"trigrams": 39}, "MISSING_TEXT", "entire page 11 is missing", False),
            ({"trigram_recall": 0.3999}, "MISSING_TEXT", "whole page is omitted", False),
            ({"missing_sentences": {"count": 1}}, "MISSING_TEXT", "candidate abruptly truncates", False),
            ({"missing_sentences": {}}, "MISSING_TEXT", "candidate abruptly cuts off", False),
            ({}, "SEAM_GAP", "section continuation absent", True),
            ({}, "TABLE_ERROR", "whole page is missing", True),
            ({}, "TABLE_ERROR", "wrong table cells", False),
            ({}, "MISSING_TEXT", "a word differs", False),
            ({}, "NUMBER_ERROR", "entire page is missing", False),
            ({}, "EQUATION_ERROR", "entire page is missing", False),
            ({}, "STRUCTURE", "entire page is missing", False),
        ]
        for overrides, kind, detail, expected in cases:
            with self.subTest(overrides=overrides, kind=kind, detail=detail):
                raw = clean_audit()
                raw.update(verdict="FAIL", findings=[{**finding(detail=detail), "kind": kind}])
                audit = self.parse(json.dumps(raw))
                score = det()
                score["page_checks"] = {"11": {"trigrams": 40, "trigram_recall": 0.40,
                    "numeric_recall": 1, "missing_sentences": {"count": 0}, **overrides}}
                self.assertEqual(audit_tools.predicate(audit, score), "PASS" if expected else "REPAIR")
                self.assertEqual(audit["contradicted"], int(expected))
                self.assertEqual(audit["critical"], 0 if expected else 1)
                self.assertEqual(audit["status"], "pass" if expected else "fail")
                self.assertEqual(audit["findings"][0].get("contradicted", False), expected)
                self.assertEqual(audit["findings"][0]["detail"], detail)
                if expected:
                    self.assertIn("page_checks[11]", audit["findings"][0]["contradicted_by"])
                score["chunk_gate"]["pass"] = False
                self.assertEqual(audit_tools.predicate(audit, score), "REPAIR")

    def test_omission_in_pdf_text_and_no_page_evidence(self):
        raw = clean_audit()
        raw["findings"] = [{**finding(detail="see source evidence"), "pdf_text": "paragraph omitted"}]
        audit = self.parse(json.dumps(raw))
        score = det()
        self.assertEqual(audit_tools.predicate(audit, score), "REPAIR")
        score["page_checks"] = {"11": {"trigrams": 70, "trigram_recall": 0.75,
                                      "missing_sentences": {"count": 0}}}
        self.assertEqual(audit_tools.predicate(audit, score), "PASS")

    def test_sparse_page_requires_numeric_evidence_and_all_printed_tags(self):
        raw = clean_audit()
        raw["findings"] = [finding(detail="entire page 11 is missing")]
        score = det()
        score["page_checks"] = {"11": {"trigrams": 39, "numeric_recall": 0.90}}
        source = {11: "(12)\nx + y = 1   (S.3)\n" + r"a = b \tag{14}"}
        complete = r"x+y=1 \tag{12} z=a \tag{S.3} a=b \tag{14}"
        cases = [(complete, source, True), (complete, None, False), (None, source, False),
                 (complete, {}, False), (r"x+y=1 \tag{12}\tag{14}", source, False),
                 ("(12)\nx+y=1 (S.3)\n(14)\n", source, True),
                 ("plain text", {11: "no numbered equations"}, True)]
        for candidate, pages, expected in cases:
            with self.subTest(candidate=candidate, pages=pages):
                audit = self.parse(json.dumps(raw))
                self.assertEqual(audit_tools.predicate(audit, score, candidate_text=candidate, page_texts=pages),
                                 "PASS" if expected else "REPAIR")
                self.assertEqual(audit["contradicted"], int(expected))
        score["page_checks"]["11"]["numeric_recall"] = 0.8999
        self.assertEqual(audit_tools.predicate(self.parse(json.dumps(raw)), score,
                                             candidate_text=complete, page_texts=source), "REPAIR")

    def test_contradictions_excluded_from_hints_markers_and_truncation(self):
        raw = clean_audit()
        raw.update(verdict="FAIL", findings=[finding(detail="entire page 11 is missing") for _ in range(3)])
        audit = self.parse(json.dumps(raw))
        score = det()
        score["page_checks"] = {"11": {"trigrams": 50, "trigram_recall": 0.5,
                                      "missing_sentences": {"count": 0}}}
        self.assertTrue(audit_tools.truncation_signature(audit, score))
        self.assertEqual(audit_tools.predicate(audit, score), "PASS")
        self.assertEqual(audit["contradicted"], 3)
        self.assertEqual(audit_tools.hints(audit, score), "")
        self.assertEqual(audit_tools.markers(audit, score), "")
        self.assertFalse(audit_tools.truncation_signature(audit, score))
        # Crosschecking new evidence cannot retain a stale contradiction.
        score["page_checks"]["11"]["trigram_recall"] = 0
        self.assertEqual(audit_tools.predicate(audit, score), "REPAIR")
        self.assertEqual(audit["contradicted"], 0)

    def test_model_cannot_mark_its_own_finding_contradicted(self):
        raw = clean_audit()
        raw["findings"] = [{**finding(), "contradicted": True, "contradicted_by": "trust me"}]
        audit = self.parse(json.dumps(raw))
        self.assertNotIn("contradicted", audit["findings"][0])
        self.assertEqual(audit_tools.predicate(audit, det()), "REPAIR")

    def test_markers_cannot_escape_comments_and_follow_frontmatter(self):
        value = clean_audit()
        value["findings"] = [finding(detail="wrong --> <script>payload</script>\nnext line")] * 12
        output = audit_tools.markers(self.parse(json.dumps(value)), det(), 100)
        self.assertEqual(len(output.splitlines()), 10)
        self.assertEqual(output.count("-->"), 10)
        self.assertNotIn("<script>", output)
        markdown = "---\ntitle: A\n---\n# A\nbody\n"
        marked = audit_tools.insert_markers(markdown, output)
        self.assertTrue(marked.startswith("---\ntitle: A\n---\n<!-- AUDIT:"))
        self.assertIn("# A\nbody\n", marked)

    def attempt(self, index, kind="pro", passed=True, findings=None, score=100, unavailable=False):
        markdown = self.write(f"part-{index}.md", f"content {index}\n")
        result = clean_audit()
        result["findings"] = findings or []
        audited = audit_tools.unavailable("tool-denied", "unparseable") if unavailable else self.parse(json.dumps(result))
        return {"index": index, "kind": kind, "md": str(markdown), "det": det(passed, score), "audit": audited}

    def test_selection_ranks_evidence_and_preserves_ties(self):
        native = self.attempt(0)
        repaired = self.attempt(1, "pro-repair")
        self.assertIs(audit_tools.select_attempt([native, repaired]), native)
        failing = self.attempt(2, findings=[finding()], score=100)
        unaudited = self.attempt(3, unavailable=True, score=60)
        self.assertIs(audit_tools.select_attempt([failing, unaudited]), unaudited)
        broken = self.attempt(4, score=100)
        broken["md"] = ""
        self.assertIs(audit_tools.select_attempt([broken, native]), native)
        broken["md"] = str(self.write("blank.md", " \n\t"))
        self.assertIs(audit_tools.select_attempt([broken, native]), native)
        low = self.attempt(5, passed=False, findings=[finding("critical")], score=99)
        high = self.attempt(6, passed=False, findings=[finding("major")], score=10)
        self.assertIs(audit_tools.select_attempt([low, high]), high)

    def report_fixture(self, status="verified", strict=False):
        chunk = self.root / "chunk_11_12"
        chunk.mkdir(exist_ok=True)
        attempt = self.attempt(0)
        self.write("det.json", attempt["det"])
        self.write("audit.json", attempt["audit"])
        (chunk / "attempts.tsv").write_text(f"0\tpro\t{attempt['md']}\t{self.root / 'det.json'}\t{self.root / 'audit.json'}\n")
        audit_tools.atomic_json(chunk / "state.json", {"id": 1, "first_page": 11, "last_page": 12,
            "status": status, "selected": 0, "final_source": "pro", "audit_attempts": 2,
            "reason": "mathpix-cap" if status == "unverified" else ""})
        settings = {"run_nonce": "run1", "models": {"convert": "pro", "audit": "flash", "identity_check": {"convert": "yes", "audit": "yes"}},
                    "input": {"pdf": "paper.pdf", "pages": 2, "sha256": "hash", "text_digest": "digest"},
                    "strict": strict, "counts": {"convert_calls": 1, "audit_calls": 2, "repair_calls": 1},
                    "deadline": {"seconds": 30, "reserve": 1, "hit": False}, "elapsed_s": 7}
        return settings

    def test_report_status_precedence_strict_and_invariant(self):
        for status, document, strict_code in (("verified", "verified", 0), ("unaudited", "degraded", 3),
                                               ("unverified", "partial", 4), ("failed", "failed", 2)):
            with self.subTest(status=status):
                settings = self.report_fixture(status, True)
                report, code = audit_tools.report(self.root, settings)
                self.assertEqual(report["status"], document)
                self.assertEqual(code, strict_code)
                self.assertEqual(sum(report["totals"][key] for key in ("verified", "unaudited", "unverified", "failed")), report["totals"]["chunks"])
                self.assertEqual(report["chunks"][0]["audit"]["attempts"], 2)
                self.assertIn(f"AUDIT: status={document} chunks=1", report["summary_line"])
                self.assertIn("external=agy:4/mathpix:0", report["summary_line"])
        settings["audit_enabled"] = False
        report, code = audit_tools.report(self.root, settings)
        self.assertEqual(report["status"], "disabled")
        self.assertEqual(code, 0)

    def test_report_skipped_and_failed_attempt(self):
        settings = self.report_fixture()
        chunk = self.root / "chunk_11_12"
        with (chunk / "attempts.tsv").open("a") as handle:
            handle.write("1\tpro-repair\t\tmissing-det\tUNAUDITED:tool-denied\n")
        self.write("skipped.tsv", "1\taudit\tdeadline\t11-12\n")
        report, code = audit_tools.report(self.root, settings)
        self.assertEqual(report["status"], "degraded")
        self.assertTrue(report["deadline"]["hit"])
        self.assertEqual(len(report["chunks"][0]["attempts"]), 2)
        self.assertTrue(report["chunks"][0]["attempts"][0]["selected"])
        self.assertFalse(report["chunks"][0]["attempts"][1]["selected"])
        self.assertIn("skipped=audit:11-12", report["summary_line"])
        self.assertIn("deadline:audit", report["chunks"][0]["degradation"])

    def test_reports_preserve_bisected_ids_skips_and_contradiction_count(self):
        from audit_report import recover
        settings = self.report_fixture()
        for ident, first, last in (("2.1", 13, 17), ("2.2", 18, 22), (3, 23, 25)):
            chunk = self.root / f"chunk_{first}_{last}"
            chunk.mkdir()
            state = {"id": ident, "first_page": first, "last_page": last, "status": "unverified",
                     "selected": 0, "final_source": "pro", "bisected": isinstance(ident, str)}
            audit_tools.atomic_json(chunk / "state.json", state)
            audit = self.parse(json.dumps({**clean_audit(), "findings": [finding(detail="entire page 11 is missing")]}))
            score = det()
            score["page_checks"] = {"11": {"trigrams": 40, "trigram_recall": 0.4,
                                          "missing_sentences": {"count": 0}}}
            audit_tools.predicate(audit, score)
            audit_tools.atomic_json(chunk / "audit.json", audit)
            audit_tools.atomic_json(chunk / "det.json", score)
            (chunk / "part.md").write_text("complete body", encoding="utf-8")
            (chunk / "attempts.tsv").write_text(f"0\tpro\t{chunk / 'part.md'}\t{chunk / 'det.json'}\t{chunk / 'audit.json'}\n")
        self.write("skipped.tsv", "2.2\taudit\tdeadline\t18-22\n")
        normal, _ = audit_tools.report(self.root, settings)
        emergency, _ = recover(self.root, settings)
        self.assertEqual(normal["chunks"], emergency["chunks"])
        self.assertEqual([chunk["id"] for chunk in normal["chunks"]], [1, "2.1", "2.2", 3])
        self.assertEqual(normal["totals"]["chunks"], 4)
        self.assertTrue(normal["chunks"][1]["bisected"])
        self.assertTrue(normal["chunks"][2]["bisected"])
        self.assertNotIn("bisected", normal["chunks"][3])
        self.assertEqual(normal["skipped_steps"], emergency["skipped_steps"])
        self.assertIn("deadline:audit", normal["chunks"][2]["degradation"])
        self.assertEqual(normal["chunks"][1]["residual"], [])
        self.assertIn("residual=3 contradicted=3 ", normal["summary_line"])

    def test_recovery_keeps_chunk_evidence_and_status_precedence(self):
        from audit_report import recover
        for status, document, strict_code in (("verified", "degraded", 3), ("unaudited", "degraded", 3),
                                               ("unverified", "partial", 4), ("failed", "failed", 2)):
            with self.subTest(status=status):
                settings = self.report_fixture(status, True)
                normal, _ = audit_tools.report(self.root, settings)
                emergency, code = recover(self.root, settings)
                self.assertEqual(emergency["chunks"], normal["chunks"])
                self.assertEqual(emergency["chunks"][0]["attempts"][0]["truncation_signature"],
                                 normal["chunks"][0]["attempts"][0]["truncation_signature"])
                self.assertEqual(emergency["totals"], normal["totals"])
                self.assertEqual(emergency["status"], document)
                self.assertEqual(code, strict_code)
                self.assertIn("report-error", emergency["degradations"])
                self.assertNotIn("aborted", emergency)
                self.assertIn(f"AUDIT: status={document} chunks=1", emergency["summary_line"])

    def test_recovery_without_states_is_zero_chunk_aborted(self):
        from audit_report import recover
        result, code = recover(None, {"input": {"pages": 25}})
        self.assertEqual(code, 2)
        self.assertTrue(result["aborted"])
        self.assertEqual(result["chunks"], [])
        self.assertTrue(all(result["totals"][key] == 0 for key in
                            ("chunks", "verified", "unaudited", "unverified", "failed")))
        self.assertIn("chunks=0 verified=0 unaudited=0 unverified=0 failed=0", result["summary_line"])

    def test_recovery_survives_malformed_attempt_row_with_real_state(self):
        from audit_report import recover
        settings = self.report_fixture()
        with (self.root / "chunk_11_12/attempts.tsv").open("a") as handle:
            handle.write("interrupted\trow\n")
        with self.assertRaises(ValueError):
            audit_tools.report(self.root, settings)
        result, code = recover(self.root, settings)
        self.assertEqual((result["status"], code), ("degraded", 0))
        self.assertEqual(result["totals"]["chunks"], 1)
        self.assertEqual(result["chunks"][0]["status"], "verified")
        self.assertTrue(result["chunks"][0]["attempts"][0]["selected"])

    def test_cli_invalid_parse_writes_classified_file(self):
        raw = self.write("raw.txt", "")
        output = self.root / "parsed.json"
        process = subprocess.run([sys.executable, str(SCRIPT), "parse", str(raw), "--nonce", "n",
                                  "--first", "11", "--last", "12", "--out", str(output)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(output.read_text())["reason"], "tool-denied")
        self.assertNotIn("PASS", process.stdout)

    def test_cli_predicate_persists_sparse_contradiction_for_report_and_selection(self):
        settings = self.report_fixture()
        raw = clean_audit()
        raw.update(verdict="FAIL", findings=[finding(detail="entire page 11 is missing")])
        audit = self.write("audit.json", self.parse(json.dumps(raw)))
        score = det()
        score["page_checks"] = {"11": {"trigrams": 10, "numeric_recall": 1}}
        det_path = self.write("det.json", score)
        candidate = self.write("part-0.md", r"x = y \tag{12}")
        pages = self.root / "page_texts"
        pages.mkdir()
        (pages / "page_11.txt").write_text("(12)\nx = y\n")
        process = subprocess.run([sys.executable, str(SCRIPT), "predicate", "--audit", str(audit),
                                  "--det", str(det_path), "--candidate", str(candidate), "--page-texts-dir", str(pages),
                                  "--with-truncation-signature"], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.splitlines()[:2], ["PASS", "false"])
        stored = json.loads(audit.read_text())
        self.assertEqual(stored["contradicted"], 1)
        self.assertTrue(stored["findings"][0]["contradicted"])
        attempts = audit_tools.read_attempts(self.root / "chunk_11_12/attempts.tsv")
        self.assertEqual(audit_tools.selection_key(attempts[0])[0], 3)
        self.assertEqual(attempts[0]["audit"]["contradicted"], 1)
        report, _ = audit_tools.report(self.root, settings)
        self.assertEqual(report["chunks"][0]["audit"]["contradicted"], 1)
        self.assertIn("residual=0 contradicted=1 ", report["summary_line"])

    def test_cli_literal_hint_round_trip_and_report(self):
        settings = self.report_fixture()
        self.write("settings.json", settings)
        result = subprocess.run([sys.executable, str(SCRIPT), "report", "--ledger-dir", str(self.root),
                                 "--settings", str(self.root / "settings.json"), "--out", str(self.root / "out.audit.json")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0\n")
        self.assertEqual(json.loads((self.root / "out.audit.json").read_text())["schema"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
