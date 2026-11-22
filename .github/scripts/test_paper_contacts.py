"""Check public manuscript redaction independently of the sync downloader."""

import unittest

import pymupdf
from paper_contacts import ADDRESSES, clean_pdf


class ContactTests(unittest.TestCase):
    def test_redact_only_the_site_author_and_preserve_the_other_link(self):
        address = ADDRESSES[0]
        coauthor = "coauthor@example.org"
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), f"A. Author {address}  B. Author {coauthor}")
            for email in (address, coauthor):
                page.insert_link(
                    dict(
                        kind=pymupdf.LINK_URI,
                        uri="mailto:" + email,
                        **{"from": page.search_for(email)[0]},
                    )
                )
            doc.set_metadata({"subject": f"{address} {coauthor}"})
            data = doc.tobytes()
        public = clean_pdf(data)
        self.assertEqual(clean_pdf(data), public)
        self.assertEqual(clean_pdf(public), public)
        with pymupdf.open(stream=public) as doc:
            self.assertNotIn(address, doc[0].get_text())
            self.assertIn(coauthor, doc[0].get_text())
            self.assertIn("A. Author", doc[0].get_text())
            self.assertEqual(doc.metadata["subject"].strip(), coauthor)
            self.assertEqual(
                [x["uri"] for x in doc[0].get_links()], ["mailto:" + coauthor]
            )
