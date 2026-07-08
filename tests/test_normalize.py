from ingestion.normalize import normalize_filing


def test_splits_on_item_headers():
    html = """
    <html><body>
    <p>Item 1. Business</p>
    <p>Some business description text that is long enough to pass the minimum length filter easily.</p>
    <p>Item 1A. Risk Factors</p>
    <p>Risk factor description text that is also long enough to pass the minimum length filter easily.</p>
    </body></html>
    """
    doc = normalize_filing(html, form_type="10-K", accession_number="0000000000-00-000000")
    names = [s.name for s in doc.sections]
    assert len(doc.sections) == 2
    assert any(n.startswith("Item 1.") for n in names)
    assert any(n.startswith("Item 1A.") for n in names)


def test_falls_back_to_full_text_when_no_item_headers():
    html = "<html><body><p>Just a short filing with no item structure at all here.</p></body></html>"
    doc = normalize_filing(html, form_type="8-K", accession_number="acc")
    assert len(doc.sections) == 1
    assert doc.sections[0].name == "Full Text"


def test_drops_near_empty_sections():
    html = """
    <html><body>
    <p>Item 3.</p>
    <p>None.</p>
    <p>Item 4. Mine Safety Disclosures</p>
    <p>Not applicable to this registrant because it is not a mining company at all, long enough text here.</p>
    </body></html>
    """
    doc = normalize_filing(html, form_type="10-Q", accession_number="acc2")
    assert len(doc.sections) == 1
    assert doc.sections[0].name.startswith("Item 4")


def test_section_indices_are_sequential_after_drops():
    html = """
    <html><body>
    <p>Item 1.</p>
    <p>x</p>
    <p>Item 2. Real Section</p>
    <p>Enough real content in this section to clear the minimum character threshold for keeping it.</p>
    </body></html>
    """
    doc = normalize_filing(html, form_type="10-Q", accession_number="acc3")
    assert [s.index for s in doc.sections] == list(range(len(doc.sections)))
