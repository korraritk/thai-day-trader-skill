"""Build the small listed-companies fixtures. Run once by hand."""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS = [
    ("Symbol", "Company", "Market"),
    ("PTT", "PTT PUBLIC COMPANY LIMITED", "SET"),
    ("AU", "บริษัท อาฟเตอร์ ยู จำกัด (มหาชน)", "mai"),
    ("L&E", "LIGHTING & EQUIPMENT", "SET"),
    ("PTT-W1", "PTT WARRANT", "SET"),
]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")


def html():
    cells = lambda r, tag: "".join("<%s>%s</%s>" % (tag, esc(c), tag) for c in r)
    body = "<tr><td colspan=\"3\"><b>List of Listed Companies</b></td></tr>"
    body += "<tr>%s</tr>" % cells(ROWS[0], "th")
    body += "".join("<tr>%s</tr>" % cells(r, "td") for r in ROWS[1:])
    with open(os.path.join(HERE, "listed.html.xls"), "w", encoding="utf-8") as fh:
        fh.write("<html><body><table>%s</table></body></html>" % body)


def html_th():
    """The Thai-language download (listedCompanies_th_TH.xls): Thai headers, cp874 bytes."""
    head = ("หลักทรัพย์", "บริษัท", "ตลาด")
    cells = lambda r, tag: "".join("<%s>%s</%s>" % (tag, esc(c), tag) for c in r)
    body = "<tr><td colspan=\"3\"><b>รายชื่อบริษัทจดทะเบียน</b></td></tr>"
    body += "<tr>%s</tr>" % cells(head, "td")
    body += "".join("<tr>%s</tr>" % cells(r, "td") for r in ROWS[1:])
    with open(os.path.join(HERE, "listed_th_TH.html.xls"), "w", encoding="cp874") as fh:
        fh.write("<html><body><table>%s</table></body></html>" % body)


def xlsx():
    strings = sorted({c for r in ROWS for c in r})
    idx = {s: i for i, s in enumerate(strings)}
    sst = "".join("<si><t>%s</t></si>" % esc(s) for s in strings)
    rows = ""
    for n, r in enumerate(ROWS, start=1):
        cells = "".join('<c r="%s%d" t="s"><v>%d</v></c>' % ("ABC"[k], n, idx[c]) for k, c in enumerate(r))
        rows += '<row r="%d">%s</row>' % (n, cells)
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(os.path.join(HERE, "listed.xlsx"), "w") as z:
        z.writestr("xl/sharedStrings.xml", '<?xml version="1.0" encoding="UTF-8"?><sst %s>%s</sst>' % (ns, sst))
        z.writestr("xl/worksheets/sheet1.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><worksheet %s><sheetData>%s</sheetData></worksheet>' % (ns, rows))


def csv(name, encoding):
    text = "\r\n".join(",".join('"%s"' % c for c in r) for r in ROWS) + "\r\n"
    with open(os.path.join(HERE, name), "w", encoding=encoding, newline="") as fh:
        fh.write(text)


def biff():
    with open(os.path.join(HERE, "listed_biff.xls"), "wb") as fh:
        fh.write(bytes.fromhex("D0CF11E0A1B11AE1") + bytes(504))


html()
html_th()
xlsx()
csv("listed_cp874.csv", "cp874")
csv("listed_utf8bom.csv", "utf-8-sig")
biff()
print(sorted(os.listdir(HERE)))
