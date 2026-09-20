"""Validate the research catalog and generate the checked-in research page."""

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


def render(catalog, template):
    entries = {entry["id"]: entry for entry in catalog["entries"]}

    def rich_text(nodes, entry):
        resources = {resource["id"]: resource for resource in entry["resources"]}
        parts = []
        for node in nodes:
            if isinstance(node, str):
                parts.append(html.escape(node))
            elif "resource" in node:
                resource = resources[node["resource"]]
                label = (
                    entry["title"]
                    if resource["id"] == entry["primary_resource"]
                    else resource["label"]
                )
                target = ' target="_blank"' if resource.get("new_tab") else ""
                parts.append(
                    f'<a href="{html.escape(resource["url"], quote=True)}"{target}>'
                    f"{html.escape(label)}</a>"
                )
            else:
                tag = node["tag"]
                css = f' class="{node["class"]}"' if "class" in node else ""
                parts.append(f"<{tag}{css}>")
                if tag != "br":
                    parts.append(rich_text(node.get("children", []), entry))
                    parts.append(f"</{tag}>")
        return "".join(parts)

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
            description = rich_text(entry["description"], entry)
            summary = rich_text(entry["summary"], entry)
            if entry.get("authors"):
                summary += "<br>" + html.escape(", ".join(entry["authors"]))
            if publication := entry.get("publication"):
                summary += (
                    f'<br><i>{html.escape(publication["venue"])}</i>, '
                    f'{publication["year"]}.'
                )
            if entry.get("related_entries"):
                links = []
                for related_id in entry["related_entries"]:
                    related = entries[related_id]
                    links.append(
                        rich_text([{"resource": related["primary_resource"]}], related)
                    )
                summary += '<br><span class="highlight">RELATED</span>: '
                summary += " | ".join(links)
            parts.extend(
                [
                    f"<details{css}>",
                    f"<summary>{summary}</summary>",
                    f'<div class="abstract">{description}</div>',
                    "</details>",
                ]
            )
        parts.append("</details>")
        return "\n".join(parts)

    if template.count("{{catalog}}") != 1:
        raise ValueError("Template must contain exactly one catalog placeholder")
    return template.replace("{{catalog}}", "\n".join(map(section, catalog["sections"])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = load_catalog()
    generated = render(catalog, (ROOT / "research/page.template.html").read_text())
    destination = ROOT / "research/index.html"
    if args.check:
        if destination.read_text() != generated:
            parser.exit(
                1, "Run python .github/scripts/research_catalog.py to regenerate.\n"
            )
        print("Research catalog and generated page are current.")
    else:
        destination.write_text(generated)


if __name__ == "__main__":
    main()
