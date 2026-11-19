"""Import the three local PDF cookbooks. Requires Poppler command-line tools.

Run from the project root with python3 scripts/import_cookbooks.py.
The private catalog lives alongside the tracker DB and opens offline as well.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from macro_engine.db import default_db_path

BOOKS = [
    ("Cookbook - Michael Kory.pdf", "Michael Kory · Complete Cookbook", "kory"),
    ("The Ultimate Anabolic Cookbook.pdf", "The Ultimate Anabolic Cookbook", "greg"),
    ("The Ultimate Anabolic Cookbook (2).pdf", "The Ultimate Anabolic Cookbook 2.0", "greg2"),
]


def text(node):
    return " ".join("".join(node.itertext()).split())


def coord(node, key):
    return int(node.get(key))


def contents(nodes):
    return " ".join(text(node) for node in nodes if text(node))


def kory_entries(page):
    width = coord(page, "width")
    columns = 2 if width > 1000 else 1
    for side in range(columns):
        left, right = width * side // columns, width * (side + 1) // columns
        nodes = [n for n in page.findall("text") if left <= coord(n, "left") < right]
        title = contents(n for n in nodes if 65 < coord(n, "top") < 255 and coord(n, "height") > 40)
        if not title:
            continue
        page_no = next(text(n).replace(" ", "") for n in nodes if coord(n, "top") < 65 and text(n).replace(" ", "").isdigit())
        # In drawing order the last value under each label is the visible one.
        labels = [n for n in nodes if text(n).lower() in ("calories", "carbs", "protein", "fat")
                  and coord(n, "top") > 900]
        values = {}
        for n in nodes:
            if n.get("font") == "16" and re.fullmatch(r"[\d.]+", text(n)):
                label = min(labels, key=lambda label: abs(coord(label, "left") - coord(n, "left")))
                values[text(label).lower()] = text(n)
        if len(values) != 4:
            raise ValueError(f"Check nutrition panel on Kory page {page_no}: {values}")
        yield title.replace(" - ", "-").title(), page_no, (left, 0, right, coord(page, "height")), values


def anabolic_entries(page, edition):
    number = coord(page, "number")
    nodes = page.findall("text")
    if edition == "greg" and 77 <= number <= 85:
        # The shake section has one common method and up to three recipes per page.
        headings = sorted((n for n in nodes if n.get("font") in ("44", "47", "50", "79", "80")
                           and text(n)), key=lambda n: coord(n, "top"))
        groups = []
        for n in headings:
            y = coord(n, "top")
            if groups and y - groups[-1][-1][0] < 55:
                groups[-1].append((y, text(n)))
            else:
                groups.append([(y, text(n))])
        for j, group in enumerate(groups):
            bottom = groups[j+1][0][0] - 3 if j+1 < len(groups) else 1125
            yield " ".join(t for _, t in group), str(number), (0, group[0][0]-3, 918, bottom), {}
        return
    if edition == "greg":
        if number not in {*range(15, 17), *range(18, 21), *range(22, 27), 28, 29,
                          *range(31, 37), *range(38, 47), *range(48, 52),
                          *range(53, 63), 64, *range(66, 75)}:
            return
        title = contents(n for n in nodes if 40 < coord(n, "top") < 270 and coord(n, "left") < 580
                         and coord(n, "height") >= 30 and text(n) != "Ingredients")
    else:
        if not 17 <= number <= 185 or number in (60, 68, 69, 94, 123):
            return
        title = contents(n for n in nodes if 55 < coord(n, "top") < 245 and coord(n, "height") > 40)
        title = {92: "Open-Face Tuna Rice Cakes", 111: "Pasta Variations: Chicken & Beef"}.get(number, title)
    if title:
        yield title, str(number), (0, 0, coord(page, "width"), coord(page, "height")), {}


def published_serving(nodes):
    """Read a single column explicitly labelled per serving, never a batch or range."""
    def token(n):
        return text(n).replace(" ", "").upper()

    fields = {"CALORIES": "calories", "FAT(G)": "fat", "CARBS(G)": "carbs",
              "FIBER(G)": "fiber", "PROTEIN(G)": "protein"}
    heads = [n for n in nodes if token(n) in ("NUTRITIONPERSERVING", "PERSERVING")]
    if len(heads) != 1:
        return {}
    x, y = coord(heads[0], "left"), coord(heads[0], "top")
    values = {}
    for n in nodes:
        label = token(n)
        nx, ny = coord(n, "left"), coord(n, "top")
        if label not in fields or abs(nx-x) > 65 or not y < ny < y + 225:
            continue
        candidates = [token(v) for v in nodes if abs(coord(v, "top")-ny) <= 2
                      and nx+40 < coord(v, "left") < nx+285
                      and re.fullmatch(r"\d+(?:\.\d+)?", token(v))]
        if len(candidates) != 1 or fields[label] in values:
            return {}
        values[fields[label]] = candidates[0]
    return values if len(values) == len(fields) else {}


def extract_layout(source, page, box):
    left, top, right, bottom = box
    result = subprocess.check_output([
        "pdftotext", "-layout", "-r", "108", "-x", str(left), "-y", str(top),
        "-W", str(right-left), "-H", str(bottom-top), "-f", str(page), "-l", str(page),
        str(source), "-"])
    return result.decode().strip()


def render(source, number, box, destination):
    if destination.exists():
        return
    x, y, right, bottom = box
    # XML coordinates use 108 dpi. Render at 162 dpi for legible zoom on phones.
    crop = [round(v * 1.5) for v in (x, y, right-x, bottom-y)]
    args = ["pdftoppm", "-jpeg", "-jpegopt", "quality=87", "-r", "162", "-f", str(number),
            "-l", str(number), "-singlefile"]
    for flag, value in zip(("-x", "-y", "-W", "-H"), crop):
        args.extend([flag, str(value)])
    result = subprocess.check_output([*args, str(source)])
    if not result.startswith(b"\xff\xd8") or not result.endswith(b"\xff\xd9"):
        raise ValueError(f"Could not render {source}, page {number}")
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(result)
    temporary.replace(destination)


def import_book(source, book, edition, out, temporary):
    xml = temporary / (edition + ".xml")
    subprocess.run(["pdftohtml", "-xml", "-i", "-hidden", "-fontfullname", "-q", source, xml], check=True)
    version = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    records = []
    pages = ET.parse(xml).findall("page")
    for page in pages:
        number = coord(page, "number")
        entries = kory_entries(page) if edition == "kory" else anabolic_entries(page, edition)
        for j, (name, printed, box, macros) in enumerate(entries):
            def picture(num, region):
                key = "-".join(map(str, (num, *region)))
                filename = f"{edition}-{version}-{key}.jpg"
                render(source, num, region, out / filename)
                return filename

            pictures = [picture(number, box)]
            layout = extract_layout(source, number, box)
            selected = [n for n in page.findall("text") if box[0] <= coord(n, "left") < box[2]
                        and box[1] <= coord(n, "top") < box[3]]
            if edition == "greg" and 77 <= number <= 85:
                common = (0, 205, 918, 550)
                pictures.append(picture(77, common))
                layout += "\n\n" + extract_layout(source, 77, common)
            if edition == "greg2" and number == 83:
                pictures.append(picture(84, (0, 0, 918, 1188)))
                layout += "\n\n" + extract_layout(source, 84, (0, 0, 918, 1188))
                selected.extend(pages[83].findall("text"))
            if edition == "kory":
                # Discard the hidden template number in the PDF text layer.
                layout = re.split(r"makes\s+\d+\s+servings?", layout, flags=re.I)[0].rstrip()
                yield_text = contents(n for n in selected if n.get("font") == "13")
                layout += "\n\n" + yield_text + "\n" + (
                    "{calories} kcal, {protein} g protein, {carbs} g carbs, {fat} g fat".format(**macros))
            else:
                macros = published_serving(selected)
            records.append(dict(id=f"{edition}-{number}-{j}", book=book, title=name, page=printed,
                                pictures=pictures, macros=macros, layout=layout))
    return records


def main():
    path = default_db_path()
    out = path.parent / "cookbooks"
    out.mkdir(parents=True, exist_ok=True)
    recipes = []
    with tempfile.TemporaryDirectory() as tmp:
        for filename, book, edition in BOOKS:
            records = import_book(ROOT / "pdf" / filename, book, edition, out, Path(tmp))
            print(f"{book}: {len(records)} entries", flush=True)
            recipes.extend(records)
    template = (ROOT / "scripts" / "cookbook-library.html").read_text()
    data = json.dumps(recipes, ensure_ascii=False).replace("<", "\\u003c")
    document = template.replace("/*COOKBOOK_DATA*/[]", data)
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS cookbook_entries
                    (id TEXT PRIMARY KEY, book TEXT NOT NULL, title TEXT NOT NULL,
                     page TEXT NOT NULL, record_json TEXT NOT NULL)""")
        con.executemany("""INSERT INTO cookbook_entries VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET book=excluded.book, title=excluded.title,
                        page=excluded.page, record_json=excluded.record_json""",
                        ((r["id"], r["book"], r["title"], r["page"], json.dumps(r)) for r in recipes))
    with tempfile.NamedTemporaryFile("w", dir=out, delete=False, encoding="utf-8") as staged:
        staged.write(document)
    os.replace(staged.name, out / "index.html")
    print(out / "index.html")


if __name__ == "__main__":
    main()
