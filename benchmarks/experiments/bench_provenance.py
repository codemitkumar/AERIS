"""How many of the encoded limits can be traced to a cited source?

A reviewer asked how the modules were validated against the regulatory limits
they claim to encode. The honest answer requires a number, not a paragraph:
this counts module-level numeric constants and checks whether a regulatory
source is named anywhere in the module that defines them.

A named source in a comment is weak evidence - it is not machine-checkable and
nothing verifies that the constant matches the document - but it is the
difference between a constant that can be audited and one that cannot.
"""

import ast
import json
import os
import pathlib
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", "AERIS_ENGINE"))

# Documents whose limits these modules claim to encode.
SOURCES = [
    ("ICAO", r"\bICAO\b"),
    ("FAR/CFR Part 25", r"\bFAR[\s-]*25\b|\bPart\s*25\b|14\s*CFR\s*25"),
    ("FAR/CFR Part 23", r"\bFAR[\s-]*23\b|\bPart\s*23\b|14\s*CFR\s*23"),
    ("TSO / TAWS", r"\bTSO[-\s]?C?\d*|\bTAWS\b"),
    ("Annex", r"\bAnnex\s*\d"),
    ("AC (advisory circular)", r"\bAC\s*\d{2,}"),
    ("Aircraft manual / AFM", r"\bAFM\b|\bFCOM\b|flight manual"),
    ("Observed from data", r"observed|measured|derived from|p5\b|percentile"),
]


def module_constants(tree):
    """Module-level NAME = <number> assignments."""
    out = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, (int, float)) and not isinstance(
                    node.value.value, bool):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out.append(t.id)
        # Class-level thresholds count too: _MAX_AGL_FT and friends live there.
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Constant):
                    if isinstance(sub.value.value, (int, float)) and not isinstance(
                            sub.value.value, bool):
                        for t in sub.targets:
                            if isinstance(t, ast.Name):
                                out.append(t.id)
    return out


def main():
    root = pathlib.Path(ENGINE) / "modules" / "math"
    per_source = {name: 0 for name, _ in SOURCES}
    modules = 0
    with_any = 0
    total_consts = 0
    consts_in_cited = 0
    uncited_modules = []

    for path in sorted(root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        modules += 1
        consts = module_constants(tree)
        total_consts += len(consts)

        hits = [name for name, pat in SOURCES if re.search(pat, src, re.I)]
        for h in hits:
            per_source[h] += 1
        if hits:
            with_any += 1
            consts_in_cited += len(consts)
        else:
            uncited_modules.append(str(path.relative_to(root)))

    print(f"detection modules examined      : {modules}")
    print(f"numeric constants defined       : {total_consts}")
    print(f"modules naming any source       : {with_any} "
          f"({with_any / modules * 100:.0f}%)")
    print(f"constants in such modules       : {consts_in_cited} "
          f"({consts_in_cited / total_consts * 100:.0f}%)")
    print(f"modules naming no source at all : {len(uncited_modules)}")
    print("\nby source named:")
    for name, _ in SOURCES:
        print(f"   {name:<26} {per_source[name]:>3} modules")
    print("\nmodules with no named source:")
    for m in uncited_modules:
        print(f"   {m}")

    out = {
        "modules": modules,
        "constants": total_consts,
        "modules_citing_a_source": with_any,
        "constants_in_citing_modules": consts_in_cited,
        "per_source": per_source,
        "uncited_modules": uncited_modules,
        "note": "A named source in a comment is not machine-checkable; no "
                "automated check verifies a constant against its document.",
    }
    with open(os.path.join(HERE, "provenance_results.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nwritten -> provenance_results.json")


main()
