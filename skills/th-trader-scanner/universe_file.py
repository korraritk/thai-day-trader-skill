"""Read the SET listed-companies file the user downloads from set.or.th in a browser.

SET has served it both as a real Excel file and as an HTML table with an .xls name, so the
format is found from the content, not the extension. Columns are found by header name,
as CrabTrade777's ListedCompaniesParser does. Standard library only.
"""
import csv
import html
import io
import re
import zipfile
import xml.etree.ElementTree as ET

HEADER_KEYS = ("symbol", "company", "market")
# The Thai-language download (listedCompanies_th_TH.xls) names the same columns in Thai.
THAI_HEADERS = {"หลักทรัพย์": "symbol", "บริษัท": "company", "ตลาด": "market"}
BIFF_MAGIC = bytes.fromhex("D0CF11E0")
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
WARRANT_RE = re.compile(r"-W\d*$")
DW_RE = re.compile(r"^[A-Z0-9]+\d{2}[CP]\d{4}[A-Z]$")
XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class UniverseError(Exception):
    """The listed-companies file cannot be read: say why in one plain line."""


def is_common_share(symbol):
    """False for warrants (-W, -W1...), foreign-board lines (-F) and derivative warrants."""
    s = symbol.upper()
    return not (WARRANT_RE.search(s) or s.endswith("-F") or DW_RE.match(s))


def read_listed_companies(path):
    """-> [(symbol, market)] of common shares, market "SET" or "mai", in file order."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data.startswith(BIFF_MAGIC):
        raise UniverseError("This is an old binary .xls. Open it in Excel and save it as .xlsx or .csv, "
                            "then run universe again.")
    if data.startswith(b"PK"):
        rows = _xlsx_rows(data)
    elif b"<tr" in data[:4096].lower():
        rows = _html_rows(_decode_html(data))
    else:
        rows = _csv_rows(data)
    return _companies(rows)


def _companies(rows):
    columns = None
    out, seen = [], set()
    for row in rows:
        if columns is None:
            columns = _header(row)
            continue
        symbol = _cell(row, columns, "symbol").strip().upper()
        if not symbol or not _cell(row, columns, "company").strip():
            continue
        market = "mai" if _cell(row, columns, "market").strip().lower() == "mai" else "SET"
        if symbol in seen or not is_common_share(symbol):
            continue
        seen.add(symbol)
        out.append((symbol, market))
    if columns is None:
        raise UniverseError("No header row with Symbol and Company columns in the file.")
    return out


def _header(row):
    columns = {}
    for i, cell in enumerate(row):
        h = cell.strip().lower()
        h = THAI_HEADERS.get(h, h)
        for key in HEADER_KEYS:
            if h.startswith(key) and key not in columns:
                columns[key] = i
    return columns if "symbol" in columns and "company" in columns else None


def _cell(row, columns, key):
    i = columns.get(key)
    return row[i] if i is not None and i < len(row) else ""


def _decode_html(data):
    """UTF-8 when it is valid, else cp874: SET's Thai-language download is cp874."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp874", "replace")


def _html_rows(text):
    for tr in ROW_RE.findall(text):
        yield [html.unescape(TAG_RE.sub("", c)).strip() for c in CELL_RE.findall(tr)]


def _csv_rows(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp874")
    return list(csv.reader(io.StringIO(text)))


def _xlsx_rows(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(XLSX_NS + "si"):
                strings.append("".join(t.text or "" for t in si.iter(XLSX_NS + "t")))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise UniverseError("Cannot read the .xlsx file: %s" % e)
    rows = []
    for r in sheet.iter(XLSX_NS + "row"):
        cells = {}
        for c in r.iter(XLSX_NS + "c"):
            col = _column_index(c.get("r", ""))
            v = c.find(XLSX_NS + "v")
            if c.get("t") == "s" and v is not None:
                value = strings[int(v.text)]
            elif c.get("t") == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(XLSX_NS + "t"))
            else:
                value = v.text if v is not None and v.text else ""
            cells[col if col is not None else len(cells)] = value
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows


def _column_index(ref):
    letters = re.match(r"[A-Z]+", ref or "")
    if not letters:
        return None
    n = 0
    for ch in letters.group(0):
        n = n * 26 + (ord(ch) - 64)
    return n - 1
