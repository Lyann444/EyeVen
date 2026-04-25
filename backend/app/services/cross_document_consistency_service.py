import logging
import re

from app.extensions import db
from app.models import EyeVenFormFieldValue, UploadedFile, VendorCase

logger = logging.getLogger(__name__)

CROSS_CHECK_FIELDS = {
    "company_registered_name": {
        "label": "Company Name",
        "category": "company_identity",
        "comparison_type": "name",
    },
    "ssm_roc_rob_number": {
        "label": "SSM/ROC/ROB Number",
        "category": "company_identity",
        "comparison_type": "registration_number",
    },
    "company_registered_number": {
        "label": "Company Registration Number",
        "category": "company_identity",
        "comparison_type": "registration_number",
    },
    "company_registered_address": {
        "label": "Registered Address",
        "category": "company_identity",
        "comparison_type": "address",
    },
    "business_type": {
        "label": "Business Type",
        "category": "business_nature",
        "comparison_type": "business_type",
    },
    "main_business_activities": {
        "label": "Main Business Activities",
        "category": "business_nature",
        "comparison_type": "business_activities",
    },
    "iso_certification_number": {
        "label": "ISO Certificate Number",
        "category": "certification",
        "comparison_type": "identifier",
    },
    "bank_account_name": {
        "label": "Bank Account Name",
        "category": "financial",
        "comparison_type": "name",
    },
    "bank_account_number": {
        "label": "Bank Account Number",
        "category": "financial",
        "comparison_type": "identifier",
    },
    "contact_person_name": {
        "label": "Contact Person Name",
        "category": "contact_signatory",
        "comparison_type": "person_name",
    },
    "signatory_name": {
        "label": "Signatory Name",
        "category": "contact_signatory",
        "comparison_type": "person_name",
    },
}

BUSINESS_TYPE_ALIASES = {
    "SDN BHD": "SDN BHD",
    "SDN. BHD.": "SDN BHD",
    "SDN.BHD.": "SDN BHD",
    "SENDIRIAN BERHAD": "SDN BHD",
    "BHD": "BHD",
    "BERHAD": "BHD",
    "ENTERPRISE": "ENTERPRISE",
    "ENTERPRISE (EM)": "ENTERPRISE",
}

SEVERITY_ORDER = {"match": 0, "low_risk": 1, "severe": 2}


def _levenshtein(a, b):
    if len(a) < len(b):
        return _levenshtein(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def _normalize_name(val):
    s = str(val or "").upper().strip()

    # Normalize Malaysian company suffix formats first
    s = re.sub(r"\bSDN\s*\.?\s*BHD\s*\.?\b", "SDN BHD", s)
    s = re.sub(r"\bSENDIRIAN\s+BERHAD\b", "SDN BHD", s)
    s = re.sub(r"\bBERHAD\b", "BHD", s)

    # Remove punctuation after suffix normalization
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Remove company suffix for comparison
    for suffix in ("SDN BHD", "BHD"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break

    return s


def _normalize_reg_number(val):
    return re.sub(r"[^A-Za-z0-9]", "", val).upper()


def _normalize_address(val):
    s = val.upper().strip()
    s = re.sub(r"[,.\-/#]", " ", s)
    s = re.sub(r"\s+", " ", s)
    tokens = sorted(s.split())
    return tokens


def _normalize_business_type(val):
    s = val.upper().strip()
    s = re.sub(r"\s+", " ", s)
    return BUSINESS_TYPE_ALIASES.get(s, s)


def _normalize_identifier(val):
    return re.sub(r"[\s\-/]", "", val).upper()


def _normalize_person_name(val):
    s = val.upper().strip()
    s = re.sub(r"\s+", " ", s)
    return set(s.split())


def _normalize_business_activities(val):
    s = val.upper().strip()
    s = re.sub(r"\s+", " ", s)
    return set(s.split())


def _compare_name(a, b):
    na, nb = _normalize_name(a), _normalize_name(b)
    if na == nb:
        return "match", na, nb
    a_tokens, b_tokens = na.split(), nb.split()
    overlap = set(a_tokens) & set(b_tokens)
    max_len = max(len(a_tokens), len(b_tokens))
    if max_len == 0:
        return "match", na, nb
    ratio = len(overlap) / max_len
    if ratio >= 0.5:
        return "low_risk", na, nb
    return "severe", na, nb


def _compare_registration_number(a, b):
    na, nb = _normalize_reg_number(a), _normalize_reg_number(b)
    if na == nb:
        return "match", na, nb
    if not na or not nb:
        return "severe", na, nb
    if nb in na or na in nb:
        return "low_risk", na, nb
    dist = _levenshtein(na, nb)
    if dist <= 2:
        return "low_risk", na, nb
    return "severe", na, nb


def _compare_address(a, b):
    na, nb = _normalize_address(a), _normalize_address(b)
    if na == nb:
        return "match", str(na), str(nb)
    if not na or not nb:
        return "severe", str(na), str(nb)
    overlap = set(na) & set(nb)
    max_len = max(len(na), len(nb))
    ratio = len(overlap) / max_len
    if ratio >= 0.6:
        return "low_risk", str(na), str(nb)
    return "severe", str(na), str(nb)


def _compare_business_type(a, b):
    na, nb = _normalize_business_type(a), _normalize_business_type(b)
    if na == nb:
        return "match", na, nb
    if na in nb or nb in na:
        return "low_risk", na, nb
    return "severe", na, nb


def _compare_business_activities(a, b):
    na, nb = _normalize_business_activities(a), _normalize_business_activities(b)
    if na == nb:
        return "match", str(na), str(nb)
    if not na or not nb:
        return "severe", str(na), str(nb)
    overlap = na & nb
    max_len = max(len(na), len(nb))
    ratio = len(overlap) / max_len
    if ratio >= 0.5:
        return "low_risk", str(na), str(nb)
    return "severe", str(na), str(nb)


def _compare_identifier(a, b):
    na, nb = _normalize_identifier(a), _normalize_identifier(b)
    if na == nb:
        return "match", na, nb
    if not na or not nb:
        return "severe", na, nb
    dist = _levenshtein(na, nb)
    if dist <= 2:
        return "low_risk", na, nb
    return "severe", na, nb


def _compare_person_name(a, b):
    na, nb = _normalize_person_name(a), _normalize_person_name(b)
    if na == nb:
        return "match", str(na), str(nb)
    if not na or not nb:
        return "severe", str(na), str(nb)
    overlap = na & nb
    max_len = max(len(na), len(nb))
    ratio = len(overlap) / max_len
    if ratio >= 0.5:
        return "low_risk", str(na), str(nb)
    return "severe", str(na), str(nb)


COMPARATORS = {
    "name": _compare_name,
    "registration_number": _compare_registration_number,
    "address": _compare_address,
    "business_type": _compare_business_type,
    "business_activities": _compare_business_activities,
    "identifier": _compare_identifier,
    "person_name": _compare_person_name,
}


class CrossDocumentConsistencyService:
    @staticmethod
    def check_consistency(case_id):
        case = VendorCase.query.get_or_404(case_id)
        files = UploadedFile.query.filter_by(vendor_case_id=case_id).all()

        procurement_form = next(
            (f for f in files if f.source_category == "procurement_form"), None
        )
        supporting_docs = [f for f in files if f.source_category == "supporting_document"]

        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()

        if procurement_form:
            mode = "form_vs_evidence"
            result = CrossDocumentConsistencyService._compare_form_vs_evidence(
                case_id, procurement_form, supporting_docs, fields
            )
        else:
            mode = "pairwise"
            result = CrossDocumentConsistencyService._compare_pairwise(
                case_id, supporting_docs, fields
            )

        result["mode"] = mode
        result["procurement_form_id"] = procurement_form.id if procurement_form else None
        result["supporting_document_ids"] = [f.id for f in supporting_docs]

        summary = {"match": 0, "low_risk": 0, "severe": 0, "insufficient_data": 0}
        for fr in result["field_results"]:
            summary[fr["verdict"]] += 1
        for _ in result["insufficient_data_fields"]:
            summary["insufficient_data"] += 1
        result["summary"] = summary
        result["fields_checked"] = len(result["field_results"])

        return result

    @staticmethod
    def _get_document_values(field):
        if field.document_values and isinstance(field.document_values, dict):
            return field.document_values
        if field.source_file_id and field.value_text:
            return {str(field.source_file_id): field.value_text}
        return {}

    @staticmethod
    def _compare_form_vs_evidence(case_id, procurement_form, supporting_docs, fields):
        form_id = str(procurement_form.id)
        field_results = []
        insufficient_data_fields = []

        for field_name, config in CROSS_CHECK_FIELDS.items():
            field = next((f for f in fields if f.field_name == field_name), None)
            if not field:
                insufficient_data_fields.append({
                    "field_name": field_name,
                    "field_label": config["label"],
                    "reason": "Field not found in case",
                })
                continue

            doc_vals = CrossDocumentConsistencyService._get_document_values(field)
            form_value = doc_vals.get(form_id)

            if form_value is None and field.value_source == "procurement_form":
                form_value = field.value_text

            evidence_values = {}
            for sd in supporting_docs:
                sd_id = str(sd.id)
                val = doc_vals.get(sd_id)
                if val is not None:
                    evidence_values[sd_id] = val

            if form_value is None and not evidence_values:
                insufficient_data_fields.append({
                    "field_name": field_name,
                    "field_label": config["label"],
                    "reason": "No values found in any document",
                })
                continue

            if form_value is None or not evidence_values:
                insufficient_data_fields.append({
                    "field_name": field_name,
                    "field_label": config["label"],
                    "reason": "Value present in only 1 document; cannot compare",
                })
                continue

            comparator = COMPARATORS[config["comparison_type"]]
            worst_verdict = "match"
            comparisons = []

            for sd_id, ev_val in evidence_values.items():
                verdict, norm_a, norm_b = comparator(form_value, ev_val)
                if SEVERITY_ORDER[verdict] > SEVERITY_ORDER[worst_verdict]:
                    worst_verdict = verdict
                comparisons.append({
                    "source_a": {
                        "file_id": int(form_id),
                        "source_category": "procurement_form",
                        "value": form_value,
                    },
                    "source_b": {
                        "file_id": int(sd_id),
                        "source_category": "supporting_document",
                        "value": ev_val,
                    },
                    "normalized_a": norm_a,
                    "normalized_b": norm_b,
                    "detail": CrossDocumentConsistencyService._verdict_detail(
                        verdict, config["comparison_type"]
                    ),
                })

            field_results.append({
                "field_name": field_name,
                "field_label": config["label"],
                "verdict": worst_verdict,
                "comparisons": comparisons,
            })

        return {
            "field_results": field_results,
            "insufficient_data_fields": insufficient_data_fields,
        }

    @staticmethod
    def _compare_pairwise(case_id, supporting_docs, fields):
        field_results = []
        insufficient_data_fields = []

        for field_name, config in CROSS_CHECK_FIELDS.items():
            field = next((f for f in fields if f.field_name == field_name), None)
            if not field:
                insufficient_data_fields.append({
                    "field_name": field_name,
                    "field_label": config["label"],
                    "reason": "Field not found in case",
                })
                continue

            doc_vals = CrossDocumentConsistencyService._get_document_values(field)

            present = {}
            for sd in supporting_docs:
                sd_id = str(sd.id)
                val = doc_vals.get(sd_id)
                if val is not None:
                    present[sd_id] = val

            if len(present) < 2:
                insufficient_data_fields.append({
                    "field_name": field_name,
                    "field_label": config["label"],
                    "reason": f"Value present in only {len(present)} document(s); need at least 2 to compare",
                })
                continue

            comparator = COMPARATORS[config["comparison_type"]]
            worst_verdict = "match"
            comparisons = []
            ids = list(present.keys())

            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    id_a, id_b = ids[i], ids[j]
                    verdict, norm_a, norm_b = comparator(present[id_a], present[id_b])
                    if SEVERITY_ORDER[verdict] > SEVERITY_ORDER[worst_verdict]:
                        worst_verdict = verdict
                    comparisons.append({
                        "source_a": {
                            "file_id": int(id_a),
                            "source_category": "supporting_document",
                            "value": present[id_a],
                        },
                        "source_b": {
                            "file_id": int(id_b),
                            "source_category": "supporting_document",
                            "value": present[id_b],
                        },
                        "normalized_a": norm_a,
                        "normalized_b": norm_b,
                        "detail": CrossDocumentConsistencyService._verdict_detail(
                            verdict, config["comparison_type"]
                        ),
                    })

            field_results.append({
                "field_name": field_name,
                "field_label": config["label"],
                "verdict": worst_verdict,
                "comparisons": comparisons,
            })

        return {
            "field_results": field_results,
            "insufficient_data_fields": insufficient_data_fields,
        }

    @staticmethod
    def _verdict_detail(verdict, comparison_type):
        if verdict == "match":
            return "Values are consistent across documents"
        if verdict == "low_risk":
            if comparison_type in ("registration_number", "identifier"):
                return "Minor difference, likely OCR or transcription error"
            if comparison_type == "address":
                return "Address differs slightly, possible omission or formatting"
            if comparison_type in ("name", "person_name"):
                return "Name differs slightly, possible abbreviation or partial name"
            if comparison_type == "business_type":
                return "Business type differs by alias or abbreviation"
            if comparison_type == "business_activities":
                return "Business activities overlap but wording differs"
            return "Minor difference detected"
        return "Values are substantively different across documents"

    @staticmethod
    def update_field_statuses(case_id):
        report = CrossDocumentConsistencyService.check_consistency(case_id)

        status_precedence = {
            "conflicting": 5,
            "unverifiable": 4,
            "mismatched_with_evidence": 3,
            "declaration_missing": 2,
            "low_confidence": 1,
        }

        for fr in report["field_results"]:
            field = EyeVenFormFieldValue.query.filter_by(
                vendor_case_id=case_id, field_name=fr["field_name"]
            ).first()
            if not field:
                continue

            verdict = fr["verdict"]
            current = status_precedence.get(field.status, 0)

            if verdict == "severe" and current < status_precedence["conflicting"]:
                field.status = "conflicting"
            elif verdict == "low_risk" and current < status_precedence["low_confidence"]:
                field.status = "low_confidence"

        db.session.commit()
        return report
