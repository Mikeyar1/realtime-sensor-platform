import xml.etree.ElementTree as ET


def parse_stream_info(info) -> dict:
    return {
        "name":           info.name(),
        "stream_type":    info.type(),
        "channel_count":  info.channel_count(),
        "sample_rate":    info.nominal_srate(),
        "channel_format": info.channel_format(),
        "source_id":      info.source_id(),
        "channels":       _parse_channels(info),
    }


def _parse_channels(info) -> list[dict]:
    channel_count = info.channel_count()

    try:
        xml_str = info.as_xml()
        root = ET.fromstring(xml_str)

        # Channel entries: <info><desc><channels><channel>
        channel_nodes = root.findall(".//desc/channels/channel")

        if channel_nodes:
            return [
                {
                    "label": _text(ch, "label") or f"ch_{i}",
                    "unit":  _text(ch, "unit")  or "",
                    "type":  _text(ch, "type")  or "",
                }
                for i, ch in enumerate(channel_nodes)
            ]

    except ET.ParseError:
        pass

    # Fallback — generic channel dicts if XML is missing or malformed
    return [{"label": f"ch_{i}", "unit": "", "type": ""} for i in range(channel_count)]


def _text(element, tag: str) -> str:
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""
