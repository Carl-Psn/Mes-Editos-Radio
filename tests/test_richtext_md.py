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

    def test_apostrophe_and_quotes_roundtrip(self) -> None:
        source = 'L\'éditorial dit "y\'a un truc" autour de l\'économie.'
        html = rt.markdown_to_html(source)
        self.assertNotIn("&#x27;", html)
        self.assertNotIn("&quot;", html)
        self.assertIn("'", html)
        back = rt.html_to_markdown(html)
        self.assertIn("L'éditorial", back)
        self.assertIn('"y\'a un truc"', back)
        self.assertNotIn("&#x27;", back)

    def test_unescape_legacy_entities(self) -> None:
        legacy = "c&#x27;est &quot;normal&quot;"
        html = rt.markdown_to_html(legacy)
        self.assertIn("c'est", html)
        self.assertIn('"normal"', html)

    def test_nested_section3_lists_roundtrip(self) -> None:
        source = (
            "## 3. Enjeux\n\n"
            "- **Notions liées**\n"
            "  - Souveraineté numérique\n"
            "  - État stratège\n"
            "- **Sujets possibles**\n"
            "    - Composition\n"
        )
        html = rt.markdown_to_html(source)
        self.assertIn('data-indent="1"', html)
        self.assertIn("ql-indent-1", html)
        back = rt.html_to_markdown(html)
        self.assertIn("- **Notions liées**", back)
        self.assertIn("    - Souveraineté numérique", back)
        self.assertIn("    - Composition", back)

    def test_exporter_reads_underline_and_color(self) -> None:
        text = "A <u>souligné</u> et <span style=\"color:#2563EB\">bleu</span>"
        runs = exporter.parse_inline(text)
        self.assertTrue(any(run[3] for run in runs))
        self.assertTrue(any(run[4] == "#2563eb" for run in runs))


if __name__ == "__main__":
    unittest.main()