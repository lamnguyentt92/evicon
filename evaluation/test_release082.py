"""Release 082: the artifact's documents for the final report.

G1 the 077 plan's outcome is recorded next to the frozen plan (RESULTS_review_077.md names the plan's unchanged
   SHA-256, the final bundle and run, and carries the interpretation note), and ARTIFACT.md no longer lists it as pending
"""
import hashlib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PREREG = REPO / "evaluation" / "prereg"


class G1Records(unittest.TestCase):
    def test_review_077_recorded(self):
        plan = PREREG / "PREREG_review_077.md"
        sha = hashlib.sha256(plan.read_bytes()).hexdigest()
        self.assertEqual(sha, "db67640d1709b06dfa5e61f77e3444c07e7c5828dfe24e0e85d163f06a711a29")
        rec = (PREREG / "RESULTS_review_077.md").read_text()
        self.assertIn(sha, rec.splitlines()[0])
        self.assertIn("EVICON_081_result.zip", rec.splitlines()[0])
        self.assertIn("review-20261030T182927", rec.splitlines()[0])
        self.assertIn("| K-2 |", rec)
        self.assertIn("## Interpretation (written after the run; not part of the plan)", rec)
        art = (REPO / "ARTIFACT.md").read_text()
        self.assertNotIn("pending", art)
        self.assertIn("Release **082**", art)


if __name__ == "__main__":
    unittest.main()
