"""Validate the research catalog and generate the checked-in research pages."""

import argparse
import html
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

from jsonschema import Draft202012Validator
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[2]
SITE_URL = "https://gsood.com/"
RESEARCH_URL = "https://gsood.com/research/"
SCHEMA_TYPES = {
    "paper": "ScholarlyArticle",
    "software": "SoftwareSourceCode",
    "dataset": "Dataset",
    "analysis": "CreativeWork",
    "blog": "BlogPosting",
}


SOFTWARE_ICONS = {
    "action": ("⚙️", "GitHub Action"),
    "app": ("📱", "Application"),
    "package": ("📦", "Package"),
    "script": ("📜", "Scripts and notebooks"),
    "theme": ("🎨", "Theme"),
}


def load_catalog(root=ROOT):
    catalog = json.loads((root / "research/catalog.json").read_text())
    schema = json.loads((root / "research/catalog.schema.json").read_text())
    Draft202012Validator(
        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    ).validate(catalog)
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
    manual = set()
    local_pdfs = set()

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
        if entry.get("site_author_position", 1) > 1 + len(entry.get("coauthors", [])):
            raise ValueError("Author position exceeds the number of authors")
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
        for section_block in entry.get("details", []):
            nodes(section_block["body"])
        if "publication_url" in entry and "publication" not in entry:
            raise ValueError(
                f"{entry['id']}: publication_url requires a publication field"
            )
        for items in entry.get("supporting", {}).values():
            for item in items:
                nodes(item)
        for resource in entry["resources"]:
            if (
                resource["id"] == entry["primary_resource"]
                and "software_type" in resource
            ):
                raise ValueError("Set the primary software type on the entry")
            if resource["id"] != entry["primary_resource"] and not resource.get(
                "label"
            ):
                raise ValueError("Secondary resources need a label")
            url = resource["url"]
            parsed = urlsplit(url)
            if parsed.scheme not in ("", "http", "https") or url.startswith("//"):
                raise ValueError(f"Unsupported resource URL: {url}")
            local_pdf = resource["kind"] == "pdf" and url.startswith("papers/")
            if local_pdf:
                local_pdfs.add(url)
            if "sync_note" in resource:
                if not local_pdf or not resource["sync_note"].strip():
                    raise ValueError("A sync note must explain a local PDF")
                if url in manual:
                    raise ValueError(f"Duplicate sync note: {url}")
                manual.add(url)
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

    if conflict := destinations & manual:
        raise ValueError(f"Conflicting source and sync note: {sorted(conflict)}")
    if uncovered := local_pdfs - destinations - manual:
        raise ValueError(f"Local PDFs need a source or sync note: {sorted(uncovered)}")


def paper_sources(catalog):
    return {
        resource["url"].removeprefix("papers/"): resource["source"]
        for entry in catalog["entries"]
        for resource in entry["resources"]
        if "source" in resource
    }


def rich_text(nodes, entry, metadata=None, url_prefix=""):
    resources = {resource["id"]: resource for resource in entry["resources"]}
    parts = []
    for node in nodes:
        if isinstance(node, str):
            parts.append(html.escape(node))
        elif "resource" in node:
            resource = resources[node["resource"]]
            primary = resource["id"] == entry["primary_resource"]
            software_type = (entry if primary else resource).get("software_type")
            if software_type:
                emoji, name = SOFTWARE_ICONS[software_type]
                parts.append(
                    f'<span class="entry-icon" role="img" aria-label="{name}" '
                    f'title="{name}">{emoji}</span>'
                )
            if (
                primary
                and metadata is not None
                and (published := entry.get("first_published"))
            ):
                parts.append(
                    f'<span hidden data-first-published="{published}" '
                    f'role="img" aria-label="First published {published}" '
                    f'title="First published {published}">🌱 </span>'
                )
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
            if primary and metadata:
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


def entry_summary(entry, entries, url_prefix=""):
    """Title line, credits, citation, resources, and related/press rows."""
    metadata = ""
    if coauthors := entry.get("coauthors"):
        metadata += f"<br>With {html.escape(credit(coauthors))}."
    if publication := entry.get("publication"):
        pub_html = rich_text(publication, entry, url_prefix=url_prefix)
        if pub_url := entry.get("publication_url"):
            href = html.escape(pub_url, quote=True)
            pub_html = f'<a href="{href}" target="_blank">{pub_html}</a>'
        metadata += "<br>" + pub_html
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
    return f"{RESEARCH_URL}{entry['id']}/"


def parse_publication(entry):
    """Extract venue, volume, number, pages, and year from entry['publication']."""
    pub = entry.get("publication", [])
    if not pub:
        return {}
    full = plain_text(pub, entry)
    years = re.findall(r"\b(19\d\d|20\d\d)\b", full)
    year = years[-1] if years else ""
    venue = ""
    for node in pub:
        if isinstance(node, dict) and node.get("tag") == "i":
            venue = plain_text(node.get("children", []), entry).strip(" .,:")
            venue = re.sub(r",?\s*(19\d\d|20\d\d)\.?$", "", venue).strip(" .,:")
            break
    info = {}
    if venue:
        info["venue"] = venue
    if year:
        info["year"] = year
    vol_issue_pages = re.search(
        r"\b(\d+)\s*\(([^)]+)\)\s*[,:]\s*(\d+[–-]\d+|\d+)\b", full
    )
    if vol_issue_pages:
        info["volume"] = vol_issue_pages.group(1)
        info["number"] = vol_issue_pages.group(2)
        info["pages"] = vol_issue_pages.group(3).replace("–", "--")
    else:
        vol_pages = re.search(r"\b(\d+)\s*[,:]\s*(\d+[–-]\d+|\d{4,})\b", full)
        if vol_pages and vol_pages.group(1) != year:
            info["volume"] = vol_pages.group(1)
            info["pages"] = vol_pages.group(2).replace("–", "--")
        else:
            vol_only = re.search(r",\s*(\d+)\s*,\s*(?:19|20)\d\d", full)
            if vol_only:
                info["volume"] = vol_only.group(1)
    return info


def entry_authors(entry):
    authors = list(entry.get("coauthors", []))
    authors.insert(entry.get("site_author_position", 1) - 1, "Gaurav Sood")
    return authors


def extract_doi(entry):
    pub_url = entry.get("publication_url", "")
    match = re.match(r"^https?://(?:dx\.)?doi\.org/(.+)$", pub_url)
    return match.group(1) if match else ""


def format_bibtex(entry):
    pub_info = parse_publication(entry)
    venue = pub_info.get("venue", "")
    year = pub_info.get("year", "")
    authors = " and ".join(entry_authors(entry))
    resources = {r["id"]: r for r in entry["resources"]}
    primary = resources[entry["primary_resource"]]
    url = entry.get("publication_url") or (
        entry_page_url(entry)
        if entry["kind"] == "paper"
        else absolute_url(primary["url"])
    )
    key_word = re.sub(r"[^a-z0-9]", "", entry["id"].split("-")[0]) or "work"
    cite_key = f"sood{year or ''}{key_word}"
    entry_type = (
        "article" if venue else ("software" if entry["kind"] == "software" else "misc")
    )
    fields = [
        ("author", authors),
        ("title", "{" + entry["title"] + "}"),
    ]
    if venue:
        fields.append(("journal", venue))
    if "volume" in pub_info:
        fields.append(("volume", pub_info["volume"]))
    if "number" in pub_info:
        fields.append(("number", pub_info["number"]))
    if "pages" in pub_info:
        fields.append(("pages", pub_info["pages"]))
    if year:
        fields.append(("year", year))
    elif entry["kind"] == "paper":
        fields.append(("note", "Working paper"))
    if doi := extract_doi(entry):
        fields.append(("doi", doi))
    fields.append(("url", url))
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
    return f"@{entry_type}{{{cite_key},\n{body}\n}}"


def format_plain_citation(entry):
    authors = credit(entry_authors(entry))
    pub_info = parse_publication(entry)
    year = pub_info.get("year", "")
    year_part = f" ({year})." if year else "."
    title = entry["title"]
    pub_text = (
        " " + plain_text(entry["publication"], entry) if "publication" in entry else ""
    )
    url = entry.get("publication_url") or entry_page_url(entry)
    return f'{authors}{year_part} "{title}."{pub_text} {url}'


def render_scholar_meta(entry):
    def meta(name, value):
        escaped = html.escape(value, quote=True)
        return f'\t<meta name="{name}" content="{escaped}" />'

    pub_info = parse_publication(entry)
    venue = pub_info.get("venue", "")
    year = pub_info.get("year", "")
    tags = [meta("citation_title", entry["title"])]
    for author in entry_authors(entry):
        tags.append(meta("citation_author", author))
    if year:
        tags.append(meta("citation_publication_date", year))
    if venue:
        tags.append(meta("citation_journal_title", venue))
    if "volume" in pub_info:
        tags.append(meta("citation_volume", pub_info["volume"]))
    if "number" in pub_info:
        tags.append(meta("citation_issue", pub_info["number"]))
    if "pages" in pub_info:
        pages = pub_info["pages"].split("--")
        tags.append(meta("citation_firstpage", pages[0]))
        if len(pages) > 1:
            tags.append(meta("citation_lastpage", pages[1]))
    if doi := extract_doi(entry):
        tags.append(meta("citation_doi", doi))
    if pub_url := entry.get("publication_url"):
        tags.append(meta("citation_public_url", pub_url))
    for resource in entry["resources"]:
        if resource["kind"] == "pdf" and resource["role"] == "manuscript":
            tags.append(meta("citation_pdf_url", absolute_url(resource["url"])))
            break
    return "\n".join(tags)


def entry_schema_object(entry):
    resources = {r["id"]: r for r in entry["resources"]}
    primary_url = absolute_url(resources[entry["primary_resource"]]["url"])
    authors = []
    for name in entry_authors(entry):
        author = {"@type": "Person", "name": name}
        if name == "Gaurav Sood":
            author["url"] = SITE_URL
        authors.append(author)
    page_url = entry_page_url(entry)
    work = {
        "@type": SCHEMA_TYPES.get(entry["kind"], "CreativeWork"),
        "@id": page_url,
        "name": entry["title"],
        "headline": entry["title"],
        "url": page_url,
        "author": authors,
        "description": plain_text(entry["description"], entry),
    }
    same_as = [
        u for u in (primary_url, entry.get("publication_url")) if u and u != page_url
    ]
    if len(same_as) == 1:
        work["sameAs"] = same_as[0]
    elif len(same_as) > 1:
        work["sameAs"] = same_as
    code_repos = [
        absolute_url(r["url"])
        for r in entry["resources"]
        if r["role"] in ("replication", "code")
    ]
    if len(code_repos) == 1:
        work["codeRepository"] = code_repos[0]
    elif len(code_repos) > 1:
        work["codeRepository"] = code_repos
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


def catalog_heading(title):
    minor_words = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "via",
        "with",
    }
    words = title.split()
    rendered = []
    for i, word in enumerate(words):
        label = html.escape(word)
        if word[0].islower() and any(c.isupper() for c in word):
            label = f'<span class="keep-case">{label}</span>'
        elif (
            0 < i < len(words) - 1
            and not word.isupper()
            and word.lower() in minor_words
        ):
            label = f'<span class="minor-word">{label}</span>'
        rendered.append(label)
    return '<span class="catalog-heading">' + " ".join(rendered) + "</span>"


def program_breadcrumb(section, *, current=False):
    title = html.escape(section["title"])
    program = (
        f'<span aria-current="page">{title}</span>'
        if current
        else f'<a href="/research/{section["id"]}/">{title}</a>'
    )
    return (
        '<nav class="program-breadcrumb" aria-label="Breadcrumb">'
        '<a href="/research/">Research</a> <span aria-hidden="true">›</span> '
        f"{program}</nav>"
    )


def render_program(section, catalog, template, study_nav):
    entries = {entry["id"]: entry for entry in catalog["entries"]}
    groups = {}

    def collect(children):
        for child in children:
            if "entry" in child:
                entry = entries[child["entry"]]
                groups.setdefault(child.get("study_group", ""), {})[entry["id"]] = entry
            else:
                collect(child["children"])

    collect(section["children"])
    blocks = []
    for group, studies in groups.items():
        if group:
            blocks.append(f"      <h3>{html.escape(group)}</h3>")
        blocks.append("      <ul>")
        for entry in studies.values():
            title = html.escape(entry["title"])
            blocks.append(f'        <li><a href="../{entry["id"]}/">{title}</a></li>')
        blocks.append("      </ul>")
    program = section["program_page"]
    title = html.escape(section["title"])
    overview = [f'<p>{html.escape(program["description"])}</p>']
    if program.get("body"):
        overview.append(MarkdownIt().render("\n\n".join(program["body"])).rstrip())
    return fill(
        template,
        page_title=title,
        heading=title,
        canonical=absolute_url(section["id"] + "/"),
        description=html.escape(program["description"], quote=True),
        overview="\n".join(overview),
        list_title=html.escape(program.get("list_title", "Studies")),
        study_nav=study_nav,
        breadcrumb=program_breadcrumb(section, current=True),
        study_list="\n".join(blocks),
    )


def render_entry_pages(catalog, template):
    study_nav = (ROOT / "research/study-nav.html").read_text().rstrip()
    entries = {entry["id"]: entry for entry in catalog["entries"]}
    pages = {}
    for top, subsection, entry_id in placements(catalog):
        entry = entries[entry_id]
        breadcrumb = catalog_heading(top["title"])
        if subsection:
            breadcrumb += " › " + " › ".join(
                map(catalog_heading, subsection.split(" › "))
            )
        if top.get("program_page"):
            breadcrumb = program_breadcrumb(top)
        else:
            breadcrumb = (
                f'<p class="entry-breadcrumb"><a href="/research/#{top["id"]}">'
                f"&larr; {breadcrumb}</a></p>"
            )
        canonical = entry_page_url(entry)
        desc = plain_text(entry["description"], entry)
        schema_obj = {"@context": "https://schema.org", **entry_schema_object(entry)}
        summary_html = entry_summary(entry, entries, url_prefix="/research/")
        abstract_html = rich_text(entry["description"], entry, url_prefix="/research/")
        details_blocks = []
        for section_block in entry.get("details", []):
            heading_html = catalog_heading(section_block["heading"])
            section_body = rich_text(
                section_block["body"], entry, url_prefix="/research/"
            )
            details_blocks.append(
                '      <section class="entry-section">\n'
                f"        <h2>{heading_html}</h2>\n"
                f'        <div class="entry-section-body">{section_body}</div>\n'
                "      </section>"
            )
        bibtex = html.escape(format_bibtex(entry))
        plain_cite = html.escape(format_plain_citation(entry))
        cite_html = (
            '      <section class="citation-section">\n'
            '        <p class="citation-text">'
            '<span class="supporting-label">Cite:</span> '
            f"{plain_cite}</p>\n"
            '        <div class="bibtex-header">'
            '<span class="supporting-label">BibTeX:</span> '
            '<button type="button" class="copy-btn">Copy</button></div>\n'
            f'        <pre class="bibtex-block"><code>{bibtex}</code></pre>\n'
            "      </section>"
        )
        body_parts = [
            f'      <div class="entry-meta">{summary_html}</div>',
            f'      <div class="abstract">{abstract_html}</div>',
            *details_blocks,
            cite_html,
        ]
        body = "\n".join(body_parts)
        pages[f"research/{entry['id']}/index.html"] = fill(
            template,
            title=html.escape(entry["title"]),
            meta_description=html.escape(desc, quote=True),
            canonical_url=html.escape(canonical, quote=True),
            meta_tags=render_scholar_meta(entry),
            schema=json.dumps(schema_obj, ensure_ascii=False, indent=2),
            study_nav=study_nav,
            breadcrumb=breadcrumb,
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
            f'<summary>{catalog_heading(value["title"])}</summary>',
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
                    f'<details{entry_id}{css} data-kind="{entry["kind"]}">',
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
        where = catalog_heading(top["title"])
        if subsection:
            path = " › ".join(map(catalog_heading, subsection.split(" › ")))
            where += f' <span class="sub">› {path}</span>'
        cards.append(
            f'<article class="card" id="card-{number}" data-section="{top["id"]}"'
            f' data-kind="{entry["kind"]}" style="--hue: {hues[top["id"]]}">'
            '<div class="card-inner">'
            f'<p class="where"><span class="kind">{entry["kind"]}</span> {where}</p>'
            f'<div class="summary">{entry_summary(entry, entries)}</div>'
            f'<div class="abstract">{rich_text(entry["description"], entry)}</div>'
            '<button class="more" type="button" hidden>More</button>'
            "</div></article>"
        )
    chip_links = "\n".join(
        f'<a href="#card-{number}" data-section="{section_id}"'
        f' style="--hue: {hues[section_id]}">{catalog_heading(title)}</a>'
        for section_id, title, number in chips
    )
    return fill(
        template,
        chips=chip_links,
        cards="\n".join(cards),
        total=str(len(cards)),
    )


def render_sitemap(catalog, _template=None):
    urls = [SITE_URL, RESEARCH_URL]
    urls.extend(
        absolute_url(section["id"] + "/")
        for section in catalog["sections"]
        if section.get("program_page")
    )
    entries = {entry["id"]: entry for entry in catalog["entries"]}
    for _, _, entry_id in placements(catalog):
        page_url = entry_page_url(entries[entry_id])
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
    for section in catalog["sections"]:
        if section.get("program_page"):
            folder = f'research/{section["id"]}'
            destination = f"{folder}/index.html"
            if destination in outputs:
                raise ValueError(f"Program and entry share a URL: {destination}")
            outputs[destination] = render_program(
                section,
                catalog,
                (ROOT / "research/program.template.html").read_text(),
                (ROOT / "research/study-nav.html").read_text().rstrip(),
            )
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
