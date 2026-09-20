"""Behavioral checks for catalog validation and page generation."""

import copy
import unittest

from jsonschema import ValidationError
from research_catalog import ROOT, load_catalog, paper_sources, render, validate_catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.entry = self.catalog["entries"][0]

    def test_checked_in_page_matches_generator(self):
        generated = render(
            self.catalog, (ROOT / "research/page.template.html").read_text()
        )
        self.assertEqual(generated, (ROOT / "research/index.html").read_text())

    def test_rejects_duplicate_entry_and_resource_ids(self):
        self.catalog["entries"].append(copy.deepcopy(self.entry))
        with self.assertRaisesRegex(ValueError, "Duplicate entry"):
            validate_catalog(self.catalog)
        self.catalog["entries"].pop()
        self.entry["resources"].append(copy.deepcopy(self.entry["resources"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate resource"):
            validate_catalog(self.catalog)

    def test_rejects_broken_references(self):
        for field in ("primary_resource", "summary", "related_entries"):
            with self.subTest(field=field):
                catalog = copy.deepcopy(self.catalog)
                catalog["entries"][0][field] = {
                    "primary_resource": "missing",
                    "summary": [{"resource": "missing"}],
                    "related_entries": ["missing"],
                }[field]
                with self.assertRaisesRegex(ValueError, "Unknown"):
                    validate_catalog(catalog)
        self.catalog["sections"][0]["children"].append({"entry": "missing"})
        with self.assertRaisesRegex(ValueError, "Unknown section entry"):
            validate_catalog(self.catalog)

    def test_rejects_unsafe_urls_and_source_paths(self):
        resource = self.entry["resources"][0]
        resource["url"] = "javascript:alert(1)"
        with self.assertRaisesRegex(ValueError, "Unsupported resource URL"):
            validate_catalog(self.catalog)
        resource["url"] = "papers/forget.pdf"
        resource["source"] = {"repo": "owner/repo", "ref": "main", "path": "../x.pdf"}
        with self.assertRaisesRegex(ValueError, "Invalid source path"):
            validate_catalog(self.catalog)
        resource["url"] = "papers/../../x.pdf"
        with self.assertRaisesRegex(ValueError, "local papers"):
            validate_catalog(self.catalog)

    def test_rejects_duplicate_sync_destination(self):
        mapped = next(
            r for e in self.catalog["entries"] for r in e["resources"] if "source" in r
        )
        duplicate = copy.deepcopy(mapped)
        duplicate.update(id="duplicate", label="Duplicate")
        self.entry["resources"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "Duplicate sync destination"):
            validate_catalog(self.catalog)

    def test_title_and_url_edits_drive_output(self):
        self.entry["title"] = 'A < B & "quoted"'
        self.entry["resources"][0]["url"] = "https://example.org/?a=1&b=2"
        generated = render(self.catalog, "{{catalog}}")
        self.assertIn("A &lt; B &amp; &quot;quoted&quot;", generated)
        self.assertIn("https://example.org/?a=1&amp;b=2", generated)
        self.assertNotIn("< B", generated)

    def test_structured_authors_publication_and_relationships_render(self):
        self.entry["authors"] = ["First Author", "Second Author"]
        self.entry["publication"] = {"venue": "A & B", "year": 2026}
        related = self.catalog["entries"][1]
        self.entry["related_entries"] = [related["id"]]
        generated = render(self.catalog, "{{catalog}}")
        self.assertIn("First Author, Second Author", generated)
        self.assertIn("<i>A &amp; B</i>, 2026.", generated)
        self.assertIn('<span class="highlight">RELATED</span>: <a', generated)
        self.assertEqual(generated.count(related["title"]), 2)

    def test_source_mapping_is_read_from_resources(self):
        sources = paper_sources(self.catalog)
        self.assertEqual(len(sources), 5)
        self.assertEqual(sources["downloads_are_cheap.pdf"]["repo"], "recite/softverse")
        self.assertNotIn("forget.pdf", sources)

    def test_schema_rejects_unsupported_markup(self):
        import json

        from jsonschema import Draft202012Validator

        schema = json.loads((ROOT / "research/catalog.schema.json").read_text())
        self.entry["summary"].append({"tag": "script", "children": ["alert(1)"]})
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(self.catalog)


if __name__ == "__main__":
    unittest.main()
