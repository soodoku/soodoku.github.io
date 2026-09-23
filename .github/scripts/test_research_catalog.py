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

    def test_coauthors_publication_and_relationships_render(self):
        self.entry["coauthors"] = ["First Author", "Second Author"]
        self.entry["publication"] = [
            {"tag": "i", "children": ["A & B"]},
            ", 2026.",
        ]
        related = self.catalog["entries"][1]
        self.entry["related_entries"] = [related["id"]]
        generated = render(self.catalog, "{{catalog}}")
        self.assertIn("With First Author and Second Author.", generated)
        self.assertIn("<i>A &amp; B</i>, 2026.", generated)
        self.assertIn('<span class="supporting-label">Related:</span>', generated)
        self.assertEqual(generated.count(related["title"]), 2)

    def test_coauthor_punctuation_order_and_escaping(self):
        cases = [
            (["Zoe"], "With Zoe."),
            (["Zoe", "Alice"], "With Zoe and Alice."),
            (["Zoe", "Alice", "Bob"], "With Zoe, Alice, and Bob."),
            (["A < B", "C & D"], "With A &lt; B and C &amp; D."),
        ]
        for names, expected in cases:
            with self.subTest(names=names):
                self.entry["coauthors"] = names
                generated = render(self.catalog, "{{catalog}}")
                self.assertIn(f"</a><br>{expected}", generated)

    def test_metadata_precedes_resources_and_is_not_repeated_in_related_links(self):
        self.entry["coauthors"] = ["Test Collaborator"]
        self.entry["publication"] = ["Test Journal, 2026."]
        self.entry["summary"] = [
            {"resource": self.entry["primary_resource"]},
            {"tag": "br"},
            "Supporting resources",
        ]
        self.catalog["entries"][1]["related_entries"] = [self.entry["id"]]
        generated = render(self.catalog, "{{catalog}}")
        self.assertIn(
            "</a><br>With Test Collaborator.<br>Test Journal, 2026."
            "<br>Supporting resources",
            generated,
        )
        self.assertEqual(generated.count("With Test Collaborator."), 1)

    def test_solo_entry_has_no_credit_line(self):
        self.entry.pop("coauthors", None)
        self.entry["summary"] = [{"resource": self.entry["primary_resource"]}]
        self.entry["publication"] = ["Test Journal, 2026."]
        generated = render(self.catalog, "{{catalog}}")
        self.assertIn(
            f'{self.entry["title"]}</a><br>Test Journal, 2026.</summary>', generated
        )
        self.entry.pop("publication")
        generated = render(self.catalog, "{{catalog}}")
        self.assertIn(f'{self.entry["title"]}</a></summary>', generated)

    def test_rejects_invalid_coauthor_names(self):
        for name in ("Gaurav Sood", " ", " Alice", "Alice "):
            with self.subTest(name=name):
                self.entry["coauthors"] = [name]
                with self.assertRaisesRegex(ValueError, "collaborator names"):
                    validate_catalog(self.catalog)

    def test_publication_resource_references_are_validated(self):
        self.entry["publication"] = [{"resource": "missing"}]
        with self.assertRaisesRegex(ValueError, "Unknown inline resource"):
            validate_catalog(self.catalog)

    def test_catalog_credits_use_only_structured_coauthors(self):
        def text(nodes):
            return " ".join(
                node if isinstance(node, str) else text(node.get("children", []))
                for node in nodes
            )

        for entry in self.catalog["entries"]:
            with self.subTest(entry=entry["id"]):
                self.assertNotIn("authors", entry)
                self.assertNotRegex(text(entry["summary"]), r"\bWith\s")

    def test_deliberation_credits_preserve_collaborators_and_omit_site_owner(self):
        entries = {entry["id"]: entry for entry in self.catalog["entries"]}
        for entry_id in (
            "how-can-you-think-that-deliberation-and-the-learning-"
            "of-opposing-arguments",
            "deliberation-and-learning-evidence-from-deliberative-polls",
        ):
            self.assertEqual(
                entries[entry_id]["coauthors"],
                ["Robert C. Luskin", "James S. Fishkin"],
            )
        self.assertNotIn(
            "coauthors",
            entries[
                "steadier-not-closer-separating-convergence-from-"
                "crystallization-in-deliberative-polls"
            ],
        )

    def test_supporting_groups_preserve_items_and_order(self):
        self.entry["supporting"] = {
            "press": [["Coverage <one>"]],
            "related": [[{"resource": self.entry["primary_resource"]}, " A note"]],
        }
        generated = render(self.catalog, "{{catalog}}")
        self.assertLess(
            generated.index('class="supporting-label">Related:'),
            generated.index('class="supporting-label">Press:'),
        )
        self.assertIn(" A note</span>", generated)
        self.assertIn('role="listitem">Coverage &lt;one&gt;</span>', generated)

    def test_rejects_unknown_supporting_resource(self):
        self.entry["supporting"] = {"related": [[{"resource": "missing"}]]}
        with self.assertRaisesRegex(ValueError, "Unknown inline resource"):
            validate_catalog(self.catalog)

    def test_source_mapping_is_read_from_resources(self):
        sources = paper_sources(self.catalog)
        self.assertEqual(
            sources["quota_elite_quality.pdf"],
            {
                "repo": "in-rolls/quota_elite_quality",
                "ref": "main",
                "path": "manuscript/main.pdf",
            },
        )
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
