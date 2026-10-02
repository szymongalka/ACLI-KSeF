import json
import subprocess
from pathlib import Path

from lxml import etree

from asef.invoice import NS, build_invoice, validate_xml
from asef.preview import preview_data, render_html, render_pdf


def test_partial_payment_and_installment_context_reach_both_previews(tmp_path):
    payload = json.loads((Path(__file__).parent.parent / 'examples/faktura.json').read_text())
    root = etree.fromstring(build_invoice(payload))
    tag = lambda name: f'{{{NS}}}{name}'
    fa = root.find(tag('Fa'))
    payment = fa.find(tag('Platnosc'))
    for child in list(payment):
        payment.remove(child)
    etree.SubElement(payment, tag('ZnacznikZaplatyCzesciowej')).text = '1'
    partial = etree.SubElement(payment, tag('ZaplataCzesciowa'))
    for key, value in [('KwotaZaplatyCzesciowej', '1.23'), ('DataZaplatyCzesciowej', '2026-09-30'), ('FormaPlatnosci', '1')]:
        etree.SubElement(partial, tag(key)).text = value
    etree.SubElement(payment, tag('FormaPlatnosci')).text = '5'
    description = etree.Element(tag('DodatkowyOpis'))
    etree.SubElement(description, tag('Klucz')).text = 'Raty'
    etree.SubElement(description, tag('Wartosc')).text = 'Spłata według harmonogramu <umowy> & warunków.'
    fa.insert(list(fa).index(fa.find(tag('FaWiersz'))), description)
    contract = etree.SubElement(etree.SubElement(fa, tag('WarunkiTransakcji')), tag('Umowy'))
    etree.SubElement(contract, tag('DataUmowy')).text = '2026-09-24'
    etree.SubElement(contract, tag('NrUmowy')).text = 'TEST/2026/123'
    xml = etree.tostring(root)
    validate_xml(xml)
    data = preview_data(xml)
    assert data['payment']['partial'][0]['method'] == 'Gotówka'
    assert data['payment']['method'] == 'Kredyt'
    assert data['payment']['due_date'] == ''
    html = render_html(xml)
    assert '&lt;umowy&gt; &amp;' in html and 'TEST/2026/123' in html
    pdf = tmp_path / 'preview.pdf'
    pdf.write_bytes(render_pdf(xml))
    text = subprocess.check_output(['pdftotext', '-raw', str(pdf), '-'], text=True)
    for expected in ['1.23 PLN', '2026-09-30', 'Gotówka', 'Kredyt', 'TEST/2026/123', '<umowy> &']:
        assert expected in text
