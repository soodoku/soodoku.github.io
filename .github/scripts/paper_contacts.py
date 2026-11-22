"""Remove the site author's contact addresses from hosted manuscript copies."""

import re
from urllib.parse import unquote

import pymupdf

ADDRESSES = ("gsood07@gmail.com", "contact@gsood.com", "gsood@princeton.edu")
CONTACT = re.compile("|".join(map(re.escape, ADDRESSES)), re.IGNORECASE)


def clean_pdf(data):
    """Preserve clean files byte-for-byte so scheduled syncs stay idempotent."""
    if not data.startswith(b"%PDF-"):
        raise ValueError("Expected PDF data")
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise ValueError("Unreadable PDF") from exc
    with document as doc:
        changed = False
        for page in doc:
            for link in page.get_links():
                if CONTACT.search(unquote(link.get("uri") or link.get("file", ""))):
                    page.delete_link(link)
                    changed = True
            matches = [rect for addr in ADDRESSES for rect in page.search_for(addr)]
            if not matches:
                continue
            # MuPDF also removes any link crossing a redaction. Temporarily detach
            # existing annotations so a citation or coauthor link keeps its target.
            annotations = page.annot_xrefs()
            doc.xref_set_key(page.xref, "Annots", "[]")
            page = doc.reload_page(page)
            for rect in matches:
                # A narrow band through the glyphs avoids touching adjacent lines.
                inset = rect.height * 0.42
                band = pymupdf.Rect(
                    rect.x0 + 0.1, rect.y0 + inset, rect.x1 - 0.1, rect.y1 - inset
                )
                page.add_redact_annot(band, fill=False, cross_out=False)
            page.apply_redactions(images=0, graphics=0)
            refs = " ".join(f"{xref} 0 R" for xref, _, _ in annotations)
            doc.xref_set_key(page.xref, "Annots", f"[{refs}]")
            changed = True
        meta = doc.metadata
        if any(CONTACT.search(v) for v in meta.values() if isinstance(v, str)):
            doc.set_metadata(
                {
                    k: CONTACT.sub("", v)
                    for k, v in meta.items()
                    if k not in {"format", "encryption"} and isinstance(v, str)
                }
            )
            changed = True
        xml = doc.get_xml_metadata()
        if CONTACT.search(xml):
            doc.set_xml_metadata(CONTACT.sub("", xml))
            changed = True
        return doc.tobytes(garbage=4, deflate=True, no_new_id=True) if changed else data
