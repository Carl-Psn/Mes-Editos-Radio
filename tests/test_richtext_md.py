"""Tests de conversion Markdown ↔ HTML pour l'éditeur de fiches."""

import unittest

import exporter
import richtext_md as rt


class RichTextMdTests(unittest.TestCase):
    def test_roundtrip_heading_and_styles(self) -> None:
        source = (
            "## Thèse centrale\n\n"
            "Un point **important** et un autre <u>souligné</u> en "
            '<span style="color:#E2001A">rouge</span>.\n\n'
            "> Citation courte\n\n"
            "- Premier niveau\n"
            "    - Deuxième niveau\n"
            "        - Troisième niveau"
        )
        html = rt.markdown_to_html(source)
        back = rt.html_to_markdown(html)
        self.assertIn("## Thèse centrale", back)
        self.assertIn("**important**", back)
        self.assertIn("<u>souligné</u>", back)
        self.assertIn('color:#e2001a', back.lower())
        self.assertIn("> Citation courte", back)
        self.assertIn("- Premier niveau", back)
        self.assertIn("    - Deuxième niveau", back)

    def test_exporter_reads_underline_and_color(self) -> None:
        text = "A <u>souligné</u> et <span style=\"color:#2563EB\">bleu</span>"
        runs = exporter.parse_inline(text)
        self.assertTrue(any(run[3] for run in runs))
        self.assertTrue(any(run[4] == "#2563eb" for run in runs))


if __name__ == "__main__":
    unittest.main()