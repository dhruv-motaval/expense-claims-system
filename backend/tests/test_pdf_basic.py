def test_pdf_helpers():
    from app import services
    assert services._is_pdf(b'%PDF-1.4', 'test.pdf')
    assert not services._is_pdf(b'notpdf', 'test.jpg')
