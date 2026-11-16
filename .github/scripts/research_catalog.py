"""Validate the research catalog and generate the checked-in research pages."""

import argparse
import html
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def load_catalog(root=ROOT):
    catalog = json.loads((root / "research/catalog.json").read_text())
    schema = json.loads((root / "research/catalog.schema.json").read_text())
    Draft202012Validator(schema).validate(catalog)
    validate_catalog(catalog, root)
    return catalog


def unique(items, label):
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate {label} ID")
    return set(ids)


def validate_catalog(catalog, root=ROOT):
    entry_ids = unique(catalog["entries"], "entry")
    section_ids = set()
    displayed = set()
    destinations = set()

    def section(value):
        if value["id"] in section_ids:
            raise ValueError("Duplicate section ID")
        section_ids.add(value["id"])
        for child in value["children"]:
            if "entry" in child:
                if child["entry"] not in entry_ids:
                    raise ValueError("Unknown section entry")
                displayed.add(child["entry"])
            else:
                section(child)

    for value in catalog["sections"]:
        section(value)
    if displayed != entry_ids:
        raise ValueError("Every entry must appear in a section")
    for entry in catalog["entries"]:
        for name in entry.get("coauthors", []):
            if not name.strip() or name != name.strip() or name == "Gaurav Sood":
                raise ValueError("Coauthors must be trimmed collaborator names")
        resources = unique(entry["resources"], "resource")
        if entry["primary_resource"] not in resources:
            raise ValueError("Unknown primary resource")
        if not set(entry.get("related_entries", [])) <= entry_ids:
            raise ValueError("Unknown related entry")

        def nodes(values):
            for node in values:
                if isinstance(node, str):
                    continue
                if "resource" in node and node["resource"] not in resources:
                    raise ValueError("Unknown inline resource")
                if node.get("tag") == "br" and node.get("children"):
                    raise ValueError("Line breaks cannot have children")
                nodes(node.get("children", []))

        nodes(entry["summary"])
        nodes(entry["description"])
        nodes(entry.get("publication", []))
        for items in entry.get("supporting", {}).values():
            for item in items:
                nodes(item)
        for resource in entry["resources"]:
            if resource["id"] != entry["primary_resource"] and not resource.get(
                "label"
            ):
                raise ValueError("Secondary resources need a label")
            url = resource["url"]
            parsed = urlsplit(url)
            if parsed.scheme not in ("", "http", "https") or url.startswith("//"):
                raise ValueError(f"Unsupported resource URL: {url}")
            if not resource.get("source"):
                continue
            if resource["kind"] != "pdf" or not re.fullmatch(
                r"papers/[A-Za-z0-9_-]+\.pdf", url
            ):
                raise ValueError("Synced resources must be local papers/*.pdf files")
            destination = root / "research" / url
            if not destination.is_file() or destination.is_symlink():
                raise ValueError(f"Destination must be an existing regular PDF: {url}")
            if url in destinations:
                raise ValueError(f"Duplicate sync destination: {url}")
            destinations.add(url)
            for field in ("ref", "path"):
                value = resource["source"][field]
                if (
                    not re.fullmatch(r"[A-Za-z0-9_./-]+", value)
                    or value.startswith("/")
                    or ".." in PurePosixPath(value).parts
                ):
                    raise ValueError(f"Invalid source {field}: {value}")
            if not resource["source"]["path"].endswith(".pdf"):
                raise ValueError("Source must be a PDF")


def paper_sources(catalog):
    return {
        resource["url"].removeprefix("papers/"): resource["source"]
        for entry in catalog["entries"]
        for resource in entry["resources"]
        if "source" in resource
    }


def rich_text(nodes, entry, metadata=""):
    resources = {resource["id"]: resource for resource in entry["resources"]}
    parts = []
    for node in nodes:
        if isinstance(node, str):
            parts.append(html.escape(node))
        elif "resource" in node:
            resource = resources[node["resource"]]
            primary = resource["id"] == entry["primary_resource"]
            label = entry["title"] if primary else resource["label"]
            target = ' target="_blank"' if resource.get("new_tab") else ""
            css = ' class="entry-title"' if primary else ""
            parts.append(
                f'<a href="{html.escape(resource["url"], quote=True)}"'
                f"{target}{css}>"
                f"{html.escape(label)}</a>"
            )
            if primary:
                parts.append(metadata)
        else:
            tag = node["tag"]
            css = f' class="{node["class"]}"' if "class" in node else ""
            parts.append(f"<{tag}{css}>")
            if tag != "br":
                parts.append(rich_text(node.get("children", []), entry, metadata))
                parts.append(f"</{tag}>")
    return "".join(parts)


def credit(coauthors):
    if len(coauthors) == 1:
        return coauthors[0]
    if len(coauthors) == 2:
        return " and ".join(coauthors)
    return ", ".join(coauthors[:-1]) + ", and " + coauthors[-1]


def entry_summary(entry, entries):
    """Title line, credits, citation, resources, and related/press rows."""
    metadata = ""
    if coauthors := entry.get("coauthors"):
        metadata += f"<br>With {html.escape(credit(coauthors))}."
    if publication := entry.get("publication"):
        metadata += "<br>" + rich_text(publication, entry)
    summary = rich_text(entry["summary"], entry, metadata)
    supporting = {
        label: [rich_text(item, entry) for item in items]
        for label, items in entry.get("supporting", {}).items()
    }
    if entry.get("related_entries"):
        links = supporting.setdefault("related", [])
        for related_id in entry["related_entries"]:
            related = entries[related_id]
            links.append(
                rich_text([{"resource": related["primary_resource"]}], related)
            )
    for label in ("related", "press"):
        if items := supporting.get(label):
            summary += (
                '<span class="supporting-row">'
                f'<span class="supporting-label">{label.title()}:</span> '
                '<span class="supporting-items" role="list">'
            )
            summary += "; ".join(
                f'<span class="supporting-item" role="listitem">{item}</span>'
                for item in items
            )
            summary += "</span></span>"
    return summary


def fill(template, **values):
    for name, value in values.items():
        placeholder = "{{" + name + "}}"
        if template.count(placeholder) != 1:
            raise ValueError(f"Template must contain exactly one {name} placeholder")
        template = template.replace(placeholder, value)
    return template


def render(catalog, template):
    entries = {entry["id"]: entry for entry in catalog["entries"]}

    def section(value):
        parts = [
            '<details class="category-toggle">',
            f'<summary>{html.escape(value["title"])}</summary>',
        ]
        for child in value["children"]:
            if "entry" not in child:
                parts.append(section(child))
                continue
            entry = entries[child["entry"]]
            css = f' class="{entry["class"]}"' if "class" in entry else ""
            parts.extend(
                [
                    f"<details{css}>",
                    f"<summary>{entry_summary(entry, entries)}</summary>",
                    f'<div class="abstract">{rich_text(entry["description"], entry)}'
                    "</div>",
                    "</details>",
                ]
            )
        parts.append("</details>")
        return "\n".join(parts)

    return fill(template, catalog="\n".join(map(section, catalog["sections"])))


def placements(catalog):
    """Each entry once, at its first position: (top section, subsection, id)."""
    seen = set()

    def walk(top, value, path):
        for child in value["children"]:
            if "entry" not in child:
                yield from walk(top, child, path + [child["title"]])
            elif child["entry"] not in seen:
                seen.add(child["entry"])
                yield top, " › ".join(path), child["entry"]

    for top in catalog["sections"]:
        yield from walk(top, top, [])


def render_swipe(catalog, template):
    entries = {entry["id"]: entry for entry in catalog["entries"]}
    hues = {
        top["id"]: round(index * 360 / len(catalog["sections"]))
        for index, top in enumerate(catalog["sections"])
    }
    chips, cards = [], []
    for number, (top, subsection, entry_id) in enumerate(placements(catalog), 1):
        entry = entries[entry_id]
        if not chips or chips[-1][0] != top["id"]:
            chips.append((top["id"], top["title"], number))
        where = html.escape(top["title"])
        if subsection:
            where += f' <span class="sub">› {html.escape(subsection)}</span>'
        cards.append(
            f'<article class="card" id="card-{number}" data-section="{top["id"]}"'
            f' style="--hue: {hues[top["id"]]}">'
            '<div class="card-inner">'
            f'<p class="where"><span class="kind">{entry["kind"]}</span> {where}</p>'
            f'<div class="summary">{entry_summary(entry, entries)}</div>'
            f'<div class="abstract">{rich_text(entry["description"], entry)}</div>'
            '<button class="more" type="button" hidden>More</button>'
            "</div></article>"
        )
    chip_links = "\n".join(
        f'<a href="#card-{number}" data-section="{section_id}"'
        f' style="--hue: {hues[section_id]}">{html.escape(title)}</a>'
        for section_id, title, number in chips
    )
    return fill(
        template, chips=chip_links, cards="\n".join(cards), total=str(len(cards))
    )


PAGES = (
    (render, "research/page.template.html", "research/index.html"),
    (render_swipe, "research/swipe.template.html", "research/swipe.html"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = load_catalog()
    for renderer, template, output in PAGES:
        generated = renderer(catalog, (ROOT / template).read_text())
        destination = ROOT / output
        if not args.check:
            destination.write_text(generated)
        elif not destination.is_file() or destination.read_text() != generated:
            parser.exit(
                1,
                f"{output} is stale. "
                "Run python .github/scripts/research_catalog.py to regenerate.\n",
            )
    if args.check:
        print("Research catalog and generated pages are current.")


if __name__ == "__main__":
    main()
