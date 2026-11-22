"""Validate the research catalog and generate the checked-in research pages."""

import argparse
import html
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SITE_URL = "https://gsood.com/"
RESEARCH_URL = "https://gsood.com/research/"
RESERVED_SLUGS = {"papers", "press", "replication"}
SCHEMA_TYPES = {
    "paper": "ScholarlyArticle",
    "software": "SoftwareSourceCode",
    "dataset": "Dataset",
    "project": "CreativeWork",
    "blog": "BlogPosting",
}


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
    slugs = set()
    for entry in catalog["entries"]:
        if slug := entry.get("slug"):
            if slug in RESERVED_SLUGS:
                raise ValueError(f"Reserved entry slug: {slug}")
            if slug in slugs:
                raise ValueError(f"Duplicate entry slug: {slug}")
            slugs.add(slug)
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
        if entry.get("status") == "draft" and "publication" in entry:
            raise ValueError(f"{entry['id']}: a draft cannot have a publication")
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


def rich_text(nodes, entry, metadata="", url_prefix=""):
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
            url = resource["url"]
            if url_prefix and not urlsplit(url).scheme and not url.startswith("/"):
                url = url_prefix + url
            parts.append(
                f'<a href="{html.escape(url, quote=True)}"'
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
                parts.append(
                    rich_text(node.get("children", []), entry, metadata, url_prefix)
                )
                parts.append(f"</{tag}>")
    return "".join(parts)


def credit(coauthors):
    if len(coauthors) == 1:
        return coauthors[0]
    if len(coauthors) == 2:
        return " and ".join(coauthors)
    return ", ".join(coauthors[:-1]) + ", and " + coauthors[-1]


def entry_summary(entry, entries, url_prefix="", include_page_link=True):
    """Title line, credits, citation, resources, and related/press rows."""
    metadata = ""
    if entry.get("status") == "draft":
        metadata += ' <span class="status">Draft</span>'
    if include_page_link and (slug := entry.get("slug")):
        href = html.escape(f"{url_prefix}{slug}/", quote=True)
        metadata += f' <a href="{href}" class="entry-page-link">[page]</a>'
    if coauthors := entry.get("coauthors"):
        metadata += f"<br>With {html.escape(credit(coauthors))}."
    if publication := entry.get("publication"):
        metadata += "<br>" + rich_text(publication, entry, url_prefix=url_prefix)
    summary = rich_text(entry["summary"], entry, metadata, url_prefix=url_prefix)
    supporting = {
        label: [rich_text(item, entry, url_prefix=url_prefix) for item in items]
        for label, items in entry.get("supporting", {}).items()
    }
    if entry.get("related_entries"):
        links = supporting.setdefault("related", [])
        for related_id in entry["related_entries"]:
            related = entries[related_id]
            links.append(
                rich_text(
                    [{"resource": related["primary_resource"]}],
                    related,
                    url_prefix=url_prefix,
                )
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


def plain_text(nodes, entry):
    resources = {resource["id"]: resource for resource in entry["resources"]}
    parts = []
    for node in nodes:
        if isinstance(node, str):
            parts.append(node)
        elif "resource" in node:
            resource = resources[node["resource"]]
            primary = resource["id"] == entry["primary_resource"]
            parts.append(entry["title"] if primary else resource["label"])
        elif node.get("tag") == "br":
            parts.append(" ")
        else:
            parts.append(plain_text(node.get("children", []), entry))
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def absolute_url(url, base=RESEARCH_URL):
    return urljoin(base, url)


def entry_page_url(entry):
    if slug := entry.get("slug"):
        return f"{RESEARCH_URL}{slug}/"
    return f"{RESEARCH_URL}#{entry['id']}"


def entry_schema_object(entry):
    resources = {r["id"]: r for r in entry["resources"]}
    primary_url = absolute_url(resources[entry["primary_resource"]]["url"])
    authors = [{"@type": "Person", "name": "Gaurav Sood", "url": SITE_URL}] + [
        {"@type": "Person", "name": name} for name in entry.get("coauthors", [])
    ]
    page_url = entry_page_url(entry)
    work = {
        "@type": SCHEMA_TYPES.get(entry["kind"], "CreativeWork"),
        "@id": page_url,
        "name": entry["title"],
        "headline": entry["title"],
        "url": page_url if "slug" in entry else primary_url,
        "author": authors,
        "description": plain_text(entry["description"], entry),
    }
    if "slug" in entry and primary_url != page_url:
        work["sameAs"] = primary_url
    return work


def render_schema(catalog):
    entries = {entry["id"]: entry for entry in catalog["entries"]}
    items = []
    for position, (_, _, entry_id) in enumerate(placements(catalog), 1):
        entry = entries[entry_id]
        items.append(
            {
                "@type": "ListItem",
                "position": position,
                "item": entry_schema_object(entry),
            }
        )
    data = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "@id": RESEARCH_URL,
        "name": "Gaurav Sood - Data, Research, and Software",
        "url": RESEARCH_URL,
        "author": {
            "@type": "Person",
            "@id": f"{SITE_URL}#person",
            "name": "Gaurav Sood",
            "url": SITE_URL,
        },
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(items),
            "itemListElement": items,
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def render_entry_pages(catalog, template):
    entries = {entry["id"]: entry for entry in catalog["entries"]}
    pages = {}
    for top, subsection, entry_id in placements(catalog):
        entry = entries[entry_id]
        slug = entry.get("slug")
        if not slug:
            continue
        breadcrumb = top["title"]
        if subsection:
            breadcrumb += f" › {subsection}"
        canonical = entry_page_url(entry)
        desc = plain_text(entry["description"], entry)
        schema_obj = {"@context": "https://schema.org", **entry_schema_object(entry)}
        summary_html = entry_summary(
            entry, entries, url_prefix="/research/", include_page_link=False
        )
        abstract_html = rich_text(entry["description"], entry, url_prefix="/research/")
        body = (
            f'      <div class="entry-meta">{summary_html}</div>\n'
            f'      <div class="abstract">{abstract_html}</div>'
        )
        pages[f"research/{slug}/index.html"] = fill(
            template,
            title=html.escape(entry["title"]),
            meta_description=html.escape(desc, quote=True),
            canonical_url=html.escape(canonical, quote=True),
            schema=json.dumps(schema_obj, ensure_ascii=False, indent=2),
            section_id=html.escape(top["id"], quote=True),
            breadcrumb=html.escape(breadcrumb),
            entry_body=body,
        )
    return pages


def render(catalog, template):
    entries = {entry["id"]: entry for entry in catalog["entries"]}
    seen_sections = set()
    seen_entries = set()

    def section(value):
        sec_id = (
            f' id="{html.escape(value["id"], quote=True)}"'
            if value["id"] not in seen_sections
            else ""
        )
        seen_sections.add(value["id"])
        parts = [
            f'<details class="category-toggle"{sec_id}>',
            f'<summary>{html.escape(value["title"])}</summary>',
        ]
        for child in value["children"]:
            if "entry" not in child:
                parts.append(section(child))
                continue
            entry = entries[child["entry"]]
            entry_id = (
                f' id="{html.escape(entry["id"], quote=True)}"'
                if entry["id"] not in seen_entries
                else ""
            )
            seen_entries.add(entry["id"])
            css = f' class="{entry["class"]}"' if "class" in entry else ""
            parts.extend(
                [
                    f"<details{entry_id}{css}>",
                    f"<summary>{entry_summary(entry, entries)}</summary>",
                    f'<div class="abstract">{rich_text(entry["description"], entry)}'
                    "</div>",
                    "</details>",
                ]
            )
        parts.append("</details>")
        return "\n".join(parts)

    values = {"catalog": "\n".join(map(section, catalog["sections"]))}
    if "{{schema}}" in template:
        values["schema"] = render_schema(catalog)
    return fill(template, **values)


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


def render_sitemap(catalog, _template=None):
    urls = [SITE_URL, RESEARCH_URL]
    for entry in catalog["entries"]:
        if "slug" in entry:
            page_url = entry_page_url(entry)
            if page_url not in urls:
                urls.append(page_url)
    for entry in catalog["entries"]:
        for resource in entry["resources"]:
            url = resource["url"]
            if not urlsplit(url).scheme:
                full = absolute_url(url)
                if full not in urls:
                    urls.append(full)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url in urls:
        lines.append(f"  <url><loc>{html.escape(url)}</loc></url>")
    lines.append("</urlset>\n")
    return "\n".join(lines)


def render_llms_txt(catalog, _template=None):
    entries = {entry["id"]: entry for entry in catalog["entries"]}
    lines = [
        "# Gaurav Sood",
        "",
        (
            "> Research, open-source software, and datasets by Gaurav Sood across "
            "statistics and research methods (calibration, prediction stability, "
            "record linkage, data collection, causal inference), name-based "
            "inference, online privacy and security, meta-science, misinformation, "
            "and the political economy of India."
        ),
        "",
        f"- [Homepage]({SITE_URL})",
        f"- [Data, Research, and Software]({RESEARCH_URL})",
        "- [Goji Berries (Blog)](https://gojiberries.io/)",
    ]
    current_top = None
    current_sub = None
    for top, subsection, entry_id in placements(catalog):
        if top["id"] != current_top:
            current_top = top["id"]
            current_sub = None
            lines.extend(["", f"## {top['title']}"])
        if subsection != current_sub:
            current_sub = subsection
            if subsection:
                lines.extend(["", f"### {subsection}"])
        entry = entries[entry_id]
        resources = {r["id"]: r for r in entry["resources"]}
        primary_url = absolute_url(resources[entry["primary_resource"]]["url"])
        page_url = entry_page_url(entry)
        desc = plain_text(entry["description"], entry)
        meta = []
        if entry.get("status") == "draft":
            meta.append("Draft.")
        if coauthors := entry.get("coauthors"):
            meta.append(f"With {credit(coauthors)}.")
        if publication := entry.get("publication"):
            meta.append(plain_text(publication, entry))
        extras = [
            f"[{r['label']}]({absolute_url(r['url'])})"
            for r in entry["resources"]
            if r["id"] != entry["primary_resource"]
        ]
        prefix = (" ".join(meta) + " ") if meta else ""
        suffix = (" Links: " + ", ".join(extras) + ".") if extras else ""
        lines.append(
            f"- [{entry['title']}]({primary_url}) "
            f"([page]({page_url})): "
            f"{prefix}{desc}{suffix}"
        )
    lines.append("")
    return "\n".join(lines)


PAGES = (
    (render, "research/page.template.html", "research/index.html"),
    (render_swipe, "research/swipe.template.html", "research/swipe.html"),
    (render_sitemap, None, "sitemap.xml"),
    (render_llms_txt, None, "llms.txt"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = load_catalog()
    outputs = {}
    for renderer, template, output in PAGES:
        template_text = (ROOT / template).read_text() if template else None
        outputs[output] = renderer(catalog, template_text)
    entry_template_path = ROOT / "research/entry.template.html"
    if entry_template_path.is_file():
        outputs.update(render_entry_pages(catalog, entry_template_path.read_text()))
    for output, generated in outputs.items():
        destination = ROOT / output
        if not args.check:
            destination.parent.mkdir(parents=True, exist_ok=True)
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
