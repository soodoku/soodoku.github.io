"""Behavioral checks for catalog validation and page generation."""

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import research_catalog
from jsonschema import Draft202012Validator, ValidationError
from research_catalog import (
    ROOT,
    load_catalog,
    paper_sources,
    placements,
    render,
    render_entry_pages,
    render_llms_txt,
    render_schema,
    render_sitemap,
    render_swipe,
    validate_catalog,
)

SWIPE = "{{chips}}|{{cards}}|{{total}}"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.entry = self.catalog["entries"][0]

    def test_checked_in_page_matches_generator(self):
        generated = render(
            self.catalog, (ROOT / "research/page.template.html").read_text()
        )
        self.assertEqual(generated, (ROOT / "research/index.html").read_text())

    def test_checked_in_swipe_page_matches_generator(self):
        generated = render_swipe(
            self.catalog, (ROOT / "research/swipe.template.html").read_text()
        )
        self.assertEqual(generated, (ROOT / "research/swipe.html").read_text())

    def test_swipe_has_one_card_per_entry_even_when_listed_twice(self):
        listed = []

        def walk(value):
            for child in value["children"]:
                if "entry" in child:
                    listed.append(child["entry"])
                else:
                    walk(child)

        for value in self.catalog["sections"]:
            walk(value)
        self.assertGreater(len(listed), len(set(listed)))
        generated = render_swipe(self.catalog, SWIPE)
        self.assertEqual(
            generated.count('<article class="card"'), len(self.catalog["entries"])
        )
        self.assertTrue(generated.endswith(f"|{len(self.catalog['entries'])}"))

    def test_swipe_keeps_first_position_and_section_order(self):
        order = [entry_id for _, _, entry_id in placements(self.catalog)]
        self.assertEqual(
            order[0], self.catalog["sections"][0]["children"][0]["children"][0]["entry"]
        )
        sections = [top["id"] for top, _, _ in placements(self.catalog)]
        self.assertEqual(
            list(dict.fromkeys(sections)),
            [value["id"] for value in self.catalog["sections"]],
        )
        chips = re.findall(
            r'href="#card-(\d+)" data-section="([^"]+)"',
            render_swipe(self.catalog, SWIPE).split("|")[0],
        )
        for number, section_id in chips:
            self.assertEqual(sections[int(number) - 1], section_id)
            if int(number) > 1:
                self.assertNotEqual(sections[int(number) - 2], section_id)

    def test_swipe_labels_subsections_and_escapes(self):
        self.entry["title"] = "A < B"
        generated = render_swipe(self.catalog, SWIPE)
        self.assertIn("A &lt; B</a>", generated)
        self.assertIn(
            '<span class="sub">\u203a <span class="catalog-heading">Prediction '
            "Stability</span></span>",
            generated,
        )

    def test_catalog_heading_marks_connectors_and_preserves_names(self):
        heading = research_catalog.catalog_heading("the IPL And iOS <ratings>")
        self.assertEqual(
            heading,
            '<span class="catalog-heading">the IPL <span class="minor-word">And</span> '
            '<span class="keep-case">iOS</span> &lt;ratings&gt;</span>',
        )

    def test_templates_require_each_placeholder_once(self):
        for template in ("{{chips}}|{{cards}}", SWIPE + "{{cards}}"):
            with self.subTest(template=template):
                with self.assertRaisesRegex(ValueError, "placeholder"):
                    render_swipe(self.catalog, template)
        with self.assertRaisesRegex(ValueError, "catalog placeholder"):
            render(self.catalog, "no placeholder")

    def test_check_fails_when_swipe_page_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "research").mkdir()
            for name in (
                "page.template.html",
                "index.html",
                "swipe.template.html",
                "swipe.html",
                "entry.template.html",
            ):
                (root / "research" / name).write_text(
                    (ROOT / "research" / name).read_text()
                )
            for name in ("sitemap.xml", "llms.txt"):
                (root / name).write_text((ROOT / name).read_text())
            for entry in self.catalog["entries"]:
                sub = root / "research" / entry["id"]
                sub.mkdir(parents=True, exist_ok=True)
                (sub / "index.html").write_text(
                    (ROOT / "research" / entry["id"] / "index.html").read_text()
                )
            with (
                mock.patch.object(research_catalog, "ROOT", root),
                mock.patch.object(
                    research_catalog, "load_catalog", lambda: self.catalog
                ),
                mock.patch.object(sys, "argv", ["research_catalog.py", "--check"]),
            ):
                research_catalog.main()
                (root / "research/swipe.html").write_text("stale")
                with self.assertRaises(SystemExit) as raised:
                    research_catalog.main()
            self.assertEqual(raised.exception.code, 1)

    def test_schema_sitemap_and_llms_txt_match_catalog(self):
        schema_data = json.loads(render_schema(self.catalog))
        self.assertEqual(schema_data["@type"], "CollectionPage")
        items = schema_data["mainEntity"]["itemListElement"]
        self.assertEqual(len(items), len(self.catalog["entries"]))
        self.assertEqual(
            items[0]["item"]["@id"],
            f"https://gsood.com/research/{self.entry['id']}/",
        )
        sitemap = render_sitemap(self.catalog)
        self.assertIn("<loc>https://gsood.com/</loc>", sitemap)
        self.assertIn("<loc>https://gsood.com/research/</loc>", sitemap)
        self.assertIn(
            f"<loc>https://gsood.com/research/{self.entry['id']}/</loc>", sitemap
        )
        self.assertIn(
            "<loc>https://gsood.com/research/papers/queue_shift.pdf</loc>", sitemap
        )
        llms = render_llms_txt(self.catalog)
        self.assertTrue(llms.startswith("# Gaurav Sood\n"))
        self.assertIn(
            f"https://gsood.com/research/{self.entry['id']}/",
            llms,
        )

    def test_dedicated_paper_pages_render_for_all_papers(self):
        template = (ROOT / "research/entry.template.html").read_text()
        pages = render_entry_pages(self.catalog, template)
        self.assertEqual(len(pages), len(self.catalog["entries"]))
        for rel_path, content in pages.items():
            self.assertEqual(content, (ROOT / rel_path).read_text())
            self.assertIn('<section class="citation-section">', content)
            self.assertIn('name="citation_title"', content)
        sample = copy.deepcopy(self.catalog)
        sample["entries"][0]["details"] = [
            {"heading": "Experimental Design", "body": ["Two field experiments."]}
        ]
        rendered = render_entry_pages(sample, template)
        first_page = rendered[f"research/{self.entry['id']}/index.html"]
        self.assertIn(
            '<h2><span class="catalog-heading">Experimental Design</span></h2>',
            first_page,
        )
        self.assertIn("Two field experiments.", first_page)

    def test_anchor_ids_are_unique_when_entry_appears_twice(self):
        generated = render(self.catalog, "{{catalog}}")
        for entry in self.catalog["entries"]:
            self.assertEqual(
                generated.count(f'<details id="{entry["id"]}"'),
                1,
            )

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
        resource["url"] = "papers/partisan_vision.pdf"
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

    def test_draft_label_follows_title_and_excludes_publication(self):
        self.entry.pop("publication", None)
        self.entry["status"] = "draft"
        self.entry["summary"] = [{"resource": self.entry["primary_resource"]}]
        generated = render(self.catalog, "{{catalog}}")
        self.assertIn(
            f'{self.entry["title"]}</a> <span class="status">Draft</span>', generated
        )
        self.entry["publication"] = ["Test Journal, 2026."]
        with self.assertRaisesRegex(ValueError, "draft cannot have a publication"):
            validate_catalog(self.catalog)
        self.entry.pop("publication")
        self.entry["status"] = "finished"
        schema = json.loads((ROOT / "research/catalog.schema.json").read_text())
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(self.catalog)

    def test_software_types_and_publication_dates(self):
        schema = json.loads((ROOT / "research/catalog.schema.json").read_text())
        validator = Draft202012Validator(
            schema["$defs"]["entry"] | {"$defs": schema["$defs"]},
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        entry = next(e for e in self.catalog["entries"] if e["kind"] == "software")
        validator.validate(entry | {"first_published": "2024-02-29"})
        for field in ({"first_published": "2025-02-29"}, {"software_type": "misc"}):
            self.assertFalse(validator.is_valid(entry | field))
        self.assertFalse(
            validator.is_valid({k: v for k, v in entry.items() if k != "software_type"})
        )
        self.assertFalse(validator.is_valid(self.entry | {"software_type": "package"}))
        for kind, (symbol, _) in research_catalog.SOFTWARE_ICONS.items():
            marked = entry | {"software_type": kind, "first_published": "2024-02-29"}
            line = research_catalog.entry_summary(marked, {})
            self.assertIn(symbol, line)
            self.assertIn('hidden data-first-published="2024-02-29"', line)
        self.assertNotIn("🌱", research_catalog.entry_summary(entry, {}))

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
        self.assertNotIn("partisan_vision.pdf", sources)

    def test_schema_rejects_unsupported_markup(self):
        import json

        from jsonschema import Draft202012Validator

        schema = json.loads((ROOT / "research/catalog.schema.json").read_text())
        self.entry["summary"].append({"tag": "script", "children": ["alert(1)"]})
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(self.catalog)


if __name__ == "__main__":
    unittest.main()
