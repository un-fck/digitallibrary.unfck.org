# docs: 
# - https://digitallibrary.un.org/help/admin/oaiharvest-admin-guide
# - https://www.openarchives.org/OAI/openarchivesprotocol.html#HTTPRequestFormat
# - https://digitallibrary.un.org/oai2d?verb=Identify
# - https://digitallibrary.un.org/oai2d?verb=ListMetadataFormats

from io import BytesIO
import requests
from pymarc import parse_xml_to_array
import xml.etree.ElementTree as ET
import json
from pathlib import Path

def get_records(start, end, token = None):
    params={"verb": "ListRecords"}
    if token:
        params["resumptionToken"] = token
    else:
        params["metadataPrefix"] = "marcxml" # marcxml, oai_dc, oai_openaire
        params["from"] = start
        params["until"] = end
    res = requests.get("https://digitallibrary.un.org/oai2d", params)
    records = parse_xml_to_array(BytesIO(res.content), strict=True)
    token_el = ET.fromstring(res.text).find(".//oai:resumptionToken", {"oai": "http://www.openarchives.org/OAI/2.0/"})
    if token_el is None or not token_el.text:
        return records
    token = token_el.text.strip()
    return records + get_records(start, end, token)

records = get_records(start="2025-01-01T00:00:00Z", end="2026-01-01T00:00:00Z")
records = [json.loads(record.as_json(indent=2)) for record in records if record]
records = [record for record in records if record["fields"]]
out = Path("records.json")
out.write_text(json.dumps(records, indent=2, ensure_ascii=False))
print(f"Saved {len(records)} to {out}")
