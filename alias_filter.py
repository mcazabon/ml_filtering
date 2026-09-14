from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.linear_model import SGDClassifier


ALIAS_SEPARATOR = re.compile(r"[;,|]")
PREFIX = re.compile(r"^(?:adid|smtp|x500|sip|upn):", re.IGNORECASE)
NON_ALNUM = re.compile(r"[^a-z0-9]+")
EMAIL_FORMAT = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
EXCLUDED_ALIAS_TYPES = {"filter1", "filter2", "filter3"}


class AliasLearningModel:
    """Online character model trained from only high-confidence rule decisions."""

    def __init__(self) -> None:
        self.vectorizer = HashingVectorizer(
            analyzer="char",
            ngram_range=(2, 5),
            n_features=2**14,
            alternate_sign=False,
        )
        self.classifier = SGDClassifier(loss="log_loss", alpha=0.0001, random_state=42)
        self.fitted = False

    @staticmethod
    def feature_text(alias: str, row: dict[str, str]) -> str:
        alias_type, _, alias_value = alias.partition(":")
        return "|".join(
            (
                alias_type.lower(),
                normalize(alias_value or alias),
                normalize(row.get("Email")),
                normalize(row.get("First_Name")),
                normalize(row.get("Last_Name")),
            )
        )

    def predict_score(self, alias: str, row: dict[str, str]) -> float | None:
        if not self.fitted:
            return None
        features = self.vectorizer.transform([self.feature_text(alias, row)])
        return float(self.classifier.predict_proba(features)[0, 1])

    def learn(self, alias: str, row: dict[str, str], label: int) -> None:
        features = self.vectorizer.transform([self.feature_text(alias, row)])
        if not self.fitted:
            self.classifier.partial_fit(features, [label], classes=[0, 1])
            self.fitted = True
        else:
            self.classifier.partial_fit(features, [label])


def split_aliases(value: str) -> list[str]:
    """Split common multi-value AD exports while keeping the original text out of scoring."""
    return [part.strip() for part in ALIAS_SEPARATOR.split(value or "") if part.strip()]


def normalize(value: str | None) -> str:
    value = PREFIX.sub("", (value or "").strip().lower())
    return NON_ALNUM.sub("", value)


def email_parts(email: str) -> tuple[str, str]:
    local, separator, domain = email.strip().lower().partition("@")
    return normalize(local), domain if separator else ""


def is_valid_email_alias(alias: str) -> bool:
    alias_type, separator, value = alias.strip().partition(":")
    return bool(separator and alias_type.lower() == "email" and EMAIL_FORMAT.fullmatch(value.strip()))


def is_excluded_alias_type(alias: str) -> bool:
    alias_type, separator, _ = alias.strip().partition(":")
    return bool(separator and alias_type.lower() in EXCLUDED_ALIAS_TYPES)


def name_tokens(row: dict[str, str]) -> set[str]:
    names = " ".join(
        row.get(column, "")
        for column in ("First_Name", "First Name", "Last_Name", "Last Name")
    )
    return {normalize(token) for token in re.split(r"\s+", names) if normalize(token)}


def score_alias(alias: str, row: dict[str, str]) -> tuple[float, str]:
    """Return a match probability and an explainable reason.

    This is deliberately conservative: an alias is flagged only when several
    independent patterns disagree with the email address.
    """
    local, domain = email_parts(row.get("Email", ""))
    alias_type, separator, alias_value = alias.partition(":")
    is_email_alias = separator and alias_type.lower() == "email"
    candidate_text = alias_value if is_email_alias else alias
    candidate = normalize(candidate_text.partition("@")[0] if is_email_alias else candidate_text)
    if not candidate or not local:
        return 0.0, "missing email or alias"

    names = name_tokens(row)
    similarity = SequenceMatcher(None, candidate, local).ratio()
    score = similarity
    reasons: list[str] = [f"character similarity={similarity:.2f}"]
    name_variants = [
        normalize(row.get("First_Name", "") + row.get("Last_Name", "")),
        normalize(row.get("Last_Name", "") + row.get("First_Name", "")),
        normalize(row.get("First Name", "") + row.get("Last Name", "")),
        normalize(row.get("Last Name", "") + row.get("First Name", "")),
    ]
    name_similarity = max(
        (SequenceMatcher(None, candidate, variant).ratio() for variant in name_variants if variant),
        default=0.0,
    )

    if candidate == local:
        score = 1.0
        reasons.append("exact email local-part")
    elif local in candidate or candidate in local:
        score = max(score, 0.86)
        reasons.append("one value contains the other")
    elif candidate in names or any(token in candidate for token in names if len(token) >= 4):
        score = max(score, 0.72)
        reasons.append("matches a name pattern")
    elif name_similarity >= 0.68:
        score = max(score, 0.72)
        reasons.append(f"matches a compact name pattern={name_similarity:.2f}")

    if domain and "@" in candidate_text.lower() and candidate_text.rsplit("@", 1)[1].lower() == domain:
        reasons.append("same email domain")
    if any(char.isdigit() for char in candidate) and not any(char.isdigit() for char in local):
        score -= 0.12
        reasons.append("unexpected digits")

    return max(0.0, min(1.0, score)), "; ".join(reasons)


def iter_decisions(
    rows: Iterable[dict[str, str]], threshold: float, learner: AliasLearningModel | None = None
) -> Iterable[dict[str, str]]:
    for row_number, row in enumerate(rows, start=2):
        aliases = split_aliases(row.get("Aliases", ""))
        if not aliases:
            decision = dict(row)
            decision.update({"alias": "", "match_score": "0.00", "status": "missing_alias", "reason": "no aliases"})
            yield decision
            continue

        for alias in aliases:
            if is_excluded_alias_type(alias):
                decision = dict(row)
                decision.update(
                    {
                        "source_row": str(row_number),
                        "alias": alias,
                        "match_score": "1.0000",
                        "status": "excluded_type",
                        "reason": "Alias type excluded from Email and ADID validation",
                    }
                )
                yield decision
                continue
            if alias.lower().startswith("email:") and not is_valid_email_alias(alias):
                decision = dict(row)
                decision.update(
                    {
                        "source_row": str(row_number),
                        "alias": alias,
                        "match_score": "0.0000",
                        "status": "invalid_format",
                        "reason": "Email alias is not a valid email address",
                    }
                )
                yield decision
                continue
            score, reason = score_alias(alias, row)
            model_score = learner.predict_score(alias, row) if learner else None
            if model_score is not None:
                learned_score = (score * 0.7) + (model_score * 0.3)
                score = max(score, learned_score)
                reason += f"; learned model={model_score:.2f}"
            if learner and score >= 0.72:
                learner.learn(alias, row, 1)
            elif learner and score <= 0.20:
                learner.learn(alias, row, 0)
            decision = dict(row)
            decision.update(
                {
                    "source_row": str(row_number),
                    "alias": alias,
                    "match_score": f"{score:.4f}",
                    "status": "match" if score >= threshold else "suspicious",
                    "reason": reason,
                }
            )
            yield decision


def filter_csv(
    input_path: Path,
    matches_path: Path,
    suspicious_path: Path,
    invalid_path: Path,
    threshold: float,
) -> tuple[int, int, int]:
    with input_path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "Email" not in reader.fieldnames or "Aliases" not in reader.fieldnames:
            raise ValueError("CSV must contain Email and Aliases columns")

        output_fields = [*reader.fieldnames, "source_row", "alias", "match_score", "status", "reason"]
        with matches_path.open("w", newline="", encoding="utf-8") as match_file, suspicious_path.open(
            "w", newline="", encoding="utf-8"
        ) as suspicious_file, invalid_path.open("w", newline="", encoding="utf-8") as invalid_file:
            match_writer = csv.DictWriter(match_file, fieldnames=output_fields)
            suspicious_writer = csv.DictWriter(suspicious_file, fieldnames=output_fields)
            invalid_writer = csv.DictWriter(invalid_file, fieldnames=output_fields)
            match_writer.writeheader()
            suspicious_writer.writeheader()
            invalid_writer.writeheader()
            match_count = suspicious_count = invalid_count = 0
            learner = AliasLearningModel()
            for decision in iter_decisions(reader, threshold, learner):
                if decision["status"] == "invalid_format":
                    invalid_writer.writerow(decision)
                    invalid_count += 1
                elif decision["status"] in {"match", "excluded_type"}:
                    match_writer.writerow(decision)
                    match_count += 1
                else:
                    suspicious_writer.writerow(decision)
                    suspicious_count += 1
    return match_count, suspicious_count, invalid_count


def write_cleaned_csv(input_path: Path, cleaned_path: Path, threshold: float) -> int:
    """Write one original contact row with only accepted aliases retained."""
    with input_path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "Email" not in reader.fieldnames or "Aliases" not in reader.fieldnames:
            raise ValueError("CSV must contain Email and Aliases columns")

        with cleaned_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
            writer.writeheader()
            row_count = 0
            learner = AliasLearningModel()
            for row in reader:
                accepted_aliases = [
                    decision["alias"]
                    for decision in iter_decisions([row], threshold, learner)
                    if decision["status"] in {"match", "excluded_type"}
                ]
                cleaned_row = {field: row.get(field) or "" for field in reader.fieldnames}
                cleaned_row["Aliases"] = "; ".join(accepted_aliases)
                writer.writerow(cleaned_row)
                row_count += 1
    return row_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Find email aliases that do not match their Email column.")
    parser.add_argument("input_csv", type=Path, nargs="?", default=Path(r"C:\temp\extracted_contact_data.csv"))
    parser.add_argument("--cleaned", type=Path, help="Cleaned contact CSV; defaults to a timestamped file beside the input.")
    parser.add_argument("--matches", type=Path, help="Accepted alias audit CSV.")
    parser.add_argument("--suspicious", type=Path, help="Suspicious alias audit CSV.")
    parser.add_argument("--invalid", type=Path, help="Invalid email alias audit CSV.")
    parser.add_argument("--threshold", type=float, default=0.62, help="Minimum score to classify as a match (default: 0.62).")
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_folder = args.input_csv.parent
    output_stem = args.input_csv.stem
    cleaned_path = args.cleaned or output_folder / f"{output_stem}_cleaned_{timestamp}.csv"
    matches_path = args.matches or output_folder / f"{output_stem}_matches_{timestamp}.csv"
    suspicious_path = args.suspicious or output_folder / f"{output_stem}_suspicious_{timestamp}.csv"
    invalid_path = args.invalid or output_folder / f"{output_stem}_invalid_{timestamp}.csv"
    matched, suspicious, invalid = filter_csv(args.input_csv, matches_path, suspicious_path, invalid_path, args.threshold)
    row_count = write_cleaned_csv(args.input_csv, cleaned_path, args.threshold)
    print(f"Wrote cleaned {row_count} contact rows to {cleaned_path}")
    print(f"Wrote {matched} matches to {matches_path}")
    print(f"Wrote {suspicious} suspicious aliases to {suspicious_path}")
    print(f"Wrote {invalid} invalid email aliases to {invalid_path}")


if __name__ == "__main__":
    main()