#!/usr/bin/env python3
"""Validate a Voice Loom scaffold output file.

Checks that the scaffold meets the structural requirements:
- Every row has a statement
- Every word palette has 5-8 items
- Every row has an emotional anchor
- No AI tell words in the statements

Usage:
    python validate-output.py <scaffold_file.md>

Exit code 0 = valid. Non-zero = issues found.
"""

import re
import sys
from pathlib import Path


# AI tell words to flag in statements (subset of humanizer patterns)
AI_TELL_WORDS = [
    "delve", "showcase", "tapestry", "crucial", "foster", "pivotal",
    "testament", "underscore", "highlight", "vibrant", "groundbreaking",
    "additionally", "moreover", "furthermore", "emphasizing", "reflecting",
    "contributing to", "symbolizing", "cultivating", "encompassing",
    "nestled", "breathtaking", "stunning", "renowned",
]


def extract_table_rows(content: str) -> list[dict]:
    """Extract rows from a Voice Loom markdown table."""
    rows = []
    in_table = False
    header_seen = False

    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("| # |") or line.startswith("|---|"):
            in_table = True
            header_seen = True
            continue
        if in_table and line.startswith("|"):
            if not header_seen:
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) >= 4:
                rows.append({
                    "num": cells[0],
                    "statement": cells[1],
                    "palette": cells[2],
                    "anchor": cells[3],
                })
        elif in_table and not line.startswith("|"):
            in_table = False
            header_seen = False

    return rows


def validate(rows: list[dict]) -> list[str]:
    """Validate rows and return list of issues."""
    issues = []

    if not rows:
        issues.append("No table rows found in the scaffold")
        return issues

    for i, row in enumerate(rows, 1):
        # Statement check
        if not row["statement"]:
            issues.append(f"Row {i}: empty statement")

        # Palette count check
        palette_words = [w.strip() for w in row["palette"].split(",") if w.strip()]
        if len(palette_words) < 5:
            issues.append(
                f"Row {i}: palette has {len(palette_words)} words (minimum 5)"
            )
        if len(palette_words) > 8:
            issues.append(
                f"Row {i}: palette has {len(palette_words)} words (maximum 8)"
            )

        # Anchor check
        if not row["anchor"]:
            issues.append(f"Row {i}: missing emotional anchor")

        # AI tell check
        statement_lower = row["statement"].lower()
        for tell in AI_TELL_WORDS:
            if tell in statement_lower:
                issues.append(
                    f"Row {i}: AI tell word '{tell}' in statement"
                )

    return issues


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate-output.py <scaffold_file.md>")
        sys.exit(1)

    filepath = Path(sys.argv[1])
    if not filepath.exists():
        print(f"Error: file not found: {filepath}")
        sys.exit(1)

    content = filepath.read_text()
    rows = extract_table_rows(content)
    issues = validate(rows)

    if issues:
        print(f"Issues found ({len(issues)}):")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)
    else:
        print(f"Valid: {len(rows)} rows, all checks passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
