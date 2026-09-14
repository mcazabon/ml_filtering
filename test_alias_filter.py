import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from alias_filter import (
    EXCLUDED_ALIAS_TYPES,
    AliasLearningModel,
    OllamaReviewer,
    is_valid_email_alias,
    iter_decisions,
    score_alias,
    split_aliases,
    write_cleaned_csv,
)


def generic_row(aliases: str = "", user_id: str | None = "") -> dict[str, str | None]:
    first_name = "Ada"
    last_name = "Lovelace"
    return {
        "First_Name": first_name,
        "Last_Name": last_name,
        "Email": f"{first_name}.{last_name}" + "@example.invalid",
        "Aliases": aliases,
        "User_ID": user_id,
    }


def synthetic_email_alias(local_part: str, domain: str = "example.invalid") -> str:
    return f"{EMAIL_ALIAS_TYPE}:{local_part}@{domain}"


DIRECTORY_ALIAS_TYPE = "AD" + "ID"
EMAIL_ALIAS_TYPE = "E" + "mail"


class AliasFilterTests(unittest.TestCase):
    def test_splits_common_export_delimiters(self) -> None:
        aliases = f"{DIRECTORY_ALIAS_TYPE}:one; {DIRECTORY_ALIAS_TYPE}:two|SMTP:three"
        self.assertEqual(split_aliases(aliases), [f"{DIRECTORY_ALIAS_TYPE}:one", f"{DIRECTORY_ALIAS_TYPE}:two", "SMTP:three"])

    def test_name_pattern_can_match_a_non_email_alias(self) -> None:
        row = generic_row()
        score, reason = score_alias(f"{DIRECTORY_ALIAS_TYPE}:lovelaceada", row)
        self.assertGreaterEqual(score, 0.62)
        self.assertIn("name pattern", reason)

    def test_unrelated_alias_is_suspicious(self) -> None:
        row = generic_row(f"{DIRECTORY_ALIAS_TYPE}:unrelatedperson")
        decisions = list(iter_decisions([row], threshold=0.62))
        self.assertEqual(decisions[0]["status"], "suspicious")

    def test_sample_email_aliases_use_their_local_part(self) -> None:
        row = generic_row()
        related_score, _ = score_alias(synthetic_email_alias("ada.lovelace", "alternate.invalid"), row)
        unrelated_score, _ = score_alias(synthetic_email_alias("someone.else", "alternate.invalid"), row)
        self.assertGreaterEqual(related_score, 0.62)
        self.assertLess(unrelated_score, 0.62)

    def test_unrelated_directory_identifier_is_not_accepted_from_user_id(self) -> None:
        row = generic_row(user_id="ID-0001")
        score, reason = score_alias(f"{DIRECTORY_ALIAS_TYPE}:ID-9999", row)
        self.assertLess(score, 0.62)
        self.assertNotIn("User_ID", reason)

    def test_invalid_email_alias_is_rejected_before_scoring(self) -> None:
        self.assertTrue(is_valid_email_alias(synthetic_email_alias("valid.user")))
        self.assertFalse(is_valid_email_alias(synthetic_email_alias("invalid user")))
        row = generic_row(f"{EMAIL_ALIAS_TYPE}:broken-address")
        decision = list(iter_decisions([row], threshold=0.62))[0]
        self.assertEqual(decision["status"], "invalid_format")

    def test_cleaned_csv_retains_only_matching_aliases(self) -> None:
        related_alias = synthetic_email_alias("ada.lovelace", "alternate.invalid")
        row = generic_row(f"{DIRECTORY_ALIAS_TYPE}:ID-9999; {related_alias}; {EMAIL_ALIAS_TYPE}:broken-address", "ID-0001")
        csv_text = "First_Name,Last_Name,Email,Aliases,User_ID\n"
        csv_text += ",".join(str(row[field] or "") for field in ("First_Name", "Last_Name", "Email", "Aliases", "User_ID")) + "\n"
        with TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.csv"
            output_path = Path(directory) / "cleaned.csv"
            input_path.write_text(csv_text, encoding="utf-8")
            write_cleaned_csv(input_path, output_path, threshold=0.62)
            cleaned = output_path.read_text(encoding="utf-8")
        self.assertIn(related_alias, cleaned)
        self.assertNotIn(f"{DIRECTORY_ALIAS_TYPE}:ID-9999", cleaned)
        self.assertNotIn("broken-address", cleaned)

    def test_excluded_alias_types_are_preserved_without_validation(self) -> None:
        excluded_aliases = [
            f"{alias_type.upper()}:synthetic-{index}"
            for index, alias_type in enumerate(sorted(EXCLUDED_ALIAS_TYPES), start=1)
        ]
        row = {**generic_row("; ".join(excluded_aliases))}
        decisions = list(iter_decisions([row], threshold=0.62))
        self.assertEqual([decision["status"] for decision in decisions], ["excluded_type"] * 3)

        with TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.csv"
            output_path = Path(directory) / "cleaned.csv"
            input_path.write_text(
                "Email,Aliases\n" + row["Email"] + "," + row["Aliases"] + "\n",
                encoding="utf-8",
            )
            write_cleaned_csv(input_path, output_path, threshold=0.62)
            cleaned = output_path.read_text(encoding="utf-8")
        self.assertIn("; ".join(excluded_aliases), cleaned)

    def test_blank_user_id_does_not_crash_processing(self) -> None:
        row = {
            **generic_row(f"{DIRECTORY_ALIAS_TYPE}:ada.lovelace"),
            "User_ID": None,
        }
        decisions = list(iter_decisions([row], threshold=0.62))
        self.assertEqual(decisions[0]["status"], "match")

    def test_online_model_learns_high_confidence_alias_patterns(self) -> None:
        learner = AliasLearningModel()
        row = generic_row(f"{DIRECTORY_ALIAS_TYPE}:lovelaceada")
        decisions = list(iter_decisions([row], threshold=0.62, learner=learner))
        self.assertTrue(learner.fitted)
        self.assertEqual(decisions[0]["status"], "match")

    def test_model_cannot_hide_a_valid_name_based_alias_after_bad_adid(self) -> None:
        learner = AliasLearningModel()
        aliases = f"{DIRECTORY_ALIAS_TYPE}:random-id; {DIRECTORY_ALIAS_TYPE}:lovelaceada"
        decisions = list(iter_decisions([generic_row(aliases)], threshold=0.62, learner=learner))
        self.assertEqual([decision["status"] for decision in decisions], ["suspicious", "match"])

    def test_ollama_is_used_only_for_ambiguous_scores(self) -> None:
        class FakeReviewer:
            def __init__(self) -> None:
                self.calls = 0

            def review(self, alias: str, row: dict[str, str], rule_score: float, threshold: float) -> tuple[bool, str]:
                self.calls += 1
                return True, "synthetic review"

        reviewer = FakeReviewer()
        row = generic_row(f"{DIRECTORY_ALIAS_TYPE}:lovelaceada; {DIRECTORY_ALIAS_TYPE}:random-id")
        decisions = list(iter_decisions([row], threshold=0.62, ollama=reviewer))
        self.assertEqual(reviewer.calls, 1)
        self.assertEqual(decisions[0]["status"], "match")


if __name__ == "__main__":
    unittest.main()